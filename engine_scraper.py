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
    """抓取文献的标题和摘要"""
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
    """尝试下载 PDF"""
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

def download_fulltext_txt(pmcid, query, download_dir):
    """文献网页全文提取"""
    safe_query = sanitize_filename(query)
    file_name = f"{safe_query}_PMC{pmcid}_网页全文.txt"
    file_path = os.path.join(download_dir, file_name)
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmcid}&retmode=xml"
    try:
        res = requests_get_with_retry(url, timeout=20)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            title_node = root.find(".//article-title")
            title = "".join(title_node.itertext()) if title_node is not None else f"PMC{pmcid}"
            abstract_node = root.find(".//abstract")
            abstract = "".join(abstract_node.itertext()) if abstract_node is not None else "无摘要内容。"
            body_node = root.find(".//body")
            body_text = []
            if body_node is not None:
                for p in body_node.findall(".//p"):
                    text_chunk = "".join(p.itertext()).strip()
                    if text_chunk: body_text.append(text_chunk)
            if not body_text: return "网页正文为空", None, None
            full_content = f"【文献标题】: {title}\n" + "="*50 + f"\n【摘要】:\n{abstract}\n\n【正文】:\n" + "\n\n".join(body_text)
            with open(file_path, "w", encoding="utf-8") as f: f.write(full_content)
            return "备用抓取成功", file_path, file_name
        return f"HTTP {res.status_code}", None, None
    except Exception as e: return f"抓取报错: {str(e)[:20]}", None, None

def download_patent_fulltext_txt(patent_id, query, download_dir):
    """【欧洲模式】提取专利全文"""
    safe_query = sanitize_filename(query)
    clean_id = patent_id.replace("PAT", "")
    file_name = f"{safe_query}_Patent_{clean_id}_专利全文.txt"
    file_path = os.path.join(download_dir, file_name)
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{clean_id}/fullTextXML"
    try:
        res = requests_get_with_retry(url, timeout=20)
        if res.status_code == 200 and "<" in res.text:
            root = ET.fromstring(res.content)
            body_text = []
            for text_node in root.findall(".//*"):
                if text_node.text and len(text_node.text.strip()) > 15: body_text.append(text_node.text.strip())
            if not body_text: return "无全文数据", None, None
            full_content = f"【专利】: {patent_id}\n" + "="*50 + "\n" + "\n\n".join(body_text[:500])
            with open(file_path, "w", encoding="utf-8") as f: f.write(full_content)
            return "抓取成功", file_path, file_name
        return "数据库无全文", None, None
    except Exception as e: return f"解析报错: {str(e)[:20]}", None, None

# ==========================================
# 新增：【谷歌模式】隐藏接口全文提取引擎
# ==========================================
def download_google_patent_fulltext_txt(patent_id, query, download_dir):
    """【谷歌专用】利用隐藏 XHR 接口提取全球专利正文说明书及权利要求书"""
    safe_query = sanitize_filename(query)
    file_name = f"{safe_query}_GooglePatent_{patent_id}_全文.txt"
    file_path = os.path.join(download_dir, file_name)
    
    # 构造 Google Patents 内部数据接口 URL
    url = f"https://patents.google.com/xhr/query?url=patent/{patent_id}/en"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        res = requests_get_with_retry(url, headers=headers, timeout=20)
        if res.status_code == 200:
            data = res.json()
            p_data = data.get("results", {}).get("patent", {})
            
            # 提取说明书 (Description) 和 权利要求 (Claims)
            description = p_data.get("description", "无说明书数据")
            claims = p_data.get("claims", "无权利要求数据")
            
            # 清洗 HTML 标签
            clean_desc = re.sub(r"<[^>]+>", "\n", description)
            clean_claims = re.sub(r"<[^>]+>", "\n", claims)
            
            full_content = f"【专利号】: {patent_id}\n"
            full_content += "="*50 + "\n"
            full_content += f"【权利要求书 (Claims)】:\n{clean_claims}\n\n"
            full_content += "="*50 + "\n"
            full_content += f"【详细说明书 (Description)】:\n{clean_desc[:15000]}" # 限制长度防止文件过大
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(full_content)
            return "抓取成功", file_path, file_name
        return f"HTTP {res.status_code}", None, None
    except Exception as e:
        return f"谷歌提取失败: {str(e)[:20]}", None, None

