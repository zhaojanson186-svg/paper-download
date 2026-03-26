import requests
import time

def requests_get_with_retry(url, headers=None, timeout=15, max_retries: int = 3):
    """requests.get 的简化版重试：遇到 429/5xx 做指数退避。"""
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code in (429, 403) or 500 <= r.status_code < 600:
                time.sleep(2 ** attempt + 1)
                continue
            return r
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt + 1)
    if last_err:
        raise last_err
    raise Exception(f"GET failed after {max_retries} retries: {url}")
