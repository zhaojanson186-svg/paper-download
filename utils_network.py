# utils_network.py
import requests
import time

def requests_get_with_retry(url, headers=None, timeout=15, max_retries: int = 3):
    last_err = None
    last_r = None # 记录最后一次的响应对象
    
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            last_r = r
            
            # 遇到 429/403/5xx 做指数退避重试
            if r.status_code in (429, 403) or 500 <= r.status_code < 600:
                time.sleep(2 ** attempt + 1)
                continue
            return r
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt + 1)
            
    # 核心修复：就算重试耗尽，只要拿到过响应（比如连续4次403），就把响应传出去让外部记录
    if last_r is not None:
        return last_r
        
    if last_err:
        raise last_err
        
    raise Exception(f"GET failed completely: {url}")
