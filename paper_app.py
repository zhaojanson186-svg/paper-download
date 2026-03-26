import streamlit as st
import os
import requests
from Bio import Entrez
import time
import zipfile
import io
import xml.etree.ElementTree as ET
import pandas as pd
import json
import re

# Google Drive 官方库
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ==========================================
# 1. 配置区与历史记录系统
# ==========================================
Entrez.email = "your_email@example.com"
DOWNLOAD_DIR = "PDF_Downloads"
HISTORY_FILE = "download_history.json"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)

# ==========================================
# 2. Google Drive 上传引擎 (增强版)
# ==========================================
def upload_to_gdrive(local_file_path, file_name, folder_id):
    """将下载好的 PDF 闪电推送到谷歌网盘"""
    try:
        raw_key = st.secrets["GCP_KEY"]
        # 核心修复：strict=False 允许 JSON 字符串中包含真实的换行符和控制字符
        key_dict = json.loads(raw_key, strict=False) 
        
        creds = service_account.Credentials.from_service_account_info(key_dict)
        drive_service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(local_file_path, mimetype='application/pdf')
        
        file = drive_service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id'
        ).execute()
        return True, file.get('id')
    except json.JSONDecodeError as e:
        return False, f"密钥格式错误(请检查Secrets配置): {str(e)}"
    except Exception as e:
        return False, f"网盘连接报错: {type(e).__name__}"

# ==========================================
# 3. 核心抓取逻辑
# ==========================================
def search_pmc_oa(query, max_results=5):
    oa_query = f"({query}) AND open access[filter]"
    try:
        handle = Entrez.esearch(db="pmc", term=oa_query, retmax=max_results, sort="date")
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"] 
    except Exception as e:
        st.error(f"检索出错: {e}")
        return []

def sanitize_filename(text):
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

def download_pdf(pmcid, query):
    safe_query = sanitize_filename(query)
    file_name = f"{safe_query}_PMC{pmcid}.pdf"
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    api_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC{pmcid}"
    
    try:
        res_xml = requests.get(api_url, timeout=15)
        if res_xml.status_code != 200: return "API拒绝响应", None, None
        
        root = ET.fromstring(res_xml.content)
        pdf_link = None
        for link in root.findall(".//link"):
            if link.attrib.get("format") == "pdf":
                pdf_link = link.attrib.get("href")
                break
        
        if not pdf_link: return "无官方纯PDF", None, None
            
        if pdf_link.startswith("ftp://"):
            pdf_link = pdf_link.replace("ftp://", "https://")
            
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res_pdf = requests.get(pdf_link, headers=headers, timeout=30)
        
        if res_pdf.status_code == 200 and res_pdf.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_pdf.content)
            return "下载成功", file_path, file_name
        else:
            return "文件异常", None, None
            
    except Exception as e:
        return "网络异常", None, None

# ==========================================
# 4. 前端网页界面
# ==========================================
st.set_page_config(page_title="AI 智能文献直传终端", layout="centered", page_icon="☁️")

st.title("☁️ AI 智能文献直传终端")
st.markdown("全自动云端抓取，并实时同步推送到你的 Google Drive 资料库。")

history = load_history()

with st.sidebar:
    st.header("⚙️ 存储配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("*(必填，机器人会将文件传到此文件夹)*")
    
    st.markdown("---")
    st.write(f"📖 历史处理记录: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("账本已重置！")
        time.sleep(1)
        st.rerun()

query = st.text_input("输入检索关键词", value="CD3 bispecific antibody")
max_results = st.number_input("本次请求最大篇数", min_value=1, max_value=500, value=20)

if st.button("🚀 开始极速抓取并上传", type="primary"):
    if not query:
        st.warning("请输入关键词")
    elif not gdrive_folder_id:
        st.error("请在左侧栏填入 Google Drive 文件夹 ID！")
    else:
        with st.spinner("正在筛选新文献..."):
            all_pmc_ids = search_pmc_oa(query, max_results)
        
        if not all_pmc_ids:
            st.info("没有找到相关公开文献。")
        else:
            new_pmc_ids = [pid for pid in all_pmc_ids if pid not in history]
            st.info(f"📊 已跳过 {len(all_pmc_ids) - len(new_pmc_ids)} 篇历史记录，本次抓取 **{len(new_pmc_ids)}** 篇。")
            
            if len(new_pmc_ids) > 0:
                progress_bar = st.progress(0)
                status_text = st.empty()
                report_data = []
                
                for i, pmcid in enumerate(new_pmc_ids):
                    status_text.text(f"正在处理: PMC{pmcid} ({i+1}/{len(new_pmc_ids)})")
                    
                    status, local_path, file_name = download_pdf(pmcid, query)
                    upload_status = "未触发"
                    
                    if status == "下载成功":
                        # 触发网盘上传
                        is_uploaded, msg = upload_to_gdrive(local_path, file_name, gdrive_folder_id)
                        if is_uploaded:
                            upload_status = "✅ 成功推送到网盘"
                            st.success(f"☁️ {file_name} -> 已保存到网盘")
                            os.remove(local_path) # 成功后清理云端空间
                        else:
                            upload_status = f"上传报错: {msg[:30]}"
                            # 明确展示是哪个文件上传失败
                            st.error(f"❌ {file_name} 上传失败: {msg}") 
                    else:
                        st.warning(f"⚠️ PMC{pmcid} - {status}")

                    history[pmcid] = status
                    save_history(history)
                    
                    report_data.append({
                        "文献编号": f"PMC{pmcid}",
                        "抓取状态": status,
                        "网盘同步状态": upload_status
                    })
                    
                    time.sleep(1) 
                    progress_bar.progress((i + 1) / len(new_pmc_ids))
                
                status_text.text("全部任务完成！")
                st.dataframe(pd.DataFrame(report_data), use_container_width=True, hide_index=True)
