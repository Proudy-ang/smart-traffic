"""
模块② 闸机安全风控 — 智慧交通项目 人员B

功能：
  - 8 状态状态机（IDLE → APPROACHING → DECIDING → OPEN → CLOSING → IDLE）
  - 尾随判定（YOLO + PIR 双通道）
  - 人脸置信度梯度决策（PASS / PASS_WARN / ALERT_LOCK）
  - PIR 信号消抖（30ms）
  - 闯闸检测（IDLE/CLOSING 状态 PIR 意外触发）
  - Modbus 线圈控制（开闸/关闸）

依赖注入：
  - modbus_client: ModbusTCPClient 实例（控制 ADAM-4150 线圈 22）
  - sys_state: SystemStateManager 实例（触发异常事件）
"""

import time
import threading
from enum import Enum, auto

from config import (
    MAX_SINGLE_PASS_MS,
    FACE_TIMEOUT_MS,
    DEBOUNCE_MS,
    LOCKED_RECOVERY_SEC,
    GATE_COIL_ADDRESS,
)


# ============================================================
# 枚举定义
# ============================================================

class GateState(Enum):
    """闸机状态枚举。"""
    IDLE = auto()           # 闸机关闭，等待触发
    APPROACHING = auto()    # 有人靠近，等待 YOLO 结果
    DECIDING = auto()       # 判定中：放行 or 拦截
    OPEN = auto()           # 闸机开启，行人通过
    CLOSING = auto()        # 正常关闭
    TAILGATE = auto()       # 尾随确认
    ALERT = auto()          # 告警状态
    LOCKED = auto()         # 完全锁定


class AlertType(Enum):
    """告警类型枚举。"""
    ALERT_NONE = 0
    ALERT_STRANGER = 1              # 陌生人
    ALERT_FACE_TIMEOUT = 2           # 人脸校验超时（2s）
    ALERT_TAILGATE_YOLO = 3          # YOLO 多人（尾随）
    ALERT_TAILGATE_PIR = 4           # PIR 时长异常（尾随）
    ALERT_TAILGATE_CONFIRMED = 5     # 出口确认多人（多 PIR 场景预留）
    ALERT_FORCED_ENTRY = 6           # 闯闸（IDLE/CLOSING 状态 PIR 触发但无人脸通过）


class FaceDecision(Enum):
    """人脸判定结果。"""
    PASS = "PASS"
    PASS_WARN = "PASS_WARN"
    ALERT_LOCK = "ALERT_LOCK"


# ============================================================
# 闸机安全控制器
# ============================================================

