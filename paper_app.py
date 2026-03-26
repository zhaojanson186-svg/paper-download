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

# 新增：Google 大模型 AI 库
import google.generativeai as genai

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
# 2. AI 提纯引擎 (大模型介入)
# ==========================================
def init_ai_model():
    """初始化大模型"""
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        # 使用 Flash 模型：速度极快，极其适合批量提纯数据
        model = genai.GenerativeModel('gemini-1.5-flash')
        return model
    except Exception as e:
        return None

def analyze_abstract_with_ai(model, abstract_text):
    """让 AI 像研发总监一样阅读摘要并提取结构化情报"""
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "抗体构型": "未提取", "AI一句话总结": "无有效摘要"}
    
    prompt = f"""
    作为资深抗体药物研发专家，请阅读以下专利摘要，提取关键商业与技术情报。
    请直接输出一个合法的 JSON 格式，严格包含以下 3 个键名，不要有任何 Markdown 标记或多余解释：
    {{
        "靶点组合": "提取提到的所有靶点(如 CD3, BCMA, HER2等)，若无则写'未提及'",
        "抗体构型": "提取抗体类型或技术平台(如 scFv, VHH, Bispecific, ADC, CAR-T等)，若无则写'未提及'",
        "AI一句话总结": "用15个字以内的中文高度概括其核心适应症或创新点"
    }}
    
    摘要原文：
    {abstract_text}
    """
    
    try:
        # 强制 AI 以 JSON 格式响应，保证数据结构极度整洁
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(response_mime_type="application/json")
        )
        result = json.loads(response.text)
        return result
    except Exception as e:
        return {"靶点组合": "AI解析失败", "抗体构型": "AI解析失败", "AI一句话总结": "AI解析失败"}

# ==========================================
# 3. Google Drive 上传引擎
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
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True, file.get('id')
    except Exception as e:
        return False, f"上传异常: {str(e)[:50]}"

# ==========================================
# 4. 核心抓取逻辑：文献 + Google Patents
# ==========================================
def sanitize_filename(text):
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

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
                
                assignees = p.get("assignee", [])
                if isinstance(assignees, str): org_str = assignees
                elif isinstance(assignees, list): org_str = "、".join([str(a) for a in assignees])
                else: org_str = ""
                
                if not org_str:
                    inventors = p.get("inventor", [])
                    if isinstance(inventors, str): org_str = inventors
                    elif isinstance(inventors, list): org_str = "、".join([str(a) for a in inventors])
                    else: org_str = "未公开"
                
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
# 5. 前端网页界面
# ==========================================
st.set_page_config(page_title="AI 情报提纯终端", layout="wide", page_icon="🧠")

st.title("🧠 商业与学术 AI 情报提纯终端")
st.markdown("集全自动抓取、LLM 智能提纯、去重与云端网盘直传于一体。")

history = load_history()