def search_pmc_oa(query, max_results=5):
    oa_query = f"({query}) AND open access[filter]"
    try:
        handle = Entrez.esearch(db="pmc", term=oa_query, retmax=max_results, sort="date")
        record = Entrez.read(handle)
        handle.close()
        return record["IdList"] 
    except Exception: return []

def search_europe_pmc_patents(query, max_results=30):
    global _last_patent_fetch_debug
    epmc_query = f'({query}) AND SRC:PAT'
    encoded_q = urllib.parse.quote(epmc_query)
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={encoded_q}&resultType=core&format=json&pageSize={max_results}"
    _last_patent_fetch_debug = {"query": query, "url": url}
    try:
        res = requests_get_with_retry(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        _last_patent_fetch_debug["status_code"] = getattr(res, "status_code", None)
        if res.status_code != 200: return []
        results = res.json().get("resultList", {}).get("result", [])
        parsed = []
        for p in results:
            abstract = p.get("abstractText", "无摘要").replace("\n", " ")
            clean_abs = re.sub(r"<[^>]+>", "", abstract)
            if len(clean_abs) < 20: continue
            parsed.append({
                "全球公开号": p.get("id"), "优先权/申请日": p.get("firstPublicationDate", "未知"),
                "申请公司 / 拥有者": p.get("authorString", "未公开"), "专利名称": p.get("title", "未公开"),
                "核心摘要": clean_abs[:1500], "直达阅读链接": f"https://europepmc.org/article/PAT/{p.get('id')}"
            })
        return parsed
    except Exception: return []

def search_google_patents(query, max_results=30):
    global _last_patent_fetch_debug
    # 使用 quote_plus 把空格变成 +，这更符合 Google 引擎的习惯
    encoded_q = urllib.parse.quote_plus(query)
    url = f"https://patents.google.com/xhr/query?url=q%3D{encoded_q}%26num%3D{max_results}"
    
    _last_patent_fetch_debug = {"query": query, "url": url}
    
    # 【核心修复】：穿上逼真的浏览器伪装服
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://patents.google.com/",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    try:
        res = requests_get_with_retry(url, headers=headers, timeout=20, max_retries=3)
        _last_patent_fetch_debug["status_code"] = getattr(res, "status_code", None)
        _last_patent_fetch_debug["response_preview"] = safe_truncate(getattr(res, "text", ""), 300)
        
        if res.status_code != 200: return []
        
        results = res.json().get("results", {}).get("cluster", [{}])[0].get("result", [])
        parsed = []
        for p in results:
            patent = p.get("patent", {})
            p_num = patent.get("publication_number")
            if not p_num: continue
            assignee = patent.get("assignee", "未公开")
            if isinstance(assignee, list): assignee = ", ".join(assignee)
            parsed.append({
                "全球公开号": p_num, "优先权/申请日": patent.get("priority_date", "未知"),
                "申请公司 / 拥有者": assignee, "专利名称": patent.get("title", "未公开"),
                "核心摘要": re.sub(r"<[^>]+>", "", patent.get("snippet", "无摘要"))[:1500],
                "直达阅读链接": f"https://patents.google.com/patent/{p_num}/en"
            })
        return parsed
    except Exception as e:
        import traceback
        _last_patent_fetch_debug["exception"] = traceback.format_exc()
        return []

def get_last_patent_fetch_debug():
    global _last_patent_fetch_debug
    return _last_patent_fetch_debug or {}
