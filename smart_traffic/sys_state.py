"""
模块③ 系统全局状态框架 — 智慧交通项目 人员B

功能：
  - 4 级系统状态（SYS_OK / SYS_DEGRADED / SYS_PARTIAL_FAULT / SYS_CRITICAL）
  - 事件驱动跳转表（立即升级）
  - 定时器自动恢复（延迟降级）
  - 心跳巡检（8 模块槽位，清零检测模式）
"""

import time
import threading
from enum import Enum, auto

from config import (
    SENSOR_TIMEOUT_SEC,
    FACE_STALL_SEC,
    DEGRADED_RECOVERY_SEC,
    PARTIAL_FAULT_RECOVERY_SEC,
    MAX_MODULES,
)


# ============================================================
# 枚举定义
# ============================================================

class SysState(Enum):
    """4 级系统状态。"""
    SYS_OK = 1
    SYS_DEGRADED = 2
    SYS_PARTIAL_FAULT = 3
    SYS_CRITICAL = 4


class SystemEvent(Enum):
    """异常事件类型。"""
    EVENT_SENSOR_TIMEOUT = 1         # 传感器 >30s 无数据
    EVENT_DATA_ANOMALY = 2           # 单次计数跳变超阈值
    EVENT_QUEUE_OVERFLOW = 3         # 推送队列满
    EVENT_FACE_PIPELINE_STALL = 4    # 人脸结果 >10s 未更新
    EVENT_MODULE_HEARTBEAT_LOST = 5  # 某模块连续 3 周期无心跳
    EVENT_GATE_INCONSISTENT = 6      # 闸机命令 vs 实际状态不一致
    EVENT_WATCHDOG_WARNING = 7       # 看门狗预警


# ============================================================
# 跳转表：事件驱动升级（立即响应）
# ============================================================

TRANSITION_TABLE = [
    # (当前状态, 事件, 目标状态)
    (SysState.SYS_OK,             SystemEvent.EVENT_SENSOR_TIMEOUT,        SysState.SYS_DEGRADED),
    (SysState.SYS_OK,             SystemEvent.EVENT_DATA_ANOMALY,          SysState.SYS_DEGRADED),
    (SysState.SYS_OK,             SystemEvent.EVENT_QUEUE_OVERFLOW,        SysState.SYS_DEGRADED),
    (SysState.SYS_OK,             SystemEvent.EVENT_FACE_PIPELINE_STALL,   SysState.SYS_DEGRADED),

    (SysState.SYS_DEGRADED,       SystemEvent.EVENT_MODULE_HEARTBEAT_LOST, SysState.SYS_PARTIAL_FAULT),
    (SysState.SYS_DEGRADED,       SystemEvent.EVENT_GATE_INCONSISTENT,     SysState.SYS_PARTIAL_FAULT),
    (SysState.SYS_DEGRADED,       SystemEvent.EVENT_WATCHDOG_WARNING,      SysState.SYS_PARTIAL_FAULT),

    (SysState.SYS_PARTIAL_FAULT,  SystemEvent.EVENT_MODULE_HEARTBEAT_LOST, SysState.SYS_CRITICAL),
    (SysState.SYS_PARTIAL_FAULT,  SystemEvent.EVENT_WATCHDOG_WARNING,      SysState.SYS_CRITICAL),
]


# ============================================================
# 系统状态管理器
# ============================================================

