"""
日志系统 — 智慧交通项目 人员B
环形缓冲区日志（deque, maxlen=100），函数式接口。
"""

import time
from collections import deque

from config import MAX_LOG_ENTRIES

# 全局环形缓冲区
_log_buffer = deque(maxlen=MAX_LOG_ENTRIES)


def write_log(level, module, error_code, context=""):
    """
    写入一条日志到环形缓冲区。

    Args:
        level: "INFO" | "WARNING" | "ERROR" | "CRITICAL"
        module: "traffic" | "gate" | "system" | "modbus" | "output"
        error_code: 错误码（int），0 表示正常
        context: 附加上下文描述（str）
    """
    entry = {
        "timestamp_ms": int(time.time() * 1000),
        "level": level,
        "module": module,
        "error_code": error_code,
        "context": context,
    }
    _log_buffer.append(entry)

    # 同时输出到控制台
    print(f"[{entry['level']}][{module}] code={error_code} {context}")


def get_logs(count=None):
    """
    获取最近的日志条目。

    Args:
        count: 返回条数（None=全部，最多 MAX_LOG_ENTRIES）

    Returns:
        list[dict]: 日志列表（从旧到新）
    """
    logs = list(_log_buffer)
    if count is not None and count < len(logs):
        return logs[-count:]
    return logs


def clear_logs():
    """清空日志缓冲区。"""
    _log_buffer.clear()
