import streamlit as st
import os
import requests
from Bio import Entrez
import time
import zipfile
import io

# ==========================================
# 1. 配置区
# ==========================================
Entrez.email = "your_email@example.com" # 建议换成你的邮箱

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
    """【双节点容灾下载引擎】: 欧洲节点与美国节点自动切换"""
    file_path = os.path.join(DOWNLOAD_DIR, f"PMC{pmcid}.pdf")
    
    if os.path.exists(file_path):
        return "已存在"

    # 终极伪装头 (模拟真实的 Mac Chrome 浏览器行为)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }

    # ==============================
    # 路线 A：Europe PMC 节点
    # 注意：去掉了 verify=False，云端环境自带合法证书，去掉防拦截
    # ==============================
    url_europe = f"https://europepmc.org/articles/PMC{pmcid}?pdf=render"
    try:
        res_eu = requests.get(url_europe, headers=headers, timeout=15, allow_redirects=True)
        if res_eu.status_code == 200 and res_eu.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_eu.content)
            return "下载成功 (Europe PMC 节点)"
    except Exception:
        pass # 如果被防火墙切断连接，默默忽略，直接进入路线 B

    # ==============================
    # 路线 B：美国 NCBI 官方直连节点
    # 作为备用路线兜底
    # ==============================
    url_ncbi = f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/pdf/"
    try:
        res_ncbi = requests.get(url_ncbi, headers=headers, timeout=15, allow_redirects=True)
        if res_ncbi.status_code == 200 and res_ncbi.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_ncbi.content)
            return "下载成功 (NCBI 节点)"
    except Exception as e:
        # 如果双节点全被机房防火墙盾拦截，才返回错误
        return f"网络报错: {type(e).__name__} (云端 IP 被数据库安全盾拦截)"
        
    return "下载失败 (被拦截或文献本身未提供纯PDF)"

# ==========================================
# 3. 前端网页界面
# ==========================================
st.set_page_config(page_title="CD3双抗 智能检索终端", layout="centered", page_icon="🧬")

st.title("🧬 AI 智能文献下载终端 (双节点容灾版)")
st.markdown("部署于海外云端，自动在欧美学术节点间切换，智能规避反爬虫防火墙。")

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
        with st.spinner("正在连接 NCBI 数据库进行高速检索..."):
            pmc_ids = search_pmc_oa(query, max_results)
        
        if not pmc_ids:
            st.info("没有找到符合条件的公开文献。")
        else:
            st.write(f"锁定 {len(pmc_ids)} 篇文献，启动双节点下载引擎：")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            success_count = 0
            
            for i, pmcid in enumerate(pmc_ids):
                status_text.text(f"正在拉取: PMC{pmcid} ({i+1}/{len(pmc_ids)})")
                
                # 开始下载
                result = download_pdf(pmcid)
                
                # 结果展示
                if "成功" in result:
                    st.success(f"✅ PMC{pmcid}.pdf - {result}")
                    success_count += 1
                elif result == "已存在":
                    st.info(f"⏭️ PMC{pmcid}.pdf - 云端已存在，跳过")
                else:
                    st.warning(f"⚠️ PMC{pmcid}.pdf - {result}")
                
                time.sleep(0.5) 
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