with st.sidebar:
    st.header("⚙️ 全局配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("---")
    
    # 检查 AI 模型状态
    ai_model = init_ai_model()
    if ai_model:
        st.success("🤖 AI 提纯引擎：已激活")
    else:
        st.error("🤖 AI 提纯引擎：离线 (请检查 GEMINI_API_KEY 配置)")

    st.markdown("---")
    st.write(f"📖 云端总账本记录数: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录 (文献+专利)", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("账本已彻底重置！")
        time.sleep(1)
        st.rerun()

tab1, tab2 = st.tabs(["📄 核心文献全自动抓取", "💡 AI 专利情报雷达 (智能提纯)"])

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
                                    os.remove(local_path)
                                else:
                                    upload_status = f"上传报错"
                            history[pmcid] = status
                            save_history(history)
                            report_data.append({"文献编号": f"PMC{pmcid}", "状态": status, "网盘同步": upload_status})
                            progress_bar.progress((i + 1) / len(new_pmc_ids))
                        status_text.text("文献任务完成！")
                        st.dataframe(pd.DataFrame(report_data), use_container_width=True)

with tab2:
    st.markdown("### 🧠 竞争对手挖掘与 AI 深度提纯")
    st.info("系统提取底层商业情报后，将交由大模型逐一阅读晦涩的专利摘要，提炼出靶点、构型及一句话结论。")
    
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=30)
    
    if st.button("📊 生成【增量提纯】专利报表并推送", type="primary"):
        if not query_patent or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        elif not ai_model:
            st.error("AI 引擎未激活！请在 Secrets 中配置 GEMINI_API_KEY。")
        else:
            with st.spinner("正在提取专利底层数据并比对历史账本..."):
                drive_service, err_msg = get_gdrive_service()
                if not drive_service:
                    st.error("网盘授权失败。")
                else:
                    patents = search_google_patents(query_patent, max_patents)
                    if not patents:
                        st.warning("未能检索到相关专利。")
                    else:
                        new_patents = [pt for pt in patents if f"PAT_{pt['全球公开号']}" not in history]
                        
                        if not new_patents:
                            st.info(f"🔕 扫描到 {len(patents)} 项专利，但均已在历史账本中，无需重复提纯推送！")
                        else:
                            st.write(f"✅ 发现 **{len(new_patents)}** 项新专利！正在调动 AI 逐篇进行深度阅读提纯...")
                            
                            ai_progress = st.progress(0)
                            ai_status = st.empty()
                            
                            # 🥇 核心突破：让大模型逐个阅读专利并注入灵魂
                            for idx, pt in enumerate(new_patents):
                                ai_status.text(f"🤖 AI 正在提纯第 {idx+1}/{len(new_patents)} 项专利: {pt['全球公开号']} ...")
                                
                                ai_insights = analyze_abstract_with_ai(ai_model, pt['核心摘要'])
                                
                                # 将 AI 提纯的结晶注入到这条数据的前排
                                pt["🎯靶点组合"] = ai_insights.get("靶点组合", "未提取")
                                pt["🧬抗体构型"] = ai_insights.get("抗体构型", "未提取")
                                pt["💡AI一句话总结"] = ai_insights.get("AI一句话总结", "未提取")
                                
                                # 为防止触发免费 API 速率限制，温柔地停顿一下
                                time.sleep(1.5)
                                ai_progress.progress((idx + 1) / len(new_patents))
                            
                            ai_status.text("🧠 AI 提纯完毕！正在生成全景报表...")
                            
                            # 调整列的顺序，把 AI 的精华放在最前面醒目的位置
                            cols_order = ["全球公开号", "申请公司 / 拥有者", "🎯靶点组合", "🧬抗体构型", "💡AI一句话总结", "优先权/申请日", "专利名称", "核心摘要", "直达阅读链接"]
                            df_patents = pd.DataFrame(new_patents)[cols_order]
                            
                            st.dataframe(
                                df_patents, 
                                column_config={"直达阅读链接": st.column_config.LinkColumn("点击查阅原文")},
                                use_container_width=True, hide_index=True
                            )
                            
                            timestamp = time.strftime("%m%d_%H%M")
                            safe_q = sanitize_filename(query_patent)
                            csv_name = f"{safe_q}_AI_Report_{timestamp}.csv"
                            csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                            df_patents.to_csv(csv_path, index=False, encoding="utf-8-sig")
                            
                            with st.spinner("正在将 AI 绝密提纯报表推送到云端硬盘..."):
                                is_up, msg = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, mime_type='text/csv')
                                if is_up:
                                    st.success(f"🎉 任务完美结束！带大模型提纯的 **{csv_name}** 已推送到你的 Google Drive！")
                                    os.remove(csv_path)
                                    for pt in new_patents:
                                        history[f"PAT_{pt['全球公开号']}"] = "✅ 已AI提纯"
                                    save_history(history)
                                else:
                                    st.error(f"报表上传失败: {msg}")
