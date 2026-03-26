import streamlit as st
import os
import requests
from Bio import Entrez
import time
import xml.etree.ElementTree as ET
import pandas as pd
import json
import urllib.parse

# Google Drive 官方库
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Google 大模型 AI 库
import google.generativeai as genai

# ==========================================
# 1. 配置区与历史记录系统
# ==========================================
Entrez.email = "your_email@example.com"
DOWNLOAD_DIR = "PDF_Downloads"
HISTORY_FILE = "download_history.json"
AI_PARSE_DEBUG_LOG_FILE = "ai_parse_debug.jsonl"

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
# 2. AI 提纯双引擎 (完全避开网页Bug的安全版)
# ==========================================
# ==========================================
# 2. AI 提纯双引擎 (抗医疗误伤 + 终极提取版)
# ==========================================
def init_ai_model(model_name: str = "gemini-1.5-flash-latest"):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=api_key)
        # 支持用户输入带资源前缀的写法，如 "models/gemini-1.5-flash"
        mn = str(model_name).strip()
        if mn.startswith("models/"):
            mn = mn[len("models/") :]
        model = genai.GenerativeModel(mn)
        return model
    except Exception:
        return None

def list_available_gemini_models(max_items: int = 50):
    """
    尝试从当前 API Key 拉取可用的模型列表（用于排查 404/权限问题）。
    返回值：list[str]
    """
    try:
        api_key = st.secrets.get("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        api_models = genai.list_models()
        # 兼容 generator / list
        out = []
        for m in api_models:
            name = getattr(m, "name", None) or getattr(m, "model_name", None) or str(m)
            name = str(name)
            if name.startswith("models/"):
                name = name[len("models/") :]
            out.append(name)
            if len(out) >= max_items:
                break
        return out
    except Exception:
        return []

def extract_json_object(text: str):
    """
    从模型输出中尽可能稳健地提取第一段 JSON 对象。
    支持：
    1) ```json ... ``` 代码块
    2) 直接在文本中做括号配对截取
    """
    import re
    if not text:
        return None

    # 1) 优先抓 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        candidate = m.group(1).strip()
        # 代码块里如果仍不是 JSON，就继续尝试括号截取
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

    # 2) 括号配对：从第一个 '{' 开始，找到对应的 '}' 结束
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                return candidate.strip()
    return None

def parse_ai_json(text: str):
    candidate = extract_json_object(text)
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except Exception:
        return None

def safe_truncate(text: str, limit: int = 800):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"...(truncated,{len(text)} chars)"

def generate_ai_json_with_retry(model, prompt: str, expected_keys: list, max_retries: int = 3):
    """
    调用 LLM 并尽可能保证返回结构：
    - 返回 dict，包含 expected_keys 中的所有键（缺失则用'未提取'兜底）
    - 遇到 429/短暂异常会重试，并把最终原因写到对应字段里
    """
    if not model or not prompt:
        return {k: "未提取" for k in expected_keys}

    import time as _time
    last_err = ""
    # 防止不同 Streamlit 版本/运行上下文导致 session_state 不可用
    try:
        ss = getattr(st, "session_state", {}) or {}
        debug_enabled = bool(ss.get("debug_ai_parse", False))
    except Exception:
        debug_enabled = False
    # 仅在调试时落盘，避免频繁写文件影响性能
    do_log = debug_enabled

    def _log_debug(payload: dict):
        if not do_log:
            return
        try:
            with open(AI_PARSE_DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            # 日志失败不影响主流程
            pass

    for attempt in range(max_retries):
        try:
            res_text = model.generate_content(
                prompt,
                # 比 BLOCK_NONE 更保守，避免无边界放开导致的合规风险
                safety_settings=[
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"}
                ],
            ).text
            candidate = extract_json_object(res_text)
            data = None
            parse_error = ""
            if candidate:
                try:
                    data = json.loads(candidate)
                except Exception as e:
                    parse_error = str(e).replace("\n", " ")
                    data = None

            if debug_enabled:
                with st.expander(f"🧪 AI 解析调试（attempt {attempt+1}/{max_retries}）", expanded=False):
                    st.write("expected_keys:", expected_keys)
                    st.write("是否找到 JSON 片段(candidate):", bool(candidate))
                    if candidate:
                        st.write("candidate 预览：")
                        st.code(safe_truncate(candidate, 900))
                    st.write("模型输出预览：")
                    st.code(safe_truncate(res_text, 900))
                    if parse_error:
                        st.write("JSON loads 错误：", parse_error)

            if not isinstance(data, dict):
                # 解析失败时继续重试，让模型更可能按格式输出
                last_err = "解析失败(无JSON/非dict)" if not candidate else f"解析失败(JSONloads失败): {parse_error[:60]}"
                _log_debug(
                    {
                        "ts": time.time(),
                        "attempt": attempt + 1,
                        "max_retries": max_retries,
                        "expected_keys": expected_keys,
                        "last_err": last_err,
                        "candidate_found": bool(candidate),
                        "candidate_preview": safe_truncate(candidate, 500) if candidate else None,
                        "response_preview": safe_truncate(res_text, 500),
                    }
                )
                if attempt < max_retries - 1:
                    continue
                return {k: "解析失败(无法提取JSON)" for k in expected_keys}

            out = {}
            for k in expected_keys:
                v = data.get(k, "未提取")
                out[k] = str(v) if v is not None else "未提取"
            return out
        except Exception as e:
            last_err = str(e).replace("\n", " ")
            _log_debug(
                {
                    "ts": time.time(),
                    "attempt": attempt + 1,
                    "max_retries": max_retries,
                    "expected_keys": expected_keys,
                    "last_err": last_err,
                    "candidate_found": None,
                    "candidate_preview": None,
                    "response_preview": None,
                }
            )
            # 429：退避重试
            if "429" in last_err and attempt < max_retries - 1:
                _time.sleep(2 ** attempt + 1)
                continue
            break

    # 终局：把错误塞进第一个字段里便于定位
    out = {k: "解析报错" for k in expected_keys}
    if expected_keys:
        out[expected_keys[0]] = last_err[:60] if last_err else "解析报错"
    return out

def requests_get_with_retry(url, headers=None, timeout=15, max_retries: int = 3):
    """requests.get 的简化版重试：遇到 429/5xx 做指数退避。"""
    import time as _time
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                _time.sleep(2 ** attempt + 1)
                continue
            return r
        except Exception as e:
            last_err = e
            _time.sleep(2 ** attempt + 1)
    if last_err:
        raise last_err
    # 理论上不会到这；兜底抛异常更利于定位
    raise Exception(f"GET failed after {max_retries} retries: {url}")

def analyze_paper_with_ai(model, abstract_text):
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "实验模型": "未提取", "AI核心结论": "无有效摘要"}
    
    prompt = f"""
    作为资深抗体药物研发专家，请阅读以下学术文献摘要，提取关键科研情报。
    请直接输出一个合法的 JSON 格式，严格包含以下 3 个键名，绝对不要输出多余解释：
    {{
        "靶点组合": "提取文献研究的靶点(如 CD3等)，若无则写'未提及'",
        "实验模型": "提取研究使用的模型(如 细胞系、小鼠等)，若无则写'未提及'",
        "AI核心结论": "用15个字以内的中文高度概括药效或发现"
    }}
    限定说明：只做信息抽取，不给出任何诊断/治疗建议或操作步骤。
    摘要原文：
    {abstract_text}
    """
    try:
        return generate_ai_json_with_retry(
            model=model,
            prompt=prompt,
            expected_keys=["靶点组合", "实验模型", "AI核心结论"],
            max_retries=3,
        )
    except Exception as e:
        # 核心修复 3：真实报错透传，再失败就能在表格里直接看到死因！
        err = str(e).replace('\n', ' ')
        if "429" in err: err = "请求过快被限流"
        return {"靶点组合": "解析报错", "实验模型": "真实原因:", "AI核心结论": err[:35]}

def analyze_patent_with_ai(model, abstract_text):
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "抗体构型": "未提取", "AI一句话总结": "无有效摘要"}
    
    prompt = f"""
    作为资深抗体药物研发专家，请阅读以下专利摘要，提取关键商业与技术情报。
    请直接输出一个合法的 JSON 格式，严格包含以下 3 个键名，绝对不要输出多余解释：
    {{
        "靶点组合": "提取提到的所有靶点(如 CD3等)，若无则写'未提及'",
        "抗体构型": "提取抗体类型或技术平台(如 scFv, ADC等)，若无则写'未提及'",
        "AI一句话总结": "用15个字以内的中文高度概括其核心适应症"
    }}
    限定说明：只做信息抽取，不给出任何诊断/治疗建议或操作步骤。
    摘要原文：
    {abstract_text}
    """
    try:
        return generate_ai_json_with_retry(
            model=model,
            prompt=prompt,
            expected_keys=["靶点组合", "抗体构型", "AI一句话总结"],
            max_retries=3,
        )
    except Exception as e:
        err = str(e).replace('\n', ' ')
        if "429" in err: err = "请求过快被限流"
        return {"靶点组合": "解析报错", "抗体构型": "真实原因:", "AI一句话总结": err[:35]}

