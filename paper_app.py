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
import re  # 新增：用于处理 Windows 文件名非法字符

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
# 2. 核心抓取与下载逻辑
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
    """【核心工具】清洗文件名，去除 Windows 不允许的特殊字符，将空格转为下划线"""
    # 替换 Windows 文件名非法字符为空，替换空格为下划线
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

def download_pdf(pmcid, query):
    """修改版：接受 query 参数，用于拼接文件名"""
    safe_query = sanitize_filename(query)
    # 新的文件命名格式：关键词_PMC编号.pdf
    file_name = f"{safe_query}_PMC{pmcid}.pdf"
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    
    # 注意：这里我们通过历史账本查重，所以不用担心不同关键词搜到同一篇文章重复下载
    api_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC{pmcid}"
    
    try:
        res_xml = requests.get(api_url, timeout=15)
        if res_xml.status_code != 200:
            return "API 拒绝响应"
        
        root = ET.fromstring(res_xml.content)
        pdf_link = None
        for link in root.findall(".//link"):
            if link.attrib.get("format") == "pdf":
                pdf_link = link.attrib.get("href")
                break
        
        if not pdf_link:
            return "无官方纯PDF (仅供网页版)"
            
        if pdf_link.startswith("ftp://ftp.ncbi.nlm.nih.gov"):
            pdf_link = pdf_link.replace("ftp://ftp.ncbi.nlm.nih.gov", "https://ftp.ncbi.nlm.nih.gov")
            
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res_pdf = requests.get(pdf_link, headers=headers, timeout=30)
        
        if res_pdf.status_code == 200 and res_pdf.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_pdf.content)
            return "下载成功"
        else:
            return "下载失败 (文件异常)"
            
    except ET.ParseError:
        return "数据解析失败"
    except Exception as e:
        return f"网络异常"

# ==========================================
# 3. 前端网页界面
# ==========================================
st.set_page_config(page_title="文献智能拉取终端", layout="centered", page_icon="🧬")

st.title("🧬 AI 智能文献拉取终端 (前缀命名版)")
st.markdown("文献将以 `搜索关键词_PMC编号.pdf` 的格式保存，方便本地资料库归档。")

history = load_history()

with st.sidebar:
    st.header("⚙️ 资料库管理")
    pdf_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
    st.write(f"📁 本地 PDF 数量: **{len(pdf_files)}** 篇")
    st.write(f"📖 历史处理记录: **{len(history)}** 条")
    
    if st.button("🗑️ 清空所有记录与缓存", type="secondary"):
        for f in pdf_files:
            os.remove(os.path.join(DOWNLOAD_DIR, f))
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
        st.success("账本及文献已全部重置！")
        time.sleep(1)
        st.rerun()

query = st.text_input("输入检索关键词", value="CD3 bispecific antibody")
max_results = st.number_input("本次向数据库请求的最大篇数", min_value=1, max_value=500, value=20)

if st.button("🚀 开始增量更新", type="primary"):
    if not query:
        st.warning("请输入关键词")
    else:
        with st.spinner("正在对比历史记录，筛选新文献..."):
            all_pmc_ids = search_pmc_oa(query, max_results)
        
        if not all_pmc_ids:
            st.info("没有找到相关公开文献。")
        else:
            new_pmc_ids = [pid for pid in all_pmc_ids if pid not in history]
            skipped_count = len(all_pmc_ids) - len(new_pmc_ids)
            
            st.info(f"📊 检索到 {len(all_pmc_ids)} 篇文献。已跳过 {skipped_count} 篇历史记录，本次需新抓取 **{len(new_pmc_ids)}** 篇。")
            
            if len(new_pmc_ids) == 0:
                st.success("🎉 当前资料库已经是最新状态，无需抓取！")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0
                report_data = []
                
                safe_query = sanitize_filename(query)
                
                for i, pmcid in enumerate(new_pmc_ids):
                    status_text.text(f"正在拉取新文献: {safe_query}_PMC{pmcid}.pdf ({i+1}/{len(new_pmc_ids)})")
                    
                    # 传入 query 用于命名
                    result = download_pdf(pmcid, query)
                    
                    history[pmcid] = result
                    save_history(history)
                    
                    report_data.append({
                        "本地文件名": f"{safe_query}_PMC{pmcid}.pdf" if result == "下载成功" else f"PMC{pmcid} (未下载)",
                        "抓取状态": result,
                        "直达链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                    })
                    
                    if result == "下载成功":
                        success_count += 1
                        st.success(f"✅ {safe_query}_PMC{pmcid}.pdf - 新增入库")
                    else:
                        st.warning(f"⚠️ PMC{pmcid} - {result}")
                    
                    time.sleep(1) 
                    progress_bar.progress((i + 1) / len(new_pmc_ids))
                
                status_text.text("增量抓取完成！")
                
                st.markdown("### 📈 本次新增文献明细")
                df = pd.DataFrame(report_data)
                st.dataframe(
                    df, 
                    column_config={
                        "直达链接": st.column_config.LinkColumn("点击查阅")
                    },
                    use_container_width=True,
                    hide_index=True
                )
                st.write(f"**更新总结**: 成功入库 {success_count} 篇纯 PDF，{len(new_pmc_ids)-success_count} 篇已记入无 PDF 账本，未来不再重复拉取。")

# ==========================================
# 4. 一键打包提取功能
# ==========================================
st.markdown("---")
current_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]

if current_files:
    st.success("🎉 PDF 文献已准备就绪，可以提取到本地：")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in current_files:
            file_path = os.path.join(DOWNLOAD_DIR, file)
            zip_file.write(file_path, arcname=file)
    
    st.download_button(
        label="📦 一键打包提取全部 PDF (ZIP)",
        data=zip_buffer.getvalue(),
        file_name=f"{sanitize_filename(query)}_Papers.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True
    )
