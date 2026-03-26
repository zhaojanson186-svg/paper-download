import os
import urllib.parse
import xml.etree.ElementTree as ET
from Bio import Entrez
import json
import re

from config import ENTREZ_EMAIL, DOWNLOAD_DIR, PATENT_FETCH_DEBUG_LOG_FILE, sanitize_filename
from utils_network import requests_get_with_retry
from engine_ai import safe_truncate

Entrez.email = ENTREZ_EMAIL
_last_patent_fetch_debug = None

def fetch_pmc_metadata(pmcid):
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

def search_pmc_oa(query, max_results=5):
    oa_query = f"({query}) AND open access[filter]"
    try:
        handle = Entrez.esearch(db="pmc", term=oa_query, retmax=max_results, sort="date")
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"] 
    except Exception:
        return []

import urllib.parse
import re

# 确保文件顶部有这个导入（如果之前没删的话）
from utils_network import requests_get_with_retry
from engine_ai import safe_truncate

def search_europe_pmc_patents(query, max_results=30):
    """调用欧洲 Europe PMC 官方接口提取生命科学专利，绝对免费且防拦截"""
    global _last_patent_fetch_debug
    
    # 核心语法：强制限定检索来源为专利 (SRC:PAT)
    epmc_query = f'({query}) AND SRC:PAT'
    encoded_q = urllib.parse.quote(epmc_query)
    
    # Europe PMC 官方 REST API，返回干净的 JSON
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={encoded_q}&resultType=core&format=json&pageSize={max_results}"

    _last_patent_fetch_debug = {"query": query, "url": url}

    headers = {
        # 友好的 UA，明确告诉欧洲局我们是学术型数据抓取
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
            # 获取专利号
            p_num = p.get("id", "无编号")
            
            # Europe PMC 通常将专利申请人/发明人放在 authorString 字段
            org_str = p.get("authorString", "未公开")
            
            # 清洗摘要中的 HTML 标签和换行符，让大模型吃得更舒服
            abstract = p.get("abstractText", "无摘要").replace("\n", " ")
            clean_abstract = re.sub(r"<[^>]+>", "", abstract)
            
            # 过滤掉真的没有摘要的垃圾专利，帮大模型省钱
            if clean_abstract == "无摘要" or len(clean_abstract) < 20:
                continue

            parsed_patents.append({
                "全球公开号": p_num,
                "优先权/申请日": p.get("firstPublicationDate", p.get("pubYear", "未知")),
                "申请公司 / 拥有者": org_str,
                "专利名称": p.get("title", "未公开"),
                "核心摘要": clean_abstract[:1500],  # 截断超长摘要防爆 token
                "直达阅读链接": f"https://europepmc.org/article/PAT/{p_num}"
            })
            
        return parsed_patents
    except Exception as e:
        import traceback
        _last_patent_fetch_debug["exception"] = traceback.format_exc()
        return []

def get_last_patent_fetch_debug():
    global _last_patent_fetch_debug
    return _last_patent_fetch_debug or {}
