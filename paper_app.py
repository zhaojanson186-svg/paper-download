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
import urllib.parse # 新增：用于解析谷歌的搜索链接

# Google Drive 官方库
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

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
# 2. Google Drive 上传引擎
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
        
        file = drive_service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id'
        ).execute()
        return True, file.get('id')
    except Exception as e:
        return False, f"上传异常: {str(e)[:50]}"

# ==========================================
# 3. 核心抓取逻辑：文献 + Google Patents
# ==========================================
def sanitize_filename(text):
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

# --- 文献模块 ---
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

# --- 全新专利模块：直连 Google Patents 底层 XHR 接口 ---
def search_google_patents(query, max_results=50):
    """抓取 Google Patents 最纯正的商业情报"""
    base_url = "https://patents.google.com/xhr/query?url="
    # 模拟真实网页的请求参数
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
                
                p_num = p.get("publication_number
