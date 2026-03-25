import streamlit as st
import os
import requests
from Bio import Entrez
import time
import zipfile
import io
import xml.etree.ElementTree as ET
import pandas as pd

# ==========================================
# 1. 配置区
# ==========================================
Entrez.email = "your_email@example.com"

DOWNLOAD_DIR = "PDF_Downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

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
    
    if os.path.exists(file_path):
        return "云端已存在"

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
st.set_page_config(page_title="CD3双抗 智能检索终端", layout="centered", page_icon="🧬")

st.title("🧬 AI 智能文献下载终端")
st.markdown("调用 NCBI 官方接口极速下载。对于仅提供网页版的文献，自动生成追踪链接。")

with st.sidebar:
    st.header("⚙️ 云端缓存管理")
    pdf_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
    st.write(f"当前暂存文献: **{len(pdf_files)}** 篇")
    
    if st.button("🗑️ 清空云端缓存", type="secondary"):
        for f in pdf_files:
            os.remove(os.path.join(DOWNLOAD_DIR, f))
        st.success("已清理！")
        time.sleep(1)
        st.rerun()

query = st.text_input("输入检索关键词 (如靶点、适应症)", value="CD3 bispecific antibody")
max_results = st.number_input("本次计划抓取篇数", min_value=1, max_value=200, value=10)

if st.button("🚀 开始云端极速抓取", type="primary"):
    if not query:
        st.warning("请输入关键词")
    else:
        with st.spinner("正在检索数据库..."):
            pmc_ids = search_pmc_oa(query, max_results)
        
        if not pmc_ids:
            st.info("没有找到符合条件的公开文献。")
        else:
            st.write(f"锁定 {len(pmc_ids)} 篇文献，启动下载引擎：")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            success_count = 0
            
            # 用于收集报告数据的列表
            report_data = []
            
            for i, pmcid in enumerate(pmc_ids):
                status_text.text(f"正在拉取: PMC{pmcid} ({i+1}/{len(pmc_ids)})")
                
                result = download_pdf(pmcid)
                
                # 记录每一篇的抓取结果
                report_data.append({
                    "文献编号": f"PMC{pmcid}",
                    "抓取状态": result,
                    "在线阅读/补充下载直达链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                })
                
                if result == "下载成功":
                    success_count += 1
                
                time.sleep(1) 
                progress_bar.progress((i + 1) / len(pmc_ids))
            
            status_text.text("抓取任务完成！")
            
            # ==========================================
            # 新增：展示结果追踪明细表
            # ==========================================
            st.markdown("### 📊 本次抓取任务明细")
            df = pd.DataFrame(report_data)
            st.dataframe(
                df, 
                column_config={
                    "在线阅读/补充下载直达链接": st.column_config.LinkColumn("点击直达网页")
                },
                use_container_width=True,
                hide_index=True
            )
            st.write(f"**总结**: 成功入库 {success_count} 篇，{len(pmc_ids)-success_count} 篇需手动查阅网页版。")

# ==========================================
# 4. 一键打包提取功能
# ==========================================
st.markdown("---")
current_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]

if current_files:
    st.success("🎉 PDF 文献已准备就绪，可以提取到本地 Windows 系统：")
    
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