class GateSafetyController:
    """闸机安全风控控制器 — 8 状态状态机。"""

    def __init__(self, modbus_client=None):
        """
        Args:
            modbus_client: ModbusTCPClient 实例（可为 None，无 Modbus 时闸机控制不可用）
        """
        self._modbus = modbus_client

        # 状态机当前状态
        self._state = GateState.IDLE
        self._prev_state = GateState.IDLE

        # 当前告警
        self._alert = AlertType.ALERT_NONE
        self._alert_context = ""

        # PIR 消抖
        self._pir_raw = False            # 原始 PIR 电平
        self._pir_stable = False         # 消抖后稳定电平
        self._pir_changed_at_ms = 0      # 电平变化时刻
        self._pir_high_start_ms = 0      # 当前 HIGH 持续起始时刻
        self._just_triggered = False     # 上升沿标记
        self._just_cleared = False       # 下降沿标记

        # 状态计时器
        self._state_entered_ms = 0       # 进入当前状态的时刻
        self._approaching_start_ms = 0   # APPROACHING 状态开始时刻

        # 最近一次人脸判定结果
        self._last_face_decision = None

        # LOCKED 状态恢复计时
        self._locked_start_ms = 0

        # 系统状态管理器引用
        self._sys_state = None

        # 线程安全
        self._lock = threading.Lock()

        # 当前 YOLO 数据缓存
        self._yolo_person_count = 0

        # 状态转换表：(当前状态, 触发条件) → 目标状态+动作
        # 由 _transition() 方法驱动

    def set_sys_state_manager(self, sys_state_mgr):
        """注入系统状态管理器。"""
        self._sys_state = sys_state_mgr

    # ---- 主更新入口 ----

    def update(self, yolo_data, pir_raw):
        """
        每个主循环周期调用一次。

        Args:
            yolo_data: YoloReader.read() 返回的 dict，或 None
            pir_raw: PIR 原始电平（bool），None 表示不更新 PIR
        """
        with self._lock:
            now_ms = int(time.time() * 1000)

            # 1. 更新 PIR 消抖
            if pir_raw is not None:
                self._update_pir(pir_raw, now_ms)

            # 2. 更新 YOLO 数据
            if yolo_data is not None:
                traffic = yolo_data.get("traffic", {})
                lanes = traffic.get("lanes", [])
                total_persons = sum(
                    lane.get("person_count", 0) for lane in lanes
                )
                self._yolo_person_count = total_persons

            # 3. 状态转换
            self._transition(now_ms, yolo_data)

            # 4. 清除已消费的 PIR 沿标志
            self._just_triggered = False
            self._just_cleared = False

    # ---- 公开接口 ----

    def get_status(self):
        """返回当前闸机状态信息。"""
        with self._lock:
            return {
                "state": self._state.name,
                "prev_state": self._prev_state.name,
                "alert": self._alert.name,
                "alert_context": self._alert_context,
                "pir_stable": self._pir_stable,
                "yolo_person_count": self._yolo_person_count,
                "last_face_decision": (
                    self._last_face_decision.value
                    if self._last_face_decision else None
                ),
                "state_duration_ms": int(time.time() * 1000) - self._state_entered_ms,
            }

    def reset(self):
        """手动复位：强制回到 IDLE 状态。"""
        with self._lock:
            self._state = GateState.IDLE
            self._prev_state = GateState.IDLE
            self._alert = AlertType.ALERT_NONE
            self._alert_context = ""
            self._last_face_decision = None

    # ================================================================
    # PIR 消抖
    # ================================================================

    def _update_pir(self, raw_level, now_ms):
        """PIR 信号消抖（30ms 窗口）。"""
        if raw_level != self._pir_raw:
            self._pir_raw = raw_level
            self._pir_changed_at_ms = now_ms

        # 信号变化后维持 DEBOUNCE_MS 才确认
        if now_ms - self._pir_changed_at_ms >= DEBOUNCE_MS:
            if self._pir_raw != self._pir_stable:
                old_stable = self._pir_stable
                self._pir_stable = self._pir_raw

                if self._pir_stable and not old_stable:
                    # 上升沿
                    self._just_triggered = True
                    self._pir_high_start_ms = now_ms
                elif not self._pir_stable and old_stable:
                    # 下降沿
                    self._just_cleared = True

    def _pir_high_duration_ms(self, now_ms):
        """返回 PIR 信号稳定 HIGH 的持续时长（ms）。"""
        if self._pir_stable and self._pir_high_start_ms > 0:
            return now_ms - self._pir_high_start_ms
        return 0

    # ================================================================
    # 状态转换逻辑
    # ================================================================

    def _transition(self, now_ms, yolo_data):
        """核心状态转换。"""
        state = self._state

        # --- IDLE：等待 PIR 触发 ---
        if state == GateState.IDLE:
            if self._just_triggered:
                # PIR 触发 → 进入 APPROACHING
                self._set_state(GateState.APPROACHING, now_ms)
                self._approaching_start_ms = now_ms
            # 闯闸检测：IDLE 状态没有正常触发流程但有持续 PIR
            # （由外部逻辑通过 APPROACHING 超时覆盖）

        # --- APPROACHING：等待 YOLO 人脸数据 ---
        elif state == GateState.APPROACHING:
            elapsed = now_ms - self._approaching_start_ms

            if yolo_data is not None:
                face = yolo_data.get("face", {})
                if face.get("detected") is not None:
                    # 收到人脸数据 → 进入 DECIDING
                    self._set_state(GateState.DECIDING, now_ms)
                    decision = self._assess_face(face)
                    self._last_face_decision = decision

                    if decision == FaceDecision.ALERT_LOCK:
                        self._trigger_alert(AlertType.ALERT_STRANGER, "人脸校验未通过", now_ms)
                    elif decision == FaceDecision.PASS or decision == FaceDecision.PASS_WARN:
                        self._set_state(GateState.OPEN, now_ms)
                        self._open_gate()
                    return

            # 超时 2s → 告警
            if elapsed >= FACE_TIMEOUT_MS:
                self._trigger_alert(AlertType.ALERT_FACE_TIMEOUT, "人脸校验超时", now_ms)
                return

        # --- OPEN：闸机开启，行人通过 ---
        elif state == GateState.OPEN:
            # 尾随检测 ①：YOLO ≥2 人
            if self._yolo_person_count >= 2:
                self._set_state(GateState.TAILGATE, now_ms)
                self._trigger_alert(AlertType.ALERT_TAILGATE_YOLO, f"YOLO检测到{self._yolo_person_count}人", now_ms)
                return

            # 尾随检测 ②：PIR 持续 >1.5s
            pir_dur = self._pir_high_duration_ms(now_ms)
            if pir_dur > MAX_SINGLE_PASS_MS:
                self._set_state(GateState.TAILGATE, now_ms)
                self._trigger_alert(AlertType.ALERT_TAILGATE_PIR, f"PIR时长异常({pir_dur}ms)", now_ms)
                return

            # 正常通过：PIR 下降沿 → 关闭
            if self._just_cleared:
                self._set_state(GateState.CLOSING, now_ms)
                self._close_gate()
                return

        # --- CLOSING：正常关闭 ---
        elif state == GateState.CLOSING:
            # 关闭完成，回到 IDLE
            self._set_state(GateState.IDLE, now_ms)
            # 闯闸检测：CLOSING 状态 PIR 意外触发
            if self._just_triggered:
                self._trigger_alert(AlertType.ALERT_FORCED_ENTRY, "CLOSING状态PIR意外触发", now_ms)
                return

        # --- LOCKED：超时自动恢复 ---
        elif state == GateState.LOCKED:
            elapsed = now_ms - self._locked_start_ms
            if elapsed >= LOCKED_RECOVERY_SEC * 1000:
                self._set_state(GateState.IDLE, now_ms)
                self._alert = AlertType.ALERT_NONE
                self._alert_context = ""

        # 闯闸检测：IDLE 或 CLOSING 状态 PIR 意外触发（独立于正常流程）
        if state in (GateState.IDLE, GateState.CLOSING):
            if self._just_triggered and self._state == state:
                # 仅在未发生其他状态转换时检测
                pass  # 闯闸在各自状态块内处理

    # ---- 手动 PIR 触发（测试用） ----
    def trigger_pir_rising(self, now_ms=None):
        """模拟 PIR 上升沿（供测试使用）。"""
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        with self._lock:
            self._pir_raw = True
            self._pir_changed_at_ms = now_ms - DEBOUNCE_MS - 1
            self._update_pir(True, now_ms)

    def trigger_pir_falling(self, now_ms=None):
        """模拟 PIR 下降沿（供测试使用）。"""
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        with self._lock:
            self._pir_raw = False
            self._pir_changed_at_ms = now_ms - DEBOUNCE_MS - 1
            self._update_pir(False, now_ms)

    # ================================================================
    # 人脸置信度梯度决策
    # ================================================================

    @staticmethod
    def _assess_face(face):
        """
        人脸置信度梯度决策（按规格 5.4）。

        Args:
            face: {"detected": bool, "person_id": int, "confidence": int, "is_stranger": bool}

        Returns:
            FaceDecision.PASS / PASS_WARN / ALERT_LOCK
        """
        if not face.get("detected") or face.get("person_id") == -3:
            return FaceDecision.ALERT_LOCK  # 没检测到人脸

        if face.get("is_stranger") or face.get("person_id") == -1:
            return FaceDecision.ALERT_LOCK  # 陌生人

        if face.get("person_id") == -2:
            return FaceDecision.ALERT_LOCK  # 检测到但无法识别

        conf = face.get("confidence", 0)
        if conf >= 80:
            return FaceDecision.PASS
        elif conf >= 50:
            return FaceDecision.PASS_WARN
        else:
            return FaceDecision.ALERT_LOCK

    # ================================================================
    # 告警与闸机控制
    # ================================================================

    def _trigger_alert(self, alert_type, context, now_ms):
        """触发告警，立即进入 ALERT → LOCKED。"""
        self._alert = alert_type
        self._alert_context = context
        self._set_state(GateState.ALERT, now_ms)

        # ALERT → LOCKED 同一周期内完成
        self._set_state(GateState.LOCKED, now_ms)
        self._locked_start_ms = now_ms
        self._close_gate()

        # 通知系统状态管理器
        if self._sys_state:
            from sys_state import SystemEvent
            self._sys_state.raise_event(SystemEvent.EVENT_GATE_INCONSISTENT)

    def _open_gate(self):
        """开闸：写线圈 22 = True。"""
        if self._modbus and self._modbus.is_connected():
            try:
                self._modbus.write_coil(GATE_COIL_ADDRESS, True)
            except (ConnectionError, TimeoutError):
                pass  # 优雅降级：Modbus 操作失败不影响状态机

    def _close_gate(self):
        """关闸：写线圈 22 = False。"""
        if self._modbus and self._modbus.is_connected():
            try:
                self._modbus.write_coil(GATE_COIL_ADDRESS, False)
            except (ConnectionError, TimeoutError):
                pass

    def _set_state(self, new_state, now_ms):
        """设置状态并记录时间戳。"""
        self._prev_state = self._state
        self._state = new_state
        self._state_entered_ms = now_ms

    # ================================================================
    # 属性（测试用）
    # ================================================================

    @property
    def state(self):
        return self._state

    @property
    def alert(self):
        return self._alert

    @property
    def pir_stable(self):
        return self._pir_stable