class SystemStateManager:
    """系统全局状态管理器。"""

    def __init__(self):
        # 当前系统状态
        self._state = SysState.SYS_OK
        self._prev_state = SysState.SYS_OK

        # 最近一次异常事件
        self._last_event = None
        self._last_event_time_ms = 0

        # 恢复计时器
        self._last_anomaly_time_ms = 0        # 最近异常发生时刻
        self._ok_since_ms = 0                 # 状态正常持续起始时刻

        # 心跳槽位（8 个模块）
        self._modules = [
            {"heartbeat": 0, "error_code": 0, "active": False}
            for _ in range(MAX_MODULES)
        ]

        # 线程安全（RLock 允许同一线程重入，heartbeat_check 内会调用 raise_event）
        self._lock = threading.RLock()

    # ---- 系统状态接口 ----

    def get_status(self):
        """返回当前系统状态摘要。"""
        with self._lock:
            return {
                "state": self._state.name,
                "prev_state": self._prev_state.name,
                "last_event": self._last_event.name if self._last_event else None,
            }

    @property
    def state(self):
        return self._state

    # ---- 事件触发 ----

    def raise_event(self, event):
        """
        触发异常事件，执行跳转表查找并可能升级系统状态。

        Args:
            event: SystemEvent 枚举值
        """
        with self._lock:
            now_ms = int(time.time() * 1000)

            # 查跳转表
            new_state = self._lookup_transition(self._state, event)

            if new_state != self._state:
                self._prev_state = self._state
                self._state = new_state
                self._last_event = event
                self._last_event_time_ms = now_ms

            # 记录异常时刻（用于恢复计时）
            self._last_anomaly_time_ms = now_ms

    # ---- 心跳巡检 ----

    def register_module(self, slot_id):
        """注册模块到心跳槽位。"""
        if 0 <= slot_id < MAX_MODULES:
            self._modules[slot_id]["active"] = True
            self._modules[slot_id]["heartbeat"] = 0
            self._modules[slot_id]["error_code"] = 0

    def heartbeat(self, slot_id):
        """模块心跳上报（每个主循环周期调用一次）。"""
        if 0 <= slot_id < MAX_MODULES and self._modules[slot_id]["active"]:
            self._modules[slot_id]["heartbeat"] = 1
            self._modules[slot_id]["error_code"] = 0

    def heartbeat_check(self):
        """
        心跳巡检（每 3 个主循环周期执行一次）。

        逻辑：检查所有活跃槽位，heartbeat=0 表示模块在上次清零后未上报心跳，
        触发 EVENT_MODULE_HEARTBEAT_LOST。检查完毕后将所有 heartbeat 清零。
        """
        with self._lock:
            for m in self._modules:
                if not m["active"]:
                    continue
                if m["heartbeat"] == 0:
                    m["error_code"] = 1
                    self.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
                else:
                    m["heartbeat"] = 0  # 清零，等下一次更新
                    m["error_code"] = 0

    # ---- 恢复逻辑 ----

    def check_recovery(self):
        """
        检查恢复条件（每个主循环周期调用一次）。

        恢复规则：
          SYS_DEGRADED    → 连续 5s 无新异常 → SYS_OK
          SYS_PARTIAL_FAULT → 所有模块连续 10s 正常 → SYS_DEGRADED
          SYS_CRITICAL     → 不自动恢复，等外部复位
        """
        with self._lock:
            now_ms = int(time.time() * 1000)

            if self._state == SysState.SYS_DEGRADED:
                elapsed = now_ms - self._last_anomaly_time_ms
                if elapsed >= DEGRADED_RECOVERY_SEC * 1000:
                    self._prev_state = self._state
                    self._state = SysState.SYS_OK
                    self._last_anomaly_time_ms = 0

            elif self._state == SysState.SYS_PARTIAL_FAULT:
                # 所有活跃模块心跳正常且连续 10s
                all_ok = all(
                    not m["active"] or m["error_code"] == 0
                    for m in self._modules
                )
                if all_ok:
                    elapsed = now_ms - self._last_anomaly_time_ms
                    if elapsed >= PARTIAL_FAULT_RECOVERY_SEC * 1000:
                        self._prev_state = self._state
                        self._state = SysState.SYS_DEGRADED
                        self._last_anomaly_time_ms = 0

            # SYS_CRITICAL: 不自动恢复

    # ---- 外部复位 ----

    def reset(self):
        """外部复位：强制回到 SYS_OK。"""
        with self._lock:
            self._prev_state = self._state
            self._state = SysState.SYS_OK
            self._last_event = None
            self._last_event_time_ms = 0
            self._last_anomaly_time_ms = 0
            for m in self._modules:
                m["heartbeat"] = 0
                m["error_code"] = 0
                m["active"] = False

    # ---- 内部方法 ----

    @staticmethod
    def _lookup_transition(current, event):
        """查跳转表，返回目标状态。无匹配则保持当前状态。"""
        for cur, evt, nxt in TRANSITION_TABLE:
            if cur == current and evt == event:
                return nxt
        return current
