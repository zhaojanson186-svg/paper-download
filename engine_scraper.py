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

def search_google_patents(query, max_results=50):
    global _last_patent_fetch_debug
    base_url = "https://patents.google.com/xhr/query?url="
    encoded_q = urllib.parse.quote(f"q={query}&num={max_results}")
    full_url = base_url + encoded_q
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://patents.google.com/",
    }
    
    _last_patent_fetch_debug = {"query": query, "max_results": max_results, "url": full_url}
    
    try:
        res = requests_get_with_retry(full_url, headers=headers, timeout=20, max_retries=4)
        _last_patent_fetch_debug["status_code"] = getattr(res, "status_code", None)
        _last_patent_fetch_debug["response_preview"] = safe_truncate(getattr(res, "text", ""), 300)
        
        if res.status_code != 200: return []
        payload = res.json()
        clusters = payload.get("results", {}).get("cluster", [])
        if not clusters: return []

        parsed_patents = []
        for item in clusters[0].get("result", []):
            p = item.get("patent", {})
            if not p: continue
            
            p_num = p.get("publication_number", "无编号")
            assignees = p.get("assignee", p.get("inventor", "未公开"))
            org_str = "、".join([str(a) for a in assignees]) if isinstance(assignees, list) else str(assignees)
            
            clean_snippet = re.sub(r"<[^>]+>", "", p.get("snippet", "无摘要"))
            
            parsed_patents.append({
                "全球公开号": p_num,
                "优先权/申请日": p.get("priority_date", p.get("filing_date", "未知")),
                "申请公司 / 拥有者": org_str,
                "专利名称": p.get("title", "未公开"),
                "核心摘要": clean_snippet,
                "直达阅读链接": f"https://patents.google.com/patent/{p_num}",
            })
        return parsed_patents
    except Exception as e:
        import traceback
        _last_patent_fetch_debug["exception"] = traceback.format_exc()
        return []

def get_last_patent_fetch_debug():
    global _last_patent_fetch_debug
    return _last_patent_fetch_debug or {}
