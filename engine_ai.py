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

# ==========================================
# 把我不小心弄丢的 safe_truncate 找回来了！
# ==========================================
def safe_truncate(text: str, limit: int = 800):
    if text is None: return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"...(truncated,{len(text)} chars)"

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
        out[expected_keys[0]] = f"解析报错: {last_err[:100]}" 
    return out

# engine_ai.py 中的替换代码

def analyze_paper_with_ai(model, abstract_text, debug_enabled=False):
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "前沿机制设计": "未提取", "实验模型": "未提取", "AI深度结论": "无有效摘要"}
    
    prompt = f"""
    作为全球顶尖的抗体药物研发与分子生物学专家，请对以下学术文献摘要进行深度审查。
    你的任务是不仅要提取表面信息，更要“透视”其底层的分子工程学创新。
    
    请严格输出合法的 JSON 格式，包含以下 4 个键名：
    {{
        "靶点组合": "提取所有被靶向的抗原(如 CD3, EpCAM等)，若无写'未提及'",
        "前沿机制设计": "重点扫描并提取: 1)是否为前药/条件激活(如包含遮蔽肽、被特定蛋白酶切割); 2)是否有特殊的Fc工程化修饰; 3)是否为新型递送(如mRNA-LNP); 4)不对称结合比(如2:1)。如果没有这些高级设计，简述其基础抗体类型。",
        "实验模型": "提取验证药效所用的体内/体外模型(如 PBMC人源化小鼠、特定的耐药细胞系等)",
        "AI深度结论": "用不超过120个中文字符，一针见血地指出该研究解决了什么痛点（如：有效降低了CRS毒性、克服了TME抑制、延长了半衰期等）及核心药效。"
    }}
    限定说明：绝对不要输出多余的解释性文本，只输出 JSON。
    摘要原文：
    {abstract_text}
    """
    try:
        return generate_ai_json_with_retry(model, prompt, ["靶点组合", "前沿机制设计", "实验模型", "AI深度结论"], debug_enabled)
    except Exception as e:
        err = str(e).replace('\n', ' ')
        return {"靶点组合": "解析报错", "前沿机制设计": "解析报错", "实验模型": "真实原因:", "AI深度结论": "请求过快被限流" if "429" in err else err[:35]}

def analyze_patent_with_ai(model, abstract_text, debug_enabled=False):
    if not model or not abstract_text or len(abstract_text) < 20:
        return {"靶点组合": "未提取", "核心抗体构型": "未提取", "特殊工程化保护": "未提取", "商业深度总结": "无有效摘要"}
    
    prompt = f"""
    作为资深抗体药物专利律师兼研发总监，请审查以下专利摘要，提取具有极高商业与技术壁垒的情报。
    请敏锐捕捉该专利是为了保护什么核心分子结构而申请的。
    
    请严格输出合法的 JSON 格式，包含以下 4 个键名：
    {{
        "靶点组合": "提取提到的所有靶点或受体(如 CD3, 4-1BB等)",
        "核心抗体构型": "提取其基础技术平台(如 scFv, VHH, T-cell engager, ADC等)",
        "特殊工程化保护": "重点提炼专利声称的创新点：是否有遮蔽域(Masking domain)、蛋白酶切割位点、亲和力减弱设计、空间位阻设计、双表位靶向等。若无，写'未提及特殊工程'。",
        "商业深度总结": "用不超过30个中文字符，概括其旨在治疗的核心适应症或解决的最大临床缺陷。"
    }}
    限定说明：绝对不要输出多余的解释性文本，只输出 JSON。
    摘要原文：
    {abstract_text}
    """
    try:
        return generate_ai_json_with_retry(model, prompt, ["靶点组合", "核心抗体构型", "特殊工程化保护", "商业深度总结"], debug_enabled)
    except Exception as e:
        err = str(e).replace('\n', ' ')
        return {"靶点组合": "解析报错", "核心抗体构型": "解析报错", "特殊工程化保护": "真实原因:", "商业深度总结": "请求过快被限流" if "429" in err else err[:35]}
