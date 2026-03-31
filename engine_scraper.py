import os
import urllib.parse
import xml.etree.ElementTree as ET
from Bio import Entrez
import json
import re

# 导入项目内定义的配置与工具
from config import ENTREZ_EMAIL, DOWNLOAD_DIR, PATENT_FETCH_DEBUG_LOG_FILE, sanitize_filename
from utils_network import requests_get_with_retry
from engine_ai import safe_truncate

Entrez.email = ENTREZ_EMAIL
_last_patent_fetch_debug = None

def fetch_pmc_metadata(pmcid):
    """抓取文献的标题和摘要用于前端展示与 AI 分析"""
    try:
        url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
        res = requests_get_with_retry(url, timeout=15)
        root = ET.fromstring(res.content)
        title_node = root.find(".//article-title")
        title = "".join(title_node.itertext()) if title_node is not None else f"PMC{pmcid}"
        abstract_node = root.find(".//abstract")
        abstract = "".join(abstract_node.itertext()) if abstract_node is not None else "无摘要"
        return title, abstract
    except Exception:
        return f"PMC{pmcid}", "摘要获取失败"

def download_pdf(pmcid, query):
    """【Plan A】尝试从 PMC 官方 OA 接口下载原始 PDF"""
    safe_query = sanitize_filename(query)
    file_name = f"{safe_query}_PMC{pmcid}.pdf"
    file_path = os.path.join(DOWNLOAD_DIR, file_name)
    api_url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id=PMC{pmcid}"
    try:
        res_xml = requests_get_with_retry(api_url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
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
        res_pdf = requests_get_with_retry(pdf_link, headers=headers, timeout=30)
        if res_pdf.status_code == 200 and res_pdf.content.startswith(b"%PDF"):
            with open(file_path, "wb") as f:
                f.write(res_pdf.content)
            return "下载成功", file_path, file_name
        return "文件异常", None, None
    except Exception:
        return "网络异常", None, None

# ==========================================
# 新增：【Plan B】网页全文抓取引擎
# ==========================================
def download_fulltext_txt(pmcid, query, download_dir):
    """当没有 PDF 时，直接抓取 NCBI XML 网页正文并洗净保存为 TXT，上传至网盘"""
    safe_query = sanitize_filename(query)
    file_name = f"{safe_query}_PMC{pmcid}_网页全文.txt"
    file_path = os.path.join(download_dir, file_name)
    
    # 获取 XML 格式的全文本数据
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
    try:
        res = requests_get_with_retry(url, timeout=20)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            
            # 1. 提取标题
            title_node = root.find(".//article-title")
            title = "".join(title_node.itertext()) if title_node is not None else f"PMC{pmcid}"
            
            # 2. 提取摘要
            abstract_node = root.find(".//abstract")
            abstract = "".join(abstract_node.itertext()) if abstract_node is not None else "无摘要内容。"
            
            # 3. 提取正文（递归提取所有段落文字）
            body_node = root.find(".//body")
            body_text = []
            if body_node is not None:
                for p in body_node.findall(".//p"):
                    text_chunk = "".join(p.itertext()).strip()
                    if text_chunk:
                        body_text.append(text_chunk)
            
            if not body_text:
                return "网页正文为空", None, None
                
            # 结构化组装文本
            full_content = f"【文献标题】: {title}\n"
            full_content += "="*50 + "\n"
            full_content += f"【核心摘要】:\n{abstract}\n\n"
            full_content += "="*50 + "\n"
            full_content += "【网页正文全文内容】:\n\n" + "\n\n".join(body_text)
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(full_content)
            
            return "备用抓取成功", file_path, file_name
        return f"HTTP {res.status_code}", None, None
    except Exception as e:
        return f"抓取报错: {str(e)[:20]}", None, None

def search_pmc_oa(query, max_results=5):
    """搜索 PMC 中的开源文献 ID 列表"""
    oa_query = f"({query}) AND open access[filter]"
    try:
        handle = Entrez.esearch(db="pmc", term=oa_query, retmax=max_results, sort="date")
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"] 
    except Exception:
        return []

def search_europe_pmc_patents(query, max_results=30):
    """调用欧洲 Europe PMC 官方接口提取生命科学专利"""
    global _last_patent_fetch_debug
    epmc_query = f'({query}) AND SRC:PAT'
    encoded_q = urllib.parse.quote(epmc_query)
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={encoded_q}&resultType=core&format=json&pageSize={max_results}"

    _last_patent_fetch_debug = {"query": query, "url": url}
    headers = {
        "User-Agent": "Mozilla/5.0 (Streamlit Antibody Research App)",
        "Accept": "application/json"
    }

    try:
        res = requests_get_with_retry(url, headers=headers, timeout=20, max_retries=3)
        _last_patent_fetch_debug["status_code"] = getattr(res, "status_code", None)
        _last_patent_fetch_debug["response_preview"] = safe_truncate(getattr(res, "text", ""), 300)

        if res.status_code != 200:
            return []

        data = res.json()
        results = data.get("resultList", {}).get("result", [])
        
        parsed_patents = []
        for p in results:
            p_num = p.get("id", "无编号")
            org_str = p.get("authorString", "未公开")
            abstract = p.get("abstractText", "无摘要").replace("\n", " ")
            clean_abstract = re.sub(r"<[^>]+>", "", abstract)
            
            if clean_abstract == "无摘要" or len(clean_abstract) < 20:
                continue

            parsed_patents.append({
                "全球公开号": p_num,
                "优先权/申请日": p.get("firstPublicationDate", p.get("pubYear", "未知")),
                "申请公司 / 拥有者": org_str,
                "专利名称": p.get("title", "未公开"),
                "核心摘要": clean_abstract[:1500],
                "直达阅读链接": f"https://europepmc.org/article/PAT/{p_num}"
            })
            
        return parsed_patents
    except Exception as e:
        import traceback
        _last_patent_fetch_debug["exception"] = traceback.format_exc()
        return []

def get_last_patent_fetch_debug():
    """获取最后一次专利抓取的调试信息"""
    global _last_patent_fetch_debug
    return _last_patent_fetch_debug or {}
