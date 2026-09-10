"""
数据读取器 — 智慧交通项目 人员B
桥接人员A v3.0 的人脸检测 + 车辆检测，产出B模块所需的标准数据格式。

模式：
  bridge（默认）：调A的 FaceDetector + LBPH + VehicleCounter，摄像头取帧
  file（后备）  ：读 yolo_output.json（兼容旧接口）

车道分配（B侧实现）：按车辆中心点 x 坐标将画面水平均分为 2 车道。
"""

import os
import sys
import time
import json

from config import YOLO_JSON_PATH

# ============================================================
# 人员A v3.0 交付目录
# ============================================================
_A_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "A人员交付文件3.0")
)
_A_MODEL_DIR = os.path.join(_A_DIR, "models")

# ============================================================
# 延迟导入（避免缺依赖时直接报错）
# ============================================================
_cv2 = None
_joblib = None


def _cv():
    global _cv2
    if _cv2 is None:
        import cv2
        _cv2 = cv2
    return _cv2


def _jl():
    global _joblib
    if _joblib is None:
        import joblib
        _joblib = joblib
    return _joblib


# ============================================================
# YoloReader
# ============================================================

class YoloReader:
    """桥接人员A v2.0：人脸 + 车辆 → B的标准 dict。"""

    def __init__(self, json_path=None, mode="bridge"):
        self.path = json_path or YOLO_JSON_PATH
        self.mode = mode
        self._last_valid_data = None
        self._last_read_time_ms = 0
        self._frame_id = 0

        # 桥接组件
        self._face_detector = None
        self._recognizer = None
        self._names = None
        self._vehicle_counter = None
        self._cap = None
        self._bridge_ok = False

        if mode == "bridge":
            self._init_bridge()

    # ================================================================
    # 公开接口
    # ================================================================

    def read(self):
        """
        Returns:
            dict: {timestamp_ms, frame_id, traffic: {lanes: [...]}, face: {...}, error: null}
            None: 数据不可用
        """
        if self.mode == "bridge" and self._bridge_ok:
            return self._read_from_bridge()
        else:
            return self._read_from_file()

    def get_last_valid(self):
        return self._last_valid_data

    # ================================================================
    # 桥接初始化
    # ================================================================

    def _init_bridge(self):
        """加载人员A v2.0 的全部组件。"""
        try:
            cv2 = _cv()

            # ---- 1. 人脸检测器（v1.0/v2.0 通用） ----
            if _A_DIR not in sys.path:
                sys.path.insert(0, _A_DIR)
            from face_detector import FaceDetector
            self._face_detector = FaceDetector()

            # ---- 2. LBPH 人脸识别 ----
            rec_path = os.path.join(_A_MODEL_DIR, "face_recognizer.m")
            if not os.path.exists(rec_path):
                print(f"[yolo_reader] 未找到识别模型: {rec_path}")
                return
            self._recognizer = cv2.face.LBPHFaceRecognizer_create()
            self._recognizer.read(rec_path)

            names_path = os.path.join(_A_MODEL_DIR, "names.pkl")
            if os.path.exists(names_path):
                self._names = _jl().load(names_path)

            # ---- 3. 车辆检测器（v2.0 新增） ----
            if _A_DIR not in sys.path:
                sys.path.insert(0, _A_DIR)
            from vehicle_detector import VehicleCounter
            self._vehicle_counter = VehicleCounter(count_line_ratio=0.5)

            # ---- 4. 摄像头 ----
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2
            self._cap = cv2.VideoCapture(0, backend)
            if not self._cap.isOpened():
                print("[yolo_reader] 摄像头打开失败")
                return

            self._bridge_ok = True
            print(f"[yolo_reader] 桥接v3.0就绪 "
                  f"(face=YuNet+LBPH, vehicle=YOLO11n, "
                  f"names={len(self._names or [])}人)")

        except Exception as e:
            print(f"[yolo_reader] 桥接初始化失败: {e}")
            import traceback
            traceback.print_exc()
            self._bridge_ok = False

    # ================================================================
    # 核心：摄像头取帧 → A的检测器 → B的标准dict
    # ================================================================

    def _read_from_bridge(self):
        cv2 = _cv()
        now_ms = int(time.time() * 1000)

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return None

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # ---- 人脸检测 + 识别（A的 YuNet + LBPH） ----
        face_data = self._build_face_data(
            self._face_detector.detect(frame, confidence_threshold=0.5),
            gray,
        )

        # ---- 车辆检测 + 车道分配（A的 YOLO11n） ----
        vehicle_result = self._vehicle_counter.update(frame)
        lanes = self._build_lanes(vehicle_result, w)

        # ---- 组装 ----
        self._frame_id += 1
        data = {
            "timestamp_ms": now_ms,
            "frame_id": self._frame_id,
            "traffic": {"lanes": lanes},
            "face": face_data,
            "error": None,
        }

        self._last_valid_data = data
        self._last_read_time_ms = now_ms
        return data

    # ================================================================
    # 人脸：A的检测结果 → B的face dict
    # ================================================================

    def _build_face_data(self, faces, gray):
        """
        faces: [(x1, y1, x2, y2, confidence), ...]
        recognizer.predict(roi) → (label, score)

        转换规则:
          score < 60       → stranger (person_id=-1)
          score >= 60      → known (person_id=label)
          无脸              → person_id=-3
          ROI提取/识别失败   → person_id=-2
          confidence       → clamp(100-score, 0, 100)
        """
        if not faces:
            return {
                "detected": False, "person_id": -3,
                "person_name": "", "confidence": 0, "is_stranger": False,
            }

        # 最大脸（闸机场景：识别最前面的人）
        faces_sorted = sorted(
            faces, key=lambda f: (f[2]-f[0])*(f[3]-f[1]), reverse=True
        )
        x1, y1, x2, y2, _det_conf = faces_sorted[0]

        try:
            roi = gray[y1:y2, x1:x2]
            roi = _cv().resize(roi, (320, 240), interpolation=_cv().INTER_CUBIC)
            label, score = self._recognizer.predict(roi)
            confidence = max(0, min(100, int(100 - score)))
        except Exception:
            return {
                "detected": True, "person_id": -2,
                "person_name": "", "confidence": 0, "is_stranger": False,
            }

        if confidence < 60:
            return {
                "detected": True, "person_id": -1,
                "person_name": "", "confidence": confidence, "is_stranger": True,
            }

        name = ""
        if self._names and 0 <= label < len(self._names):
            name = self._names[label]

        return {
            "detected": True, "person_id": int(label),
            "person_name": name, "confidence": confidence, "is_stranger": False,
        }

    # ================================================================
    # 车辆 → 车道分配（B侧实现）
    # ================================================================

    def _build_lanes(self, vehicle_result, frame_width):
        """
        将车辆检测结果分配到 2 个车道。

        策略：按车辆中心点 cx 将画面水平均分。
              cx < frame_width/2  → lane 0
              cx >= frame_width/2 → lane 1

        person_count: 车道区域暂无人流检测，填 0（闸机人流由face覆盖）
        vehicle_count: 该车道当前画面内的车辆数
        data_valid: True（有真实检测数据）
        """
        mid = frame_width / 2
        lane_vehicles = [0, 0]

        for v in vehicle_result.get("vehicles", []):
            cx = v["center"][0]
            lane_id = 0 if cx < mid else 1
            lane_vehicles[lane_id] += 1

        return [
            {
                "lane_id": 0,
                "person_count": 0,
                "vehicle_count": lane_vehicles[0],
                "data_valid": True,
            },
            {
                "lane_id": 1,
                "person_count": 0,
                "vehicle_count": lane_vehicles[1],
                "data_valid": True,
            },
        ]

    # ================================================================
    # 文件模式（后备）
    # ================================================================

    def _read_from_file(self):
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
        except (OSError, IOError):
            return None
        if not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not self._validate(data):
            return None
        self._last_valid_data = data
        self._last_read_time_ms = int(time.time() * 1000)
        return data

    @staticmethod
    def _validate(data):
        if not isinstance(data, dict):
            return False
        lanes = data.get("traffic", {}).get("lanes")
        return isinstance(lanes, list) and len(lanes) >= 1

    # ================================================================
    # 资源管理
    # ================================================================

    def close(self):
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._bridge_ok = False

    def __del__(self):
        self.close()
