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
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

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
# 2. Google Drive 上传引擎
# ==========================================
def get_gdrive_service():
    try:
        raw_token = st.secrets["GCP_TOKEN"]
        token_dict = json.loads(raw_token, strict=False)
        creds = Credentials.from_authorized_user_info(token_dict)
        drive_service = build('drive', 'v3', credentials=creds)
        return drive_service, None
    except Exception as e:
        return None, f"Token解析失败: {str(e)}"

def upload_to_gdrive(drive_service, local_file_path, file_name, folder_id, mime_type='application/pdf'):
    try:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(local_file_path, mimetype=mime_type)
        
        file = drive_service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id'
        ).execute()
        return True, file.get('id')
    except Exception as e:
        return False, f"上传异常: {str(e)[:50]}"

# ==========================================
# 3. 核心抓取逻辑：文献 (PMC) + 专利 (USPTO)
# ==========================================
def sanitize_filename(text):
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

# --- 文献模块 ---
def search_pmc_oa(query, max_results=5):
    oa_query = f"({query}) AND open access[filter]"
    try:
        handle = Entrez.esearch(db="pmc", term=oa_query, retmax=max_results, sort="date")
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"] 
    except Exception as e:
        return []

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
    except Exception:
        return "网络异常", None, None

# --- 专利模块 (终极修复版) ---
def search_uspto_patents(query, max_results=20):
    url = "https://api.patentsview.org/patents/query"
    # 核心修复：扩大搜索范围（标题+摘要），并使用官方严格要求的 assignee_organization 字段
    payload = {
        "q": {"_or": [
            {"_text_any": {"patent_abstract": query}},
            {"_text_any": {"patent_title": query}}
        ]},
        "f": ["patent_number", "patent_title", "patent_date", "patent_abstract", "assignee_organization"],
        "o": {"per_page": max_results}
    }
    try:
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            data = res.json()
            return data.get("patents") or []
        else:
            # 加上显微镜：如果专利局报错，直接把原话打印在网页上
            st.error(f"⚠️ USPTO接口报错 (状态码 {res.status_code}): {res.text[:150]}")
            return []
    except Exception as e:
        st.error(f"🚨 专利检索连接异常: {e}")
        return []

# ==========================================
# 4. 前端网页界面 (双引擎架构)
# ==========================================
st.set_page_config(page_title="商业与学术情报终端", layout="wide", page_icon="🌐")

st.title("🌐 商业与学术全景情报终端")
st.markdown("集开源文献直传与专利雷达于一体，您的云端科研助理。")

history = load_history()

# --- 侧边栏 ---
with st.sidebar:
    st.header("⚙️ 全局存储配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("*(必填，PDF和报表将存入此文件夹)*")
    
    st.markdown("---")
    st.write(f"📖 历史文献处理记录: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("文献账本已重置！")
        time.sleep(1)
        st.rerun()

# --- 构建双标签页 ---
tab1, tab2 = st.tabs(["📄 核心文献全自动抓取", "💡 专利情报雷达 (USPTO)"])

# ========================================================
# 引擎 1：文献抓取
# ========================================================
with tab1:
    st.markdown("### 🧬 学术前沿直达")
    query_paper = st.text_input("输入检索关键词 (靶点/适应症)", value="CD3 bispecific antibody", key="q_paper")
    max_papers = st.number_input("本次请求最大篇数", min_value=1, max_value=500, value=15)
    
    if st.button("🚀 开始极速抓取文献并上传", type="primary"):
        if not query_paper or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        else:
            with st.spinner("正在初始化 Google Drive 授权通道..."):
                drive_service, err_msg = get_gdrive_service()
                
            if not drive_service:
                st.error("网盘授权失败，请检查配置。")
            else:
                with st.spinner("正在筛选新文献..."):
                    all_pmc_ids = search_pmc_oa(query_paper, max_papers)
                
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
                            status, local_path, file_name = download_pdf(pmcid, query_paper)
                            upload_status = "未触发"
                            
                            if status == "下载成功":
                                is_up, msg = upload_to_gdrive(drive_service, local_path, file_name, gdrive_folder_id)
                                if is_up:
                                    upload_status = "✅ 已保存到网盘"
                                    st.success(f"☁️ {file_name} -> {upload_status}")
                                    os.remove(local_path)
                                else:
                                    upload_status = f"上传报错"
                                    st.error(f"❌ {file_name} 上传失败: {msg}")
                            else:
                                st.warning(f"⚠️ PMC{pmcid} - {status}")

                            history[pmcid] = status
                            save_history(history)
                            report_data.append({"文献编号": f"PMC{pmcid}", "状态": status, "网盘同步": upload_status})
                            time.sleep(1) 
                            progress_bar.progress((i + 1) / len(new_pmc_ids))
                        
                        status_text.text("文献任务完成！")
                        st.dataframe(pd.DataFrame(report_data), use_container_width=True)

# ========================================================
# 引擎 2：专利情报雷达
# ========================================================
with tab2:
    st.markdown("### 💡 技术壁垒梳理与竞争情报汇总")
    st.info("系统将检索美国专利数据库，自动生成 Excel 兼容的商业情报汇总表，并直接推送到网盘。")
    
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=50)
    
    if st.button("📊 生成专利全景报表并推送网盘", type="primary"):
        if not query_patent or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        else:
            with st.spinner("正在初始化网盘连接并查询 USPTO 底层数据..."):
                drive_service, err_msg = get_gdrive_service()
                
                if not drive_service:
                    st.error("网盘授权失败。")
                else:
                    patents = search_uspto_patents(query_patent, max_patents)
                    
                    if not patents:
                        st.warning("未能检索到相关专利，请尝试更换关键词。如果是 API 报错，请查看上方红色提示。")
                    else:
                        st.write(f"✅ 成功提取 **{len(patents)}** 项相关专利，正在生成报表...")
                        
                        patent_report = []
                        for p in patents:
                            assignees = p.get("assignees", [])
                            orgs = [a.get("assignee_organization", "") for a in assignees if isinstance(a, dict) and a.get("assignee_organization")]
                            org_str = "、".join(orgs) if orgs else "未公开/个人"
                            
                            p_num = p.get("patent_number", "")
                            
                            patent_report.append({
                                "专利编号": p_num,
                                "公开日期": p.get("patent_date", ""),
                                "申请人 / 拥有公司": org_str,
                                "专利名称": p.get("patent_title", ""),
                                "核心摘要": p.get("patent_abstract", ""),
                                "直达阅读链接": f"https://patents.google.com/patent/US{p_num}"
                            })
                        
                        df_patents = pd.DataFrame(patent_report)
                        st.dataframe(
                            df_patents, 
                            column_config={"直达阅读链接": st.column_config.LinkColumn("点击查阅原文")},
                            use_container_width=True, hide_index=True
                        )
                        
                        safe_q_patent = sanitize_filename(query_patent)
                        csv_name = f"{safe_q_patent}_Patent_Report.csv"
                        csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                        df_patents.to_csv(csv_path, index=False, encoding="utf-8-sig")
                        
                        with st.spinner("正在将报表推送到云端硬盘..."):
                            is_up, msg = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, mime_type='text/csv')
                            if is_up:
                                st.success(f"🎉 任务完美结束！**{csv_name}** 报表已成功推送到你的 Google Drive。")
                                os.remove(csv_path)
                            else:
                                st.error(f"报表上传失败: {msg}")