# ==========================================
# 3. Google Drive 上传引擎
# ==========================================
def get_gdrive_service():
    try:
        raw_token = st.secrets["GCP_TOKEN"]
        token_dict = json.loads(raw_token, strict=False)
        creds = Credentials.from_authorized_user_info(token_dict)
        return build('drive', 'v3', credentials=creds), None
    except Exception as e:
        return None, f"Token解析失败: {str(e)}"

def upload_to_gdrive(drive_service, local_file_path, file_name, folder_id, mime_type='application/pdf'):
    try:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaFileUpload(local_file_path, mimetype=mime_type)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True, file.get('id')
    except Exception as e:
        return False, f"上传异常: {str(e)[:50]}"

# ==========================================
# 4. 核心抓取逻辑：文献提取 + 专利抓取
# ==========================================
def sanitize_filename(text):
    import re
    clean_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return clean_text.replace(" ", "_")

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
    import re
    base_url = "https://patents.google.com/xhr/query?url="
    q_params = f"q={query}&num={max_results}"
    encoded_q = urllib.parse.quote(q_params)
    full_url = base_url + encoded_q
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    try:
        res = requests_get_with_retry(full_url, headers=headers, timeout=20, max_retries=3)
        if res.status_code != 200:
            return []

        clusters = res.json().get("results", {}).get("cluster", [])
        if not clusters:
            return []

        parsed_patents = []
        for item in clusters[0].get("result", []):
            p = item.get("patent", {})
            if not p:
                continue
            p_num = p.get("publication_number", "无编号")
            title = p.get("title", "未公开")

            assignees = p.get("assignee", [])
            if isinstance(assignees, str):
                org_str = assignees
            elif isinstance(assignees, list):
                org_str = "、".join([str(a) for a in assignees])
            else:
                org_str = ""

            if not org_str:
                inventors = p.get("inventor", [])
                if isinstance(inventors, str):
                    org_str = inventors
                elif isinstance(inventors, list):
                    org_str = "、".join([str(a) for a in inventors])
                else:
                    org_str = "未公开"

            clean_snippet = re.sub(r"<[^>]+>", "", p.get("snippet", "无摘要"))
            pub_date = p.get("priority_date", p.get("filing_date", "未知"))

            parsed_patents.append({
                "全球公开号": p_num,
                "优先权/申请日": pub_date,
                "申请公司 / 拥有者": org_str,
                "专利名称": title,
                "核心摘要": clean_snippet,
                "直达阅读链接": f"https://patents.google.com/patent/{p_num}",
            })
        return parsed_patents
    except Exception:
        return []
        # ==========================================
