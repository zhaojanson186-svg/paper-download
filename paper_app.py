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
import urllib.parse

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
# 3. 核心抓取逻辑：文献 + Google Patents
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
            
        headers = {"User-Agent": "Mozilla/5.0"}
        res_pdf = requests.get(pdf_link, headers=headers, timeout=30)
        
        if res_pdf.status_code == 200 and res_pdf.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_pdf.content)
            return "下载成功", file_path, file_name
        else:
            return "文件异常", None, None
    except Exception:
        return "网络异常", None, None

# --- 专利模块：直连 Google Patents 底层 XHR 接口 ---
def search_google_patents(query, max_results=50):
    base_url = "https://patents.google.com/xhr/query?url="
    q_params = f"q={query}&num={max_results}"
    encoded_q = urllib.parse.quote(q_params)
    full_url = base_url + encoded_q
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    try:
        res = requests.get(full_url, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            clusters = data.get("results", {}).get("cluster", [])
            if not clusters: return []
            
            raw_patents = clusters[0].get("result", [])
            parsed_patents = []
            
            for item in raw_patents:
                p = item.get("patent", {})
                if not p: continue
                
                p_num = p.get("publication_number", "无编号")
                title = p.get("title", "未公开")
                
                # 🥇 核心修复：精准判断字符串还是列表，彻底告别字母被拆开的惨剧
                assignees = p.get("assignee", [])
                if isinstance(assignees, str):
                    org_str = assignees
                elif isinstance(assignees, list):
                    org_str = "、".join([str(a) for a in assignees])
                else:
                    org_str = ""
                
                if not org_str:
                    inventors = p.get("inventor", [])
                    if isinstance(inventors, str):
                        org_str = inventors
                    elif isinstance(inventors, list):
                        org_str = "、".join([str(a) for a in inventors])
                    else:
                        org_str = "未公开"
                
                snippet = p.get("snippet", "无摘要")
                clean_snippet = re.sub(r'<[^>]+>', '', snippet)
                pub_date = p.get("priority_date", p.get("filing_date", "未知"))
                
                parsed_patents.append({
                    "全球公开号": p_num,
                    "优先权/申请日": pub_date,
                    "申请公司 / 拥有者": org_str,
                    "专利名称": title,
                    "核心摘要": clean_snippet,
                    "直达阅读链接": f"https://patents.google.com/patent/{p_num}"
                })
            return parsed_patents
        else:
            st.error(f"⚠️ 谷歌专利防爬墙拦截 (状态码 {res.status_code})")
            return []
    except Exception as e:
        st.error(f"🚨 专利检索异常: {e}")
        return []

# ==========================================
# 4. 前端网页界面
# ==========================================
st.set_page_config(page_title="商业与学术情报终端", layout="wide", page_icon="🌐")

st.title("🌐 商业与学术全景情报终端")
st.markdown("集开源文献直传与专利雷达于一体，您的云端科研助理。")

history = load_history()

with st.sidebar:
    st.header("⚙️ 全局存储配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("*(必填，PDF和报表将存入此文件夹)*")
    
    st.markdown("---")
    st.write(f"📖 云端总账本记录数: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录 (文献+专利)", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("账本已彻底重置！下次检索将重新拉取所有历史数据。")
        time.sleep(1)
        st.rerun()

tab1, tab2 = st.tabs(["📄 核心文献全自动抓取", "💡 全球抗体专利雷达 (智能去重)"])

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
                            
                            # 记录文献历史
                            history[pmcid] = status
                            save_history(history)
                            report_data.append({"文献编号": f"PMC{pmcid}", "状态": status, "网盘同步": upload_status})
                            time.sleep(1) 
                            progress_bar.progress((i + 1) / len(new_pmc_ids))
                        status_text.text("文献任务完成！")
                        st.dataframe(pd.DataFrame(report_data), use_container_width=True)

with tab2:
    st.markdown("### 💡 核心技术壁垒与竞争对手挖掘")
    st.info("系统将直连底层数据库提取商业公司情报，自动剔除历史重复项，仅推送增量报表。")
    
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=50)
    
    if st.button("📊 生成【增量】专利报表并推送网盘", type="primary"):
        if not query_patent or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        else:
            with st.spinner("正在提取专利情报并进行去重比对..."):
                drive_service, err_msg = get_gdrive_service()
                if not drive_service:
                    st.error("网盘授权失败。")
                else:
                    patents = search_google_patents(query_patent, max_patents)
                    
                    if not patents:
                        st.warning("未能检索到相关专利。")
                    else:
                        # 🥇 核心突破：智能去重过滤逻辑
                        new_patents = []
                        for pt in patents:
                            pat_key = f"PAT_{pt['全球公开号']}"
                            if pat_key not in history:
                                new_patents.append(pt)
                        
                        if not new_patents:
                            st.info(f"🔕 本次雷达扫描了 {len(patents)} 项顶级专利，但**全部都在历史账本中**。系统已拦截重复推送！")
                        else:
                            st.write(f"✅ 扫描到 {len(patents)} 项专利，剔除已推送的，**发现 {len(new_patents)} 项新专利！** 正在生成报表...")
                            
                            df_patents = pd.DataFrame(new_patents)
                            st.dataframe(
                                df_patents, 
                                column_config={"直达阅读链接": st.column_config.LinkColumn("点击查阅原文")},
                                use_container_width=True, hide_index=True
                            )
                            
                            # 给文件名加上时间戳，防止在网盘里重名覆盖
                            timestamp = time.strftime("%m%d_%H%M")
                            safe_q_patent = sanitize_filename(query_patent)
                            csv_name = f"{safe_q_patent}_NewPatents_{timestamp}.csv"
                            csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                            df_patents.to_csv(csv_path, index=False, encoding="utf-8-sig")
                            
                            with st.spinner("正在将增量绝密报表推送到云端硬盘..."):
                                is_up, msg = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, mime_type='text/csv')
                                if is_up:
                                    st.success(f"🎉 商业情报挖掘完成！**{csv_name}** 报表已推送到网盘！")
                                    os.remove(csv_path)
                                    
                                    # 推送成功后，将这批新专利写入账本
                                    for pt in new_patents:
                                        history[f"PAT_{pt['全球公开号']}"] = "✅ 已推送报表"
                                    save_history(history)
                                else:
                                    st.error(f"报表上传失败: {msg}")
