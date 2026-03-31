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

def generate_ai_json_with_retry(model, prompt: str, expected_keys: list, debug_enabled: bool = False, max_retries: int = 3):
    if not model or not prompt:
        return {k: "未提取" for k in expected_keys}

    last_err = ""
    for attempt in range(max_retries):
        try:
            # 恢复旧版 SDK 完美兼容的列表格式，彻底杜绝本地崩溃！
            res_text = model.generate_content(
                prompt,
                safety_settings=[
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
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
                return {k: f"解析失败:{last_err[:20]}" for k in expected_keys}

            return {k: str(data.get(k, "未提取")) for k in expected_keys}

        except Exception as e:
            last_err = str(e).replace("\n", " ")
            # 如果是 429 限流，直接休眠重试
            if "429" in last_err and attempt < max_retries - 1:
                time.sleep(2 ** attempt + 1)
                continue
            # 遇到其他致命错误直接跳出，把最真实的报错抛给主程序雷达
            break

    # 包装最终报错信息，确保带上关键字给外层雷达识别
    out = {k: "解析报错" for k in expected_keys}
    if expected_keys:
        # 把大模型真实的报错信息（如 400/Quota 等）写进第一个格子里，方便我们排错
        out[expected_keys[0]] = f"解析报错: {last_err[:100]}" 
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
        "AI核心结论": "用不超过100个中文字符概括药效或发现"
    }}
    摘要原文：
    {abstract_text}
    """
    return generate_ai_json_with_retry(model, prompt, ["靶点组合", "实验模型", "AI核心结论"], debug_enabled)

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
    摘要原文：
    {abstract_text}
    """
    return generate_ai_json_with_retry(model, prompt, ["靶点组合", "抗体构型", "AI一句话总结"], debug_enabled)