# 5. 前端网页界面
# ==========================================
st.set_page_config(page_title="双擎 AI 情报终端", layout="wide", page_icon="🧠")

st.title("🧠 药物研发 AI 全景情报终端")
st.markdown("文献精读与专利防线双向覆盖。自动抓取、LLM 智能提纯、去重并直传云盘。")

history = load_history()

with st.sidebar:
    st.header("⚙️ 全局配置")
    gdrive_folder_id = st.text_input("📁 Google Drive 文件夹 ID", placeholder="粘贴你的文件夹ID")
    st.markdown("---")
    
    st.caption("用于 Gemini API 的模型 id（Google AI Studio 可用的 model 名称）")
    gemini_model_name = st.text_input("🤖 Gemini model id", value="gemini-1.5-flash-latest")

    if st.button("🔄 刷新可用模型列表", type="secondary"):
        with st.spinner("正在拉取可用模型列表..."):
            models = list_available_gemini_models(max_items=50)
        if models:
            st.success(f"获取到 {len(models)} 个模型：请从列表里手动复制到上面的 model id 输入框。")
        else:
            st.warning("未能获取模型列表（可能是 SDK/权限限制/网络原因）。仍可手动填写 model id。")
        if models:
            st.expander("可用模型列表（复制其中一个）").code("\n".join(models))

    ai_model = init_ai_model(gemini_model_name)
    if ai_model:
        st.success("🤖 AI 双擎提纯：已激活")
    else:
        st.error("🤖 AI 双擎提纯：离线 (需配置 GEMINI_API_KEY)")

    st.markdown("---")
    st.checkbox("🧪 显示 AI 解析调试信息", value=False, key="debug_ai_parse")

    st.markdown("---")
    st.write(f"📖 云端总账本记录数: **{len(history)}** 条")
    if st.button("🗑️ 清空历史记录", type="secondary"):
        if os.path.exists(HISTORY_FILE): os.remove(HISTORY_FILE)
        st.success("账本已彻底重置！")
        time.sleep(1)
        st.rerun()

