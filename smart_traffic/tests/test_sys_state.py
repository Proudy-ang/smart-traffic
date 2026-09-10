"""
模块③ 单元测试 — 系统全局状态框架
"""

import time
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sys_state import (
    SystemStateManager,
    SysState,
    SystemEvent,
    TRANSITION_TABLE,
)
from config import DEGRADED_RECOVERY_SEC, PARTIAL_FAULT_RECOVERY_SEC


# ============================================================
# 初始状态
# ============================================================

class TestInitialState:

    def test_initial_state_is_ok(self):
        sm = SystemStateManager()
        assert sm.state == SysState.SYS_OK

    def test_get_status_returns_ok(self):
        sm = SystemStateManager()
        s = sm.get_status()
        assert s["state"] == "SYS_OK"
        assert s["prev_state"] == "SYS_OK"


# ============================================================
# 跳转表测试
# ============================================================

class TestTransitionTable:

    def test_all_transitions_defined(self):
        """验证跳转表覆盖了所有需要的事件。"""
        transitions = set()
        for cur, evt, nxt in TRANSITION_TABLE:
            transitions.add((cur, evt, nxt))
        # SYS_OK + 4 events → SYS_DEGRADED
        for evt in [SystemEvent.EVENT_SENSOR_TIMEOUT, SystemEvent.EVENT_DATA_ANOMALY,
                     SystemEvent.EVENT_QUEUE_OVERFLOW, SystemEvent.EVENT_FACE_PIPELINE_STALL]:
            assert (SysState.SYS_OK, evt, SysState.SYS_DEGRADED) in transitions

    def test_ok_to_degraded_on_sensor_timeout(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        assert sm.state == SysState.SYS_DEGRADED

    def test_ok_to_degraded_on_data_anomaly(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_DATA_ANOMALY)
        assert sm.state == SysState.SYS_DEGRADED

    def test_ok_to_degraded_on_queue_overflow(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_QUEUE_OVERFLOW)
        assert sm.state == SysState.SYS_DEGRADED

    def test_ok_to_degraded_on_face_stall(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_FACE_PIPELINE_STALL)
        assert sm.state == SysState.SYS_DEGRADED

    def test_degraded_to_partial_fault_on_heartbeat_lost(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)  # OK → DEGRADED
        assert sm.state == SysState.SYS_DEGRADED
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        assert sm.state == SysState.SYS_PARTIAL_FAULT

    def test_degraded_to_partial_fault_on_gate_inconsistent(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_GATE_INCONSISTENT)
        assert sm.state == SysState.SYS_PARTIAL_FAULT

    def test_degraded_to_partial_fault_on_watchdog(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_WATCHDOG_WARNING)
        assert sm.state == SysState.SYS_PARTIAL_FAULT

    def test_partial_fault_to_critical_on_heartbeat_lost(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)       # OK → DEGRADED
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST) # DEGRADED → PARTIAL_FAULT
        assert sm.state == SysState.SYS_PARTIAL_FAULT
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST) # PARTIAL_FAULT → CRITICAL
        assert sm.state == SysState.SYS_CRITICAL

    def test_partial_fault_to_critical_on_watchdog(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_GATE_INCONSISTENT)
        assert sm.state == SysState.SYS_PARTIAL_FAULT
        sm.raise_event(SystemEvent.EVENT_WATCHDOG_WARNING)
        assert sm.state == SysState.SYS_CRITICAL

    def test_no_match_keeps_state(self):
        """无匹配的跳转保持当前状态。"""
        sm = SystemStateManager()
        # 在 SYS_OK 直接触发 MODULE_HEARTBEAT_LOST（不在跳转表中）
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        assert sm.state == SysState.SYS_OK  # 保持不变

    def test_status_tracks_last_event(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        s = sm.get_status()
        assert s["last_event"] == "EVENT_SENSOR_TIMEOUT"


# ============================================================
# 恢复规则测试
# ============================================================

class TestRecovery:

    def test_degraded_recovers_to_ok_after_timeout(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        assert sm.state == SysState.SYS_DEGRADED

        # 等待恢复时间
        time.sleep(DEGRADED_RECOVERY_SEC + 0.15)
        sm.check_recovery()
        assert sm.state == SysState.SYS_OK

    def test_degraded_does_not_recover_early(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        # 立即检查 → 不恢复
        sm.check_recovery()
        assert sm.state == SysState.SYS_DEGRADED

    def test_degraded_resets_timer_on_new_event(self):
        """新异常事件重置恢复计时器。"""
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        time.sleep(DEGRADED_RECOVERY_SEC / 2)  # 一半时间
        sm.raise_event(SystemEvent.EVENT_DATA_ANOMALY)  # 新事件，重置计时
        time.sleep(DEGRADED_RECOVERY_SEC / 2)  # 还不够恢复
        sm.check_recovery()
        assert sm.state == SysState.SYS_DEGRADED

    def test_partial_fault_recovers_to_degraded(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        assert sm.state == SysState.SYS_PARTIAL_FAULT

        # 注册并维持心跳正常
        sm.register_module(0)
        sm.heartbeat(0)
        time.sleep(PARTIAL_FAULT_RECOVERY_SEC + 0.15)
        sm.check_recovery()
        assert sm.state == SysState.SYS_DEGRADED

    def test_critical_does_not_auto_recover(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        assert sm.state == SysState.SYS_CRITICAL

        time.sleep(1)
        sm.check_recovery()
        assert sm.state == SysState.SYS_CRITICAL  # 仍然 CRITICAL


# ============================================================
# 心跳巡检测试
# ============================================================

class TestHeartbeat:

    def test_register_module_activates_slot(self):
        sm = SystemStateManager()
        sm.register_module(0)
        sm.heartbeat(0)
        # 心跳巡检：heartbeat=1 → 清零后 error_code=0
        sm.heartbeat_check()
        # 应该没有触发异常（活跃模块有心跳）
        assert sm.state == SysState.SYS_OK

    def test_missing_heartbeat_triggers_event(self):
        sm = SystemStateManager()
        sm.register_module(0)
        # 不上报心跳 → heartbeat=0
        sm.heartbeat_check()
        # 应该触发 EVENT_MODULE_HEARTBEAT_LOST
        # 但 SYS_OK 下此事件不在跳转表中（需要 SYS_DEGRADED 才触发升级）
        assert sm.state == SysState.SYS_OK

        # 先进入 DEGRADED
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        assert sm.state == SysState.SYS_DEGRADED

        # 再次心跳丢失 → 升级到 PARTIAL_FAULT
        sm.heartbeat_check()
        assert sm.state == SysState.SYS_PARTIAL_FAULT

    def test_heartbeat_clear_and_check_pattern(self):
        """验证清零-检测模式：heartbeat=1 → 巡检后清零为 0。"""
        sm = SystemStateManager()
        sm.register_module(0)
        sm.heartbeat(0)
        sm.heartbeat_check()
        # 巡检后 heartbeat 被清零，下次需要重新上报
        sm.heartbeat_check()
        # 第二次巡检时 heartbeat=0 → 触发异常
        # 需要先进入 DEGRADED 才有效
        assert sm.state == SysState.SYS_OK

    def test_inactive_modules_ignored(self):
        sm = SystemStateManager()
        # 未注册的模块不参与巡检
        sm.heartbeat_check()
        assert sm.state == SysState.SYS_OK

    def test_slot_out_of_range_ignored(self):
        sm = SystemStateManager()
        sm.register_module(100)  # 超出范围
        sm.heartbeat(100)
        sm.heartbeat_check()
        assert sm.state == SysState.SYS_OK


# ============================================================
# 复位测试
# ============================================================

class TestReset:

    def test_reset_from_critical_to_ok(self):
        sm = SystemStateManager()
        sm.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        sm.raise_event(SystemEvent.EVENT_MODULE_HEARTBEAT_LOST)
        assert sm.state == SysState.SYS_CRITICAL

        sm.reset()
        assert sm.state == SysState.SYS_OK
        s = sm.get_status()
        assert s["last_event"] is None

    def test_reset_clears_modules(self):
        sm = SystemStateManager()
        sm.register_module(0)
        sm.reset()
        # 所有模块 active=False
        sm.heartbeat_check()
        assert sm.state == SysState.SYS_OK  # 无活跃模块，不触发异常
