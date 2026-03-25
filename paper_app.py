import streamlit as st
import os
import requests
from Bio import Entrez
import time
import zipfile
import io
import xml.etree.ElementTree as ET

# ==========================================
# 1. 配置区
# ==========================================
Entrez.email = "your_email@example.com" # 部署前建议换成你的邮箱

DOWNLOAD_DIR = "PDF_Downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ==========================================
# 2. 核心抓取与下载逻辑
# ==========================================
def search_pmc_oa(query, max_results=5):
    """检索 PMC 数据库公开文献"""
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
    """【官方 API 引擎】: 通过 NCBI OA Web Service 获取官方底层下载链接"""
    file_path = os.path.join(DOWNLOAD_DIR, f"PMC{pmcid}.pdf")
    
    if os.path.exists(file_path):
        return "已存在"

    # 1. 向官方 API 请求该文献的下载清单 (返回 XML)
    api_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC{pmcid}"
    try:
        res_xml = requests.get(api_url, timeout=15)
        if res_xml.status_code != 200:
            return "官方 API 拒绝响应"
        
        # 2. 解析 XML，寻找 PDF 格式的官方底层下载地址
        root = ET.fromstring(res_xml.content)
        pdf_link = None
        for link in root.findall(".//link"):
            if link.attrib.get("format") == "pdf":
                pdf_link = link.attrib.get("href")
                break
        
        if not pdf_link:
            return "官方未提供独立PDF版 (该文献可能只有网页版)"
            
        # 3. NCBI 默认提供 ftp:// 链接，云端为保证稳定，将其转换为 https://
        if pdf_link.startswith("ftp://ftp.ncbi.nlm.nih.gov"):
            pdf_link = pdf_link.replace("ftp://ftp.ncbi.nlm.nih.gov", "https://ftp.ncbi.nlm.nih.gov")
            
        # 4. 根据官方底层直链进行无阻碍下载
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res_pdf = requests.get(pdf_link, headers=headers, timeout=30)
        
        if res_pdf.status_code == 200 and res_pdf.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_pdf.content)
            return "下载成功 (官方底层接口)"
        else:
            return "下载失败 (获取到了链接，但文件拉取异常)"
            
    except ET.ParseError:
        return "官方 API 返回数据解析失败"
    except Exception as e:
        return f"网络报错: {type(e).__name__}"

# ==========================================
# 3. 前端网页界面
# ==========================================
st.set_page_config(page_title="CD3双抗 智能检索终端", layout="centered", page_icon="🧬")

st.title("🧬 AI 智能文献下载终端 (官方接口版)")
st.markdown("调用 NCBI 官方 OA 开发者接口，彻底绕过网页端验证码拦截。")

# --- 侧边栏：缓存管理 ---
with st.sidebar:
    st.header("⚙️ 云端缓存管理")
    pdf_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]
    st.write(f"当前云端暂存文献数: **{len(pdf_files)}** 篇")
    
    if st.button("🗑️ 清空云端缓存", type="secondary"):
        for f in pdf_files:
            os.remove(os.path.join(DOWNLOAD_DIR, f))
        st.success("云端空间已清理！")
        time.sleep(1)
        st.rerun()

# --- 主界面 ---
query = st.text_input("输入检索关键词 (如靶点、适应症)", value="CD3 bispecific antibody")
max_results = st.number_input("本次计划抓取篇数", min_value=1, max_value=200, value=10)

if st.button("🚀 开始云端极速抓取", type="primary"):
    if not query:
        st.warning("请输入关键词")
    else:
        with st.spinner("正在连接 NCBI 数据库进行检索..."):
            pmc_ids = search_pmc_oa(query, max_results)
        
        if not pmc_ids:
            st.info("没有找到符合条件的公开文献。")
        else:
            st.write(f"锁定 {len(pmc_ids)} 篇文献，启动官方接口下载：")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            success_count = 0
            
            for i, pmcid in enumerate(pmc_ids):
                status_text.text(f"正在拉取: PMC{pmcid} ({i+1}/{len(pmc_ids)})")
                
                result = download_pdf(pmcid)
                
                if "成功" in result:
                    st.success(f"✅ PMC{pmcid}.pdf - {result}")
                    success_count += 1
                elif result == "已存在":
                    st.info(f"⏭️ PMC{pmcid}.pdf - 云端已存在，跳过")
                else:
                    st.warning(f"⚠️ PMC{pmcid}.pdf - {result}")
                
                # 官方 API 要求请求间隔不要太短，设置1秒延迟非常安全
                time.sleep(1) 
                progress_bar.progress((i + 1) / len(pmc_ids))
            
            status_text.text("云端抓取任务完成！")
            st.write(f"**总结**: 本次成功拉取 {success_count} 篇 PDF 到云端服务器。")

# ==========================================
# 4. 一键打包提取功能
# ==========================================
st.markdown("---")
current_files = [f for f in os.listdir(DOWNLOAD_DIR) if f.endswith('.pdf')]

if current_files:
    st.success("🎉 文献已准备就绪，可以提取到本地系统：")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in current_files:
            file_path = os.path.join(DOWNLOAD_DIR, file)
            zip_file.write(file_path, arcname=file)
    
    st.download_button(
        label="📦 一键打包提取全部文献 (ZIP)",
        data=zip_buffer.getvalue(),
        file_name=f"{query.replace(' ', '_')}_Papers.zip",
        mime="application/zip",
        type="primary",
        use_container_width=True
    )