tab1, tab2 = st.tabs(["📄 核心文献直传 + AI 精读", "💡 专利雷达 + AI 构型拆解"])

# ========================================================
# 引擎 1：文献抓取 + AI 精读报表
# ========================================================
with tab1:
    st.markdown("### 🧬 学术前沿：抓取原文并生成 AI 精读报表")
    query_paper = st.text_input("输入检索关键词 (靶点/适应症)", value="CD3 bispecific antibody", key="q_paper")
    max_papers = st.number_input("本次请求最大篇数", min_value=1, max_value=500, value=15)
    
    if st.button("🚀 开始极速抓取并进行 AI 提纯", type="primary"):
        if not query_paper or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        elif not ai_model:
            st.error("请先激活 AI 引擎才能生成精读报表！")
        else:
            with st.spinner("正在初始化网盘与文献数据库..."):
                drive_service, _ = get_gdrive_service()
            if not drive_service:
                st.error("网盘授权失败。")
            else:
                all_pmc_ids = search_pmc_oa(query_paper, max_papers)
                new_pmc_ids = [pid for pid in all_pmc_ids if f"PMC_{pid}" not in history]
                
                if not new_pmc_ids:
                    st.info("🔕 本次未发现新的开源文献。")
                else:
                    st.write(f"✅ 发现 **{len(new_pmc_ids)}** 篇新文献！正在下载 PDF 并调动 AI 阅读摘要...")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    paper_report_data = []
                    
                    for i, pmcid in enumerate(new_pmc_ids):
                        status_text.text(f"🤖 正在处理第 {i+1}/{len(new_pmc_ids)} 篇 (PMC{pmcid}): 下载原文 + AI 精读...")
                        
                        status, local_path, file_name = download_pdf(pmcid, query_paper)
                        pdf_uploaded = "未上传"
                        if status == "下载成功":
                            is_up, _ = upload_to_gdrive(drive_service, local_path, file_name, gdrive_folder_id)
                            pdf_uploaded = "✅ 原文已入库" if is_up else "❌ 上传失败"
                            os.remove(local_path)
                        
                        title, abstract = fetch_pmc_metadata(pmcid)
                        ai_insights = analyze_paper_with_ai(ai_model, abstract)
                        
                        paper_report_data.append({
                            "文献编号": f"PMC{pmcid}",
                            "🎯核心靶点": ai_insights.get("靶点组合", ""),
                            "🐁实验模型": ai_insights.get("实验模型", ""),
                            "💡核心结论": ai_insights.get("AI核心结论", ""),
                            "原文状态": pdf_uploaded,
                            "文献标题": title,
                            "官方直达链接": f"https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/"
                        })
                        
                        # 只要 AI 精读完成，就应该避免下次重复花钱
                        history[f"PMC_{pmcid}"] = f"✅ 已精读 (PDF入库状态: {pdf_uploaded})"
                        save_history(history)
                        
                        time.sleep(4.5)
                        progress_bar.progress((i + 1) / len(new_pmc_ids))
                        
                    status_text.text("🧠 所有文献下载及 AI 精读完毕！正在推送总报表...")
                    
                    df_papers = pd.DataFrame(paper_report_data)
                    st.dataframe(
                        df_papers, 
                        column_config={"官方直达链接": st.column_config.LinkColumn("点击看网页原文")},
                        use_container_width=True, hide_index=True
                    )
                    
                    timestamp = time.strftime("%m%d_%H%M")
                    safe_q = sanitize_filename(query_paper)
                    csv_name = f"{safe_q}_Paper_AI_Report_{timestamp}.csv"
                    csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                    df_papers.to_csv(csv_path, index=False, encoding="utf-8-sig")
                    
                    is_up, _ = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, mime_type='text/csv')
                    if is_up:
                        st.success(f"🎉 任务完美结束！原文 PDF 及《文献精读报表》已推送到你的 Google Drive！")
                        os.remove(csv_path)

