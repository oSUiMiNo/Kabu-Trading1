"""
指数バックオフリトライユーティリティ
"""

import time
import random


def retry_with_backoff(fn, max_retries: int = 5, base_delay: float = 2.0):
    """
    指数バックオフ付きリトライ。
    失敗時は 2秒 → 4秒 → 8秒 → 16秒 → 32秒 で再試行。
    全て失敗したら None を返す。
    """
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"    [エラー] {max_retries}回リトライ後も失敗: {e}")
                return None
            delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
            print(f"    [リトライ] {attempt + 1}/{max_retries} 失敗、{delay:.1f}秒後に再試行: {e}")
            time.sleep(delay)
    return None
