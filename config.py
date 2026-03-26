import os
import json
import re

# ==========================================
# 全局常量配置
# ==========================================
ENTREZ_EMAIL = "your_email@example.com"
DOWNLOAD_DIR = "PDF_Downloads"
HISTORY_FILE = "download_history.json"
AI_PARSE_DEBUG_LOG_FILE = "ai_parse_debug.jsonl"
PATENT_FETCH_DEBUG_LOG_FILE = "patent_fetch_debug.jsonl"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ==========================================
# 存储与通用工具
# ==========================================
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

def sanitize_filename(text):
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")