# ========================================================
# 引擎 2：专利情报抓取 + AI 深度提纯
# ========================================================
# ========================================================
# 引擎 2：专利情报抓取 + AI 深度提纯
# ========================================================
with tab2:
    st.markdown("### 🧠 竞争对手挖掘：大模型提炼核心管线情报")
    query_patent = st.text_input("输入检索关键词 (如靶点、技术全称)", value="CD3 bispecific antibody", key="q_patent")
    max_patents = st.number_input("需梳理的专利数量", min_value=1, max_value=200, value=30)
    
    if st.button("📊 生成【专利 AI 提纯】报表并推送", type="primary"):
        if not query_patent or not gdrive_folder_id:
            st.error("请确保已填写关键词和左侧的 Google Drive 文件夹 ID！")
        elif not ai_model:
            st.error("AI 引擎未激活！请配置 API_KEY。")
        else:
            with st.spinner("正在提取底层专利数据并比对账本..."):
                drive_service, _ = get_gdrive_service()
                if not drive_service:
                    st.error("网盘授权失败。")
                else:
                    patents = search_google_patents(query_patent, max_patents)
                    
                    # 🥇 核心修复：把丢失的“空数据防线”加回来！
                    if not patents:
                        st.warning("⚠️ 未能抓取到专利数据！极大可能是因为刚才频繁测试，触发了谷歌的防爬虫限流机制。请喝口水，稍等几分钟后再试！")
                    else:
                        new_patents = [pt for pt in patents if f"PAT_{pt['全球公开号']}" not in history]
                        
                        if not new_patents:
                            st.info("🔕 扫描到的专利均已在历史账本中，无需重复提取！")
                        else:
                            st.write(f"✅ 发现 **{len(new_patents)}** 项新专利！大模型正在逐篇拆解抗体构型...")
                            
                            ai_progress = st.progress(0)
                            ai_status = st.empty()
                            
                            for idx, pt in enumerate(new_patents):
                                ai_status.text(f"🤖 AI 提纯第 {idx+1}/{len(new_patents)} 项: {pt['全球公开号']} ...")
                                ai_insights = analyze_patent_with_ai(ai_model, pt['核心摘要'])
                                
                                pt["🎯靶点组合"] = ai_insights.get("靶点组合", "未提取")
                                pt["🧬抗体构型"] = ai_insights.get("抗体构型", "未提取")
                                pt["💡商业一句话总结"] = ai_insights.get("AI一句话总结", "未提取")

                                # 记录已完成 AI 提纯，尽量避免“上传失败 -> 下次重复提纯”
                                history[f"PAT_{pt['全球公开号']}"] = "✅ 已AI提纯"

                                time.sleep(4.5)
                                ai_progress.progress((idx + 1) / len(new_patents))
                            
                            ai_status.text("🧠 提纯完毕！正在生成全景竞争报表...")
                            # 先落盘历史账本，再做 CSV 生成与上传
                            save_history(history)
                            
                            cols = ["全球公开号", "申请公司 / 拥有者", "🎯靶点组合", "🧬抗体构型", "💡商业一句话总结", "优先权/申请日", "专利名称", "核心摘要", "直达阅读链接"]
                            df_patents = pd.DataFrame(new_patents)[cols]
                            
                            st.dataframe(df_patents, column_config={"直达阅读链接": st.column_config.LinkColumn("点击查阅原文")}, use_container_width=True, hide_index=True)
                            
                            timestamp = time.strftime("%m%d_%H%M")
                            safe_q = sanitize_filename(query_patent)
                            csv_name = f"{safe_q}_Patent_AI_Report_{timestamp}.csv"
                            csv_path = os.path.join(DOWNLOAD_DIR, csv_name)
                            df_patents.to_csv(csv_path, index=False, encoding="utf-8-sig")
                            
                            is_up, _ = upload_to_gdrive(drive_service, csv_path, csv_name, gdrive_folder_id, mime_type='text/csv')
                            if is_up:
                                st.success(f"🎉 任务完美结束！带有 AI 商业总结的报表已推送到网盘！")
                                os.remove(csv_path)
                            else:
                                st.warning("⚠️ CSV 上传失败：账本已先记录 AI 提纯结果，避免重复花钱。")
