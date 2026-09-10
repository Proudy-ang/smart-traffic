"""
模块② 单元测试 — 闸机安全风控
"""

import time
import pytest
import sys
import os
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gate_safety import (
    GateSafetyController,
    GateState,
    AlertType,
    FaceDecision,
)
from config import (
    FACE_TIMEOUT_MS, MAX_SINGLE_PASS_MS, LOCKED_RECOVERY_SEC,
    GATE_COIL_ADDRESS,
)


# ============================================================
# Mock & 辅助
# ============================================================

def _make_mock_modbus(connected=True):
    m = Mock()
    m.is_connected.return_value = connected
    m.write_coil.return_value = True
    return m


def _make_yolo_with_face(detected=True, person_id=1, confidence=92,
                          is_stranger=False, person_name="测试"):
    return {
        "timestamp_ms": int(time.time() * 1000), "frame_id": 1,
        "traffic": {"lanes": [
            {"lane_id": 0, "person_count": 1, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]},
        "face": {"detected": detected, "person_id": person_id,
                 "person_name": person_name, "confidence": confidence,
                 "is_stranger": is_stranger},
        "error": None,
    }


def _make_yolo_with_persons(count):
    return {
        "timestamp_ms": int(time.time() * 1000), "frame_id": 1,
        "traffic": {"lanes": [
            {"lane_id": 0, "person_count": count, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]},
        "face": {"detected": False, "person_id": -3, "person_name": "", "confidence": 0, "is_stranger": False},
        "error": None,
    }


# 一步：触发 PIR + update（驱动 IDLE → APPROACHING）
def _trigger(gate, now_ms=None):
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    gate.trigger_pir_rising(now_ms)
    gate.update(None, True)


# 一步：下降沿 + update（驱动 OPEN → CLOSING）
def _fall(gate, now_ms=None):
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    gate.trigger_pir_falling(now_ms)
    gate.update(None, False)


# ============================================================
# 初始状态
# ============================================================

class TestInitialState:

    def test_initial_state_is_idle(self):
        gate = GateSafetyController()
        assert gate.state == GateState.IDLE
        assert gate.alert == AlertType.ALERT_NONE

    def test_initial_pir_stable_is_false(self):
        gate = GateSafetyController()
        assert gate.pir_stable == False

    def test_get_status_structure(self):
        s = GateSafetyController().get_status()
        assert s["state"] == "IDLE"
        assert s["alert"] == "ALERT_NONE"
        assert "state_duration_ms" in s


# ============================================================
# 正常流程
# ============================================================

class TestNormalFlow:

    def test_idle_to_approaching(self):
        gate = GateSafetyController()
        _trigger(gate)
        assert gate.state == GateState.APPROACHING

    def test_approaching_to_open_on_face_pass(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN

    def test_open_to_closing_on_pir_fall(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN
        _fall(gate)
        assert gate.state == GateState.CLOSING

    def test_closing_to_idle(self):
        gate = GateSafetyController()
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=85), True)
        _fall(gate)
        assert gate.state == GateState.CLOSING
        gate.update(_make_yolo_with_face(), False)
        assert gate.state == GateState.IDLE


# ============================================================
# 人脸置信度梯度决策
# ============================================================

class TestFaceDecision:

    def test_high_confidence_passes(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN

    def test_mid_confidence_passes_with_warning(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=55), True)
        assert gate.state == GateState.OPEN

    def test_low_confidence_alerts(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=30), True)
        assert gate.state == GateState.LOCKED
        assert gate.alert == AlertType.ALERT_STRANGER

    def test_stranger_alerts(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-1, is_stranger=True, confidence=0), True)
        assert gate.state == GateState.LOCKED

    def test_no_face_alerts(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(detected=False, person_id=-3, confidence=0), True)
        assert gate.state == GateState.LOCKED

    def test_unrecognizable_alerts(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-2, confidence=0), True)
        assert gate.state == GateState.LOCKED

    def test_exact_80_passes(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=80), True)
        assert gate.state == GateState.OPEN

    def test_exact_50_passes_warn(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=50), True)
        assert gate.state == GateState.OPEN

    def test_49_alerts(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=49), True)
        assert gate.state == GateState.LOCKED


