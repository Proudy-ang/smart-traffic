"""
数据输出接口 — 智慧交通项目 人员B
将处理后的交通态势数据和系统状态输出给人员 A（配时）和人员 D/E（设备控制 / 云平台）。

输出频率：
  - 1Hz：给人员 A（红绿灯配时用）
  - 5s 周期：给人员 E（云平台展示）
"""

import json
import os
import time
from collections import deque

from config import MAX_LOG_ENTRIES, TRAFFIC_DATA_OUTPUT_PATH


class DataOutput:
    """标准化数据输出管理器。"""

    def __init__(self):
        # 最近一次 1Hz 交通数据
        self._traffic_1hz = None
        self._traffic_1hz_time_ms = 0

        # 最近一次 5s 综合数据
        self._traffic_5s = None
        self._gate_status_5s = None
        self._system_status_5s = None
        self._5s_time_ms = 0

        # 输出历史缓存（deque，方便调试）
        self._output_history = deque(maxlen=MAX_LOG_ENTRIES)

        # 确保输出目录存在
        os.makedirs(os.path.dirname(TRAFFIC_DATA_OUTPUT_PATH) or ".", exist_ok=True)

    # ---- 推送接口（由主循环调用） ----

    def push_traffic_1hz(self, traffic_data):
        """
        缓存 1Hz 交通态势数据（给人员 A 配时用），同时写文件供 A 的 traffic_light.py 读取。

        Args:
            traffic_data: TrafficDataProcessor.get_output() 返回的 dict
        """
        self._traffic_1hz = traffic_data
        self._traffic_1hz_time_ms = int(time.time() * 1000)

        # 原子写入文件（A 的 traffic_light.py 读取）
        self._write_atomic(TRAFFIC_DATA_OUTPUT_PATH, traffic_data)

        # 记录到历史
        self._output_history.append({
            "type": "traffic_1hz",
            "timestamp_ms": self._traffic_1hz_time_ms,
            "data": traffic_data,
        })

    def push_traffic_5s(self, traffic_data, gate_status, system_status):
        """
        缓存 5s 周期综合数据（给人员 E 上云展示）。

        Args:
            traffic_data: TrafficDataProcessor.get_output() 返回的 dict
            gate_status: GateSafetyController.get_status() 返回的 dict
            system_status: SystemStateManager.get_status() 返回的 dict
        """
        self._traffic_5s = traffic_data
        self._gate_status_5s = gate_status
        self._system_status_5s = system_status
        self._5s_time_ms = int(time.time() * 1000)

        # 记录到历史
        self._output_history.append({
            "type": "traffic_5s",
            "timestamp_ms": self._5s_time_ms,
            "data": {
                "traffic": traffic_data,
                "gate": gate_status,
                "system": system_status,
            },
        })

    # ---- 获取接口（供外部读取） ----

    def get_1hz_output(self):
        """获取最近一次 1Hz 交通数据（JSON 字符串）。"""
        if self._traffic_1hz is None:
            return None
        return json.dumps(self._traffic_1hz, ensure_ascii=False)

    def get_5s_output(self):
        """获取最近一次 5s 综合数据（JSON 字符串）。"""
        if self._traffic_5s is None:
            return None
        payload = {
            "timestamp_ms": self._5s_time_ms,
            "traffic": self._traffic_5s,
            "gate": self._gate_status_5s,
            "system": self._system_status_5s,
        }
        return json.dumps(payload, ensure_ascii=False)

    def get_recent_history(self, count=10):
        """获取最近 N 条输出历史记录。"""
        logs = list(self._output_history)
        if count < len(logs):
            return logs[-count:]
        return logs

    def flush(self):
        """清空所有缓存数据。"""
        self._traffic_1hz = None
        self._traffic_5s = None
        self._gate_status_5s = None
        self._system_status_5s = None
        self._output_history.clear()

    # ---- 内部 ----

    @staticmethod
    def _write_atomic(path, data):
        """原子写入 JSON：先写 .tmp 再 rename（防止 A 读到半截文件）。"""
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except (OSError, IOError):
            pass
