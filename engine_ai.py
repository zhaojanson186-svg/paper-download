import json
import time
import google.generativeai as genai
from config import AI_PARSE_DEBUG_LOG_FILE

def init_ai_model(api_key: str, model_name: str = "gemini-1.5-flash-latest"):
    try:
        genai.configure(api_key=api_key)
        mn = str(model_name).strip()
        if mn.startswith("models/"):
            mn = mn[len("models/") :]
        return genai.GenerativeModel(mn)
    except Exception:
        return None

def list_available_gemini_models(api_key: str, max_items: int = 50):
    try:
        if api_key:
            genai.configure(api_key=api_key)
        out = []
        for m in genai.list_models():
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
    """防御性提取：绝对不用复杂的正则，仅用 replace 脱壳 + 括号配对"""
    if not text:
        return None
    
    clean_text = text.replace("```json", "").replace("```", "").strip()

    start = clean_text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(clean_text)):
        ch = clean_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = clean_text[start : i + 1]
                return candidate.strip()
    return None

def safe_truncate(text: str, limit: int = 800):
    if text is None: return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"...(truncated,{len(text)} chars)"

def generate_ai_json_with_retry(model, prompt: str, expected_keys: list, debug_enabled: bool = False, max_retries: int = 3):
    if not model or not prompt:
        return {k: "未提取" for k in expected_keys}

    last_err = ""
    def _log_debug(payload: dict):
        if not debug_enabled: return
        try:
            with open(AI_PARSE_DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except: pass

    for attempt in range(max_retries):
        try:
            # ==========================================
            # 核心修复区：完美兼容所有新老 Gemini 模型的参数格式
            # ==========================================
            res_text = model.generate_content(
                prompt,
                generation_config={"temperature": 0.1}, # 降低随机性，保证 JSON 格式极度稳定
                safety_settings={
                    "HARM_CATEGORY_DANGEROUS_CONTENT": "BLOCK_NONE",
                    "HARM_CATEGORY_HARASSMENT": "BLOCK_NONE",
                    "HARM_CATEGORY_HATE_SPEECH": "BLOCK_NONE",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT": "BLOCK_NONE"
                }
            ).text
            
            candidate = extract_json_object(res_text)
            data, parse_error = None, ""
            
            if candidate:
                try:
                    data = json.loads(candidate)
                except Exception as e:
                    parse_error = str(e).replace("\n", " ")
                    data = None

            if not isinstance(data, dict):
                last_err = "解析失败(无JSON/非dict)" if not candidate else f"解析失败(JSONloads失败): {parse_error[:60]}"
                if attempt < max_retries - 1:
                    continue
                return {k: "解析失败(无法提取JSON)" for k in expected_keys}

            return {k: str(data.get(k, "未提取")) for k in expected_keys}

        except Exception as e:
            last_err = str(e).replace("\n", " ")
            # 兼容检测：如果是 429 限流，直接原地等待重试；其他严重错误（如400/503）直接抛出给外层换弹夹
            if "429" in last_err and attempt < max_retries - 1:
                time.sleep(2 ** attempt + 1)
                continue
            break

    out = {k: "解析报错" for k in expected_keys}
    if expected_keys:
        out[expected_keys[0]] = last_err[:60] if last_err else "解析报错"
    return out

def analyze_paper_with_ai(model, abstract_text, debug_enabled=False):
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "实验模型": "未提取", "AI核心结论": "无有效摘要"}
    
    prompt = f"""
    作为资深抗体药物研发专家，请阅读以下学术文献摘要，提取关键科研情报。
    请直接输出一个合法的 JSON 格式，严格包含以下 3 个键名，绝对不要输出多余解释：
    {{
        "靶点组合": "提取文献研究的靶点(如 CD3等)，若无则写'未提及'",
        "实验模型": "提取研究使用的模型(如 细胞系、小鼠等)，若无则写'未提及'",
        "AI核心结论": "用不超过100个中文字符（约100字以内）高度概括药效或发现"
    }}
    限定说明：只做信息抽取，不给出任何诊断/治疗建议或操作步骤。
    摘要原文：
    {abstract_text}
    """
    try:
        return generate_ai_json_with_retry(model, prompt, ["靶点组合", "实验模型", "AI核心结论"], debug_enabled)
    except Exception as e:
        err = str(e).replace('\n', ' ')
        return {"靶点组合": "解析报错", "实验模型": "真实原因:", "AI核心结论": "请求过快被限流" if "429" in err else err[:35]}

def analyze_patent_with_ai(model, abstract_text, debug_enabled=False):
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
        return generate_ai_json_with_retry(model, prompt, ["靶点组合", "抗体构型", "AI一句话总结"], debug_enabled)
    except Exception as e:
        err = str(e).replace('\n', ' ')
        return {"靶点组合": "解析报错", "抗体构型": "真实原因:", "AI一句话总结": "请求过快被限流" if "429" in err else err[:35]}
