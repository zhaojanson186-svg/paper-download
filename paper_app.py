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

# ==========================================
# 1. 配置区与历史记录系统
# ==========================================
Entrez.email = "your_email@example.com"

DOWNLOAD_DIR = "PDF_Downloads"
HISTORY_FILE = "download_history.json"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# 初始化/加载历史记录账本
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

def download_pdf(pmcid):
    file_path = os.path.join(DOWNLOAD_DIR, f"PMC{pmcid}.pdf")
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

st.title("🧬 AI 智能文献拉取终端 (增量更新版)")
st.markdown("自动过滤历史已处理文献，专为持续追踪最新学术动态设计。")

# 加载历史记录
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
            # 核心逻辑：剔除已经在账本里的 ID，只保留全新的文献
            new_pmc_ids = [pid for pid in all_pmc_ids if pid not in history]
            skipped_count = len(all_pmc_ids) - len(new_pmc_ids)
            
            st.info(f"📊 检索到 {len(all_pmc_ids)} 篇文献。已自动跳过 {skipped_count} 篇历史处理过的文献，本次需新抓取 **{len(new_pmc_ids)}** 篇。")
            
            if len(new_pmc_ids) == 0:
                st.success("🎉 当前资料库已经是最新状态，无需抓取！")
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                success_count = 0
                report_data = []
                
                for i, pmcid in enumerate(new_pmc_ids):
                    status_text.text(f"正在拉取新文献: PMC{pmcid} ({i+1}/{len(new_pmc_ids)})")
                    
                    result = download_pdf(pmcid)
                    
                    # 将结果写进内存账本，并立刻保存到本地 json
                    history[pmcid] = result
                    save_history(history)
                    
                    report_data.append({
                        "文献编号": f"PMC{pmcid}",
                        "抓取状态": result,
                        "直达链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                    })
                    
                    if result == "下载成功":
                        success_count += 1
                        st.success(f"✅ PMC{pmcid}.pdf - 新增入库")
                    else:
                        st.warning(f"⚠️ PMC{pmcid} - {result}")
                    
                    time.sleep(1) 
                    progress_bar.progress((i + 1) / len(new_pmc_ids))
                
                status_text.text("增量抓取完成！")
                
                # 展示本次新增的明细表
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
        file_name=f"{query.replace(' ', '_')}_Papers.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True
    )
