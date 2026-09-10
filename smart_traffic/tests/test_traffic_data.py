"""
模块① 单元测试 — 交通态势数据算法
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from traffic_data import TrafficDataProcessor
from config import EWMA_ALPHA, WAITING_AREA_PER_LANE


# ============================================================
# 辅助函数：构造模拟 YOLO 数据
# ============================================================

def _make_yolo_data(lanes_override=None):
    """构造最小合法的 YOLO 数据。"""
    lanes = lanes_override or [
        {"lane_id": 0, "person_count": 3, "vehicle_count": 1, "data_valid": True},
        {"lane_id": 1, "person_count": 0, "vehicle_count": 2, "data_valid": True},
    ]
    return {
        "timestamp_ms": 1000000,
        "frame_id": 100,
        "traffic": {"lanes": lanes},
        "face": {"detected": True, "person_id": 1, "person_name": "测试", "confidence": 90, "is_stranger": False},
        "error": None,
    }


# ============================================================
# EWMA 平滑测试
# ============================================================

class TestEWMA:
    """EWMA 指数加权移动平均算法测试。"""

    def test_initial_values_are_zero(self):
        tp = TrafficDataProcessor()
        d = tp.get_density(0)
        assert d["person_smooth"] == 0.0
        assert d["vehicle_smooth"] == 0.0

    def test_single_update_converges_toward_raw(self):
        tp = TrafficDataProcessor()
        data = _make_yolo_data([
            {"lane_id": 0, "person_count": 10, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ])
        tp.update_from_yolo(data)
        lane = tp.get_density(0)
        # 首次：smooth = α×10 + (1-α)×0 = 10α
        expected = EWMA_ALPHA * 10
        assert lane["person_smooth"] == pytest.approx(expected, rel=1e-6)

    def test_multiple_updates_converge(self):
        tp = TrafficDataProcessor()
        a = EWMA_ALPHA
        expected = 0.0
        for i in range(10):
            data = _make_yolo_data([
                {"lane_id": 0, "person_count": 5, "vehicle_count": 0, "data_valid": True},
                {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
            ])
            tp.update_from_yolo(data)
            expected = a * 5 + (1 - a) * expected

        lane = tp.get_density(0)
        assert lane["person_smooth"] == pytest.approx(expected, rel=1e-6)

    def test_invalid_data_skipped_preserves_value(self):
        """无效数据应跳过，保持上次平滑值。"""
        tp = TrafficDataProcessor()
        # 先给有效数据
        tp.update_from_yolo(_make_yolo_data([
            {"lane_id": 0, "person_count": 8, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]))
        before = tp.get_density(0)["person_smooth"]

        # 再给无效数据
        tp.update_from_yolo(_make_yolo_data([
            {"lane_id": 0, "person_count": 999, "vehicle_count": 0, "data_valid": False},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]))
        after = tp.get_density(0)["person_smooth"]

        assert after == pytest.approx(before, rel=1e-6)
        assert after != pytest.approx(EWMA_ALPHA * 999 + (1 - EWMA_ALPHA) * before)

    def test_none_yolo_data_preserves_value(self):
        """YOLO 数据为 None 时保持上次值。"""
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data([
            {"lane_id": 0, "person_count": 4, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]))
        before = tp.get_density(0)["person_smooth"]

        tp.update_from_yolo(None)
        after = tp.get_density(0)["person_smooth"]
        assert after == pytest.approx(before, rel=1e-6)


# ============================================================
# 密度和流率计算测试
# ============================================================

class TestDensity:
    """密度和流率换算测试。"""

    def test_person_density_formula(self):
        tp = TrafficDataProcessor()
        data = _make_yolo_data([
            {"lane_id": 0, "person_count": 6, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ])
        tp.update_from_yolo(data)
        d = tp.get_density(0)
        expected_density = d["person_smooth"] / WAITING_AREA_PER_LANE
        assert d["person_density"] == pytest.approx(expected_density, rel=1e-6)

    def test_flow_rate_is_smooth_times_60(self):
        tp = TrafficDataProcessor()
        data = _make_yolo_data([
            {"lane_id": 0, "person_count": 3, "vehicle_count": 2, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ])
        tp.update_from_yolo(data)
        d = tp.get_density(0)
        assert d["person_flow_rate"] == pytest.approx(d["person_smooth"] * 60, rel=1e-6)
        assert d["vehicle_flow_rate"] == pytest.approx(d["vehicle_smooth"] * 60, rel=1e-6)

    def test_both_lanes_independent(self):
        """两个车道独立更新，互不影响。"""
        tp = TrafficDataProcessor()
        data = _make_yolo_data([
            {"lane_id": 0, "person_count": 10, "vehicle_count": 0, "data_valid": True},
            {"lane_id": 1, "person_count": 2, "vehicle_count": 0, "data_valid": True},
        ])
        tp.update_from_yolo(data)
        d0 = tp.get_density(0)
        d1 = tp.get_density(1)
        assert d0["person_smooth"] > d1["person_smooth"]


# ============================================================
# 数据质量评估测试
# ============================================================

class TestDataQuality:
    """数据质量评分测试。"""

    def test_quality_starts_at_100(self):
        tp = TrafficDataProcessor()
        assert tp.get_quality() == 100

    def test_quality_after_valid_data_stays_100(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data())
        assert tp.get_quality() == 100

    def test_quality_drops_with_invalid_frames(self):
        """连续无效帧 <5：每帧 -15 分。"""
        tp = TrafficDataProcessor()
        # 3 帧无效
        for _ in range(3):
            tp.update_from_yolo(None)
        assert tp.get_quality() == 100 - 3 * 15  # 55

    def test_quality_floor_at_30_for_moderate_invalid(self):
        """5 ≤ 连续无效帧 <30：固定 30 分。"""
        tp = TrafficDataProcessor()
        for _ in range(10):
            tp.update_from_yolo(None)
        assert tp.get_quality() == 30

    def test_quality_zero_after_30_invalid(self):
        """≥30 帧连续无效：质量归零。"""
        tp = TrafficDataProcessor()
        for _ in range(30):
            tp.update_from_yolo(None)
        assert tp.get_quality() == 0

    def test_quality_recovers_after_valid_data(self):
        """收到有效数据后质量立即恢复到 100。"""
        tp = TrafficDataProcessor()
        for _ in range(5):
            tp.update_from_yolo(None)
        assert tp.get_quality() == 30

        tp.update_from_yolo(_make_yolo_data())
        assert tp.get_quality() == 100


# ============================================================
# 输出结构测试
# ============================================================

class TestOutput:
    """标准化输出测试。"""

    def test_output_has_required_fields(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data())
        out = tp.get_output()
        assert "timestamp_ms" in out
        assert "lanes" in out
        assert len(out["lanes"]) == 2

    def test_output_lane_structure(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data())
        out = tp.get_output()
        lane0 = out["lanes"][0]
        required_keys = [
            "lane_id", "person_density", "vehicle_density",
            "person_flow_rate", "vehicle_flow_rate", "data_quality",
        ]
        for key in required_keys:
            assert key in lane0, f"缺少字段: {key}"

    def test_output_lane_ids_are_0_and_1(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data())
        out = tp.get_output()
        assert out["lanes"][0]["lane_id"] == 0
        assert out["lanes"][1]["lane_id"] == 1


# ============================================================
# 容错测试
# ============================================================

class TestFaultTolerance:
    """Hold-Last-Value 容错测试。"""

    def test_empty_lanes_preserves_value(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data([
            {"lane_id": 0, "person_count": 5, "vehicle_count": 1, "data_valid": True},
            {"lane_id": 1, "person_count": 0, "vehicle_count": 0, "data_valid": True},
        ]))
        before = tp.get_density(0)["person_smooth"]

        # 空 lanes 列表
        tp.update_from_yolo({"traffic": {"lanes": []}})
        after = tp.get_density(0)["person_smooth"]
        assert after == pytest.approx(before, rel=1e-6)

    def test_missing_traffic_key(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data())
        before = tp.get_density(0)["person_smooth"]

        tp.update_from_yolo({"timestamp_ms": 123, "frame_id": 1, "face": {}, "error": None})
        after = tp.get_density(0)["person_smooth"]
        assert after == pytest.approx(before, rel=1e-6)

    def test_reset_clears_all_state(self):
        tp = TrafficDataProcessor()
        tp.update_from_yolo(_make_yolo_data([
            {"lane_id": 0, "person_count": 9, "vehicle_count": 3, "data_valid": True},
            {"lane_id": 1, "person_count": 1, "vehicle_count": 1, "data_valid": True},
        ]))
        tp.reset()
        assert tp.get_quality() == 100
        d = tp.get_density(0)
        assert d["person_smooth"] == 0.0
        assert d["vehicle_smooth"] == 0.0
