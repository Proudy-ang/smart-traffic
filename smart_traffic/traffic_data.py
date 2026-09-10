"""
模块① 交通态势数据算法 — 智慧交通项目 人员B

功能：
  - EWMA 指数加权移动平均平滑
  - 人流/车流密度计算
  - 数据质量评估（0-100）
  - Hold-Last-Value 容错

输入：YOLO11 JSON 中的 traffic.lanes 数据
输出：标准化交通态势数据（给人员A配时 和 人员E上云）
"""

import time
from collections import deque

from config import (
    EWMA_ALPHA,
    WAITING_AREA_PER_LANE,
    LANE_COUNT,
    SENSOR_TIMEOUT_SEC,
    DATA_ANOMALY_RATIO,
)


class TrafficDataProcessor:
    """交通态势数据处理器。"""

    def __init__(self):
        self.alpha = EWMA_ALPHA

        # 每车道状态: { "person_smooth": float, "vehicle_smooth": float }
        self._lanes = [
            {"person_smooth": 0.0, "vehicle_smooth": 0.0}
            for _ in range(LANE_COUNT)
        ]

        # 连续无效帧计数器
        self._consecutive_invalid = 0

        # 最近一次有效数据的时间戳（用于超时检测）
        self._last_valid_timestamp_ms = 0

        # 历史值（用于跳变检测）
        self._prev_person_counts = [0] * LANE_COUNT
        self._prev_vehicle_counts = [0] * LANE_COUNT

        # 数据质量当前分数
        self._quality_score = 100

        # 系统状态管理器引用（由主循环注入）
        self._sys_state = None

        # 上次输出的时间戳（用于控制输出频率）
        self._last_output_ms = 0

    def set_sys_state_manager(self, sys_state_mgr):
        """注入系统状态管理器，用于触发异常事件。"""
        self._sys_state = sys_state_mgr

    # ---- 核心接口 ----

    def update_from_yolo(self, yolo_data):
        """
        从 YOLO JSON 数据中提取并更新各车道交通状态。

        Args:
            yolo_data: YoloReader.read() 返回的 dict，或 None（数据不可用）
        """
        now_ms = int(time.time() * 1000)

        if yolo_data is None:
            # 读取失败 → 保持上次值，计数器递增
            self._consecutive_invalid += 1
            self._quality_score = self._assess_quality()
            self._check_timeout(now_ms)
            return

        traffic = yolo_data.get("traffic", {})
        lanes = traffic.get("lanes", [])

        if not lanes:
            self._consecutive_invalid += 1
            self._quality_score = self._assess_quality()
            self._check_timeout(now_ms)
            return

        all_valid = True
        for lane in lanes:
            lane_id = lane.get("lane_id", 0)
            if lane_id >= LANE_COUNT:
                continue

            person_count = lane.get("person_count", 0)
            vehicle_count = lane.get("vehicle_count", 0)
            data_valid = lane.get("data_valid", True)

            if data_valid:
                self._update_ewma(lane_id, person_count, vehicle_count)
                # 跳变检测
                self._check_anomaly(lane_id, person_count, vehicle_count)
            else:
                all_valid = False

        if all_valid:
            self._consecutive_invalid = 0
            self._last_valid_timestamp_ms = now_ms
        else:
            self._consecutive_invalid += 1
            self._check_timeout(now_ms)

        # 更新质量分数
        self._quality_score = self._assess_quality()

    def get_density(self, lane_id):
        """
        获取指定车道的密度和流率指标。

        Returns:
            dict: {
                "person_density": float,    # 排队密度（人/m²）
                "vehicle_density": float,   # 排队密度（辆/m²）
                "person_flow_rate": float,  # 人流量（人/分钟）
                "vehicle_flow_rate": float, # 车流量（辆/分钟）
                "person_smooth": float,
                "vehicle_smooth": float,
            }
        """
        lane = self._lanes[lane_id]
        ps = lane["person_smooth"]
        vs = lane["vehicle_smooth"]

        return {
            "person_density": ps / WAITING_AREA_PER_LANE,
            "vehicle_density": vs / WAITING_AREA_PER_LANE,
            "person_flow_rate": ps * 60,
            "vehicle_flow_rate": vs * 60,
            "person_smooth": ps,
            "vehicle_smooth": vs,
        }

    def get_quality(self):
        """返回当前数据质量分数（0-100）。"""
        return self._quality_score

    def get_output(self):
        """
        返回标准化交通态势输出结构。

        Returns:
            dict 符合规格 4.5 输出结构
        """
        now_ms = int(time.time() * 1000)
        lanes_output = []
        for lane_id in range(LANE_COUNT):
            density = self.get_density(lane_id)
            lanes_output.append({
                "lane_id": lane_id,
                "person_density": round(density["person_density"], 4),
                "vehicle_density": round(density["vehicle_density"], 4),
                "person_flow_rate": round(density["person_flow_rate"], 2),
                "vehicle_flow_rate": round(density["vehicle_flow_rate"], 2),
                "data_quality": self._quality_score,
            })

        return {
            "timestamp_ms": now_ms,
            "lanes": lanes_output,
        }

    # ---- 内部方法 ----

    def _update_ewma(self, lane_id, person_count, vehicle_count):
        """EWMA 指数加权移动平均更新。"""
        lane = self._lanes[lane_id]
        a = self.alpha
        lane["person_smooth"] = a * person_count + (1 - a) * lane["person_smooth"]
        lane["vehicle_smooth"] = a * vehicle_count + (1 - a) * lane["vehicle_smooth"]

    def _assess_quality(self):
        """根据连续无效帧数评估数据质量（0-100）。"""
        c = self._consecutive_invalid
        if c == 0:
            return 100
        elif c < 5:
            return 100 - c * 15
        elif c < 30:
            return 30
        else:
            return 0

    def _check_timeout(self, now_ms):
        """检查传感器数据超时，触发异常事件。"""
        if self._consecutive_invalid >= SENSOR_TIMEOUT_SEC:
            if self._sys_state:
                from sys_state import SystemEvent
                self._sys_state.raise_event(SystemEvent.EVENT_SENSOR_TIMEOUT)

    def _check_anomaly(self, lane_id, person_count, vehicle_count):
        """检测单次计数跳变异常。"""
        prev_p = self._prev_person_counts[lane_id]
        prev_v = self._prev_vehicle_counts[lane_id]

        p_jump = (
            prev_p > 0
            and abs(person_count - prev_p) > prev_p * DATA_ANOMALY_RATIO
        )
        v_jump = (
            prev_v > 0
            and abs(vehicle_count - prev_v) > prev_v * DATA_ANOMALY_RATIO
        )

        if p_jump or v_jump:
            if self._sys_state:
                from sys_state import SystemEvent
                self._sys_state.raise_event(SystemEvent.EVENT_DATA_ANOMALY)

        self._prev_person_counts[lane_id] = person_count
        self._prev_vehicle_counts[lane_id] = vehicle_count

    def reset(self):
        """重置所有内部状态（测试用）。"""
        self._lanes = [
            {"person_smooth": 0.0, "vehicle_smooth": 0.0}
            for _ in range(LANE_COUNT)
        ]
        self._consecutive_invalid = 0
        self._last_valid_timestamp_ms = 0
        self._quality_score = 100
        self._prev_person_counts = [0] * LANE_COUNT
        self._prev_vehicle_counts = [0] * LANE_COUNT