# ============================================================
# 尾随检测
# ============================================================

class TestTailgateDetection:

    def test_yolo_two_persons_in_open_triggers_tailgate(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN
        gate.update(_make_yolo_with_persons(2), True)
        assert gate.state == GateState.LOCKED
        assert gate.alert == AlertType.ALERT_TAILGATE_YOLO

    def test_pir_over_max_duration_triggers_tailgate(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN
        time.sleep(MAX_SINGLE_PASS_MS / 1000 + 0.15)
        gate.update(_make_yolo_with_persons(1), True)
        assert gate.state == GateState.LOCKED
        assert gate.alert == AlertType.ALERT_TAILGATE_PIR


# ============================================================
# 超时
# ============================================================

class TestTimeout:

    def test_approaching_face_timeout(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        assert gate.state == GateState.APPROACHING
        time.sleep(FACE_TIMEOUT_MS / 1000 + 0.15)
        gate.update(None, True)
        assert gate.state == GateState.LOCKED
        assert gate.alert == AlertType.ALERT_FACE_TIMEOUT

    def test_locked_manual_reset(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-1, is_stranger=True, confidence=0), True)
        assert gate.state == GateState.LOCKED
        gate.reset()
        assert gate.state == GateState.IDLE
        assert gate.alert == AlertType.ALERT_NONE

    def test_locked_auto_recovery(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-1, is_stranger=True, confidence=0), True)
        assert gate.state == GateState.LOCKED
        time.sleep(LOCKED_RECOVERY_SEC + 0.15)
        gate.update(None, False)
        assert gate.state == GateState.IDLE


# ============================================================
# 闯闸检测
# ============================================================

class TestForcedEntry:

    def test_closing_state_pir_trigger_is_forced_entry(self):
        gate = GateSafetyController(_make_mock_modbus())
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN
        _fall(gate)
        assert gate.state == GateState.CLOSING
        # CLOSING 状态 PIR 再次触发
        _trigger(gate)
        assert gate.state == GateState.LOCKED
        assert gate.alert == AlertType.ALERT_FORCED_ENTRY


# ============================================================
# Modbus 控制
# ============================================================

class TestModbusControl:

    def test_open_gate_writes_coil_true(self):
        modbus = _make_mock_modbus()
        gate = GateSafetyController(modbus)
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        modbus.write_coil.assert_any_call(GATE_COIL_ADDRESS, True)

    def test_close_gate_on_closing(self):
        modbus = _make_mock_modbus()
        gate = GateSafetyController(modbus)
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        _fall(gate)
        modbus.write_coil.assert_any_call(GATE_COIL_ADDRESS, False)

    def test_alert_triggers_close_gate(self):
        modbus = _make_mock_modbus()
        gate = GateSafetyController(modbus)
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-1, is_stranger=True, confidence=0), True)
        modbus.write_coil.assert_any_call(GATE_COIL_ADDRESS, False)

    def test_disconnected_modbus_state_machine_still_works(self):
        gate = GateSafetyController(_make_mock_modbus(connected=False))
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN

    def test_no_modbus_client_does_not_crash(self):
        gate = GateSafetyController(None)
        _trigger(gate)
        gate.update(_make_yolo_with_face(confidence=92), True)
        assert gate.state == GateState.OPEN


# ============================================================
# PIR 消抖
# ============================================================

class TestPIRDebounce:

    def test_rising_edge_detected(self):
        gate = GateSafetyController()
        gate.trigger_pir_rising()
        assert gate.pir_stable == True

    def test_falling_edge_detected(self):
        gate = GateSafetyController()
        gate.trigger_pir_rising()
        gate.trigger_pir_falling()
        assert gate.pir_stable == False

    def test_reset_from_locked(self):
        gate = GateSafetyController()
        _trigger(gate)
        gate.update(_make_yolo_with_face(person_id=-1, is_stranger=True, confidence=0), True)
        assert gate.state == GateState.LOCKED
        gate.reset()
        assert gate.state == GateState.IDLE
        assert gate.alert == AlertType.ALERT_NONE
