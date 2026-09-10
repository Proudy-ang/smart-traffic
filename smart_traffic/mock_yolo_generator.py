#!/usr/bin/env python
"""
假数据生成器 — 智慧交通项目 人员B
定时生成合法的 yolo_output.json，供开发调试使用。

用法：
    python mock_yolo_generator.py                # 默认模式，正常数据
    python mock_yolo_generator.py --anomaly      # 含异常注入（测试容错）

特性：
    - 1Hz 写入，使用 .tmp → rename 原子模式
    - 模拟 2 车道人/车计数随机波动
    - 模拟人脸检测（已知人员/陌生人/无脸 交替）
    - 可选异常注入：data_valid=false、人脸超时、数据跳变
"""

import json
import os
import random
import time
import argparse

# ---- 配置 ----
OUTPUT_DIR = "./output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "yolo_output.json")
TMP_FILE = os.path.join(OUTPUT_DIR, "yolo_output.tmp")

# 已知人员库
KNOWN_PERSONS = [
    {"person_id": 1, "person_name": "张三", "confidence": 92, "is_stranger": False},
    {"person_id": 2, "person_name": "李四", "confidence": 85, "is_stranger": False},
    {"person_id": 3, "person_name": "王五", "confidence": 78, "is_stranger": False},
]

# 陌生人配置
STRANGER = {"person_id": -1, "person_name": "", "confidence": 0, "is_stranger": True}
NO_FACE = {"person_id": -3, "person_name": "", "confidence": 0, "is_stranger": False}


def generate_traffic_data(anomaly=False, anomaly_counter=None):
    """
    生成交通流量数据。

    Args:
        anomaly: 是否可能注入异常
        anomaly_counter: 异常计数器引用（用于周期性异常注入）

    Returns:
        dict: traffic 数据
    """
    lanes = []
    for lane_id in range(2):
        person_count = random.randint(0, 6)
        vehicle_count = random.randint(0, 3)
        data_valid = True

        # 异常注入：每 60 秒有 20% 概率产生 1 帧无效数据
        if anomaly and anomaly_counter is not None:
            anomaly_counter[0] += 1
            if anomaly_counter[0] > 60 and random.random() < 0.2:
                data_valid = False
                person_count = 0
                vehicle_count = 0
                anomaly_counter[0] = 0

        lanes.append({
            "lane_id": lane_id,
            "person_count": person_count,
            "vehicle_count": vehicle_count,
            "data_valid": data_valid,
        })

    return {"lanes": lanes}


def generate_face_data(frame_count, anomaly=False):
    """
    生成人脸检测数据，周期性切换状态。

    状态轮换（每 10-20 帧切换一次）：
        已知人员 → 陌生人 → 无脸 → 已知人员 → ...
    """
    if not hasattr(generate_face_data, "_state"):
        generate_face_data._state = "known"
        generate_face_data._state_counter = 0
        generate_face_data._switch_at = random.randint(10, 20)

    generate_face_data._state_counter += 1

    if generate_face_data._state_counter >= generate_face_data._switch_at:
        # 切换状态
        if generate_face_data._state == "known":
            generate_face_data._state = "stranger"
        elif generate_face_data._state == "stranger":
            generate_face_data._state = "none"
        else:
            generate_face_data._state = "known"
        generate_face_data._state_counter = 0
        generate_face_data._switch_at = random.randint(10, 20)

    state = generate_face_data._state

    if state == "known":
        person = random.choice(KNOWN_PERSONS).copy()
        # 置信度随机波动 ±10
        person["confidence"] = max(0, min(100, person["confidence"] + random.randint(-10, 10)))
        return {"detected": True, **person}

    elif state == "stranger":
        return {"detected": True, **STRANGER.copy()}

    else:  # "none"
        return {"detected": False, **NO_FACE.copy()}


def atomic_write(data):
    """原子写入：先写 .tmp 再 rename。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    with open(TMP_FILE, "w", encoding="utf-8") as f:
        f.write(json_str)
        f.flush()
        os.fsync(f.fileno())

    os.replace(TMP_FILE, OUTPUT_FILE)  # Windows 上也是原子操作


def main():
    parser = argparse.ArgumentParser(description="YOLO 假数据生成器")
    parser.add_argument("--anomaly", action="store_true", help="启用异常注入")
    parser.add_argument("--rate", type=float, default=1.0, help="生成频率（Hz），默认 1.0")
    args = parser.parse_args()

    print(f"假数据生成器启动: {OUTPUT_FILE}")
    print(f"  频率: {args.rate} Hz")
    print(f"  异常注入: {'开启' if args.anomaly else '关闭'}")
    print(f"  按 Ctrl+C 停止")

    frame_id = 0
    anomaly_counter = [0]  # 用列表包装以支持在函数间共享
    interval = 1.0 / args.rate

    try:
        while True:
            t_start = time.time()
            timestamp_ms = int(time.time() * 1000)

            traffic = generate_traffic_data(args.anomaly, anomaly_counter)
            face = generate_face_data(frame_id, args.anomaly)

            output = {
                "timestamp_ms": timestamp_ms,
                "frame_id": frame_id,
                "traffic": traffic,
                "face": face,
                "error": None,
            }

            atomic_write(output)

            elapsed = time.time() - t_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

            frame_id += 1

            if frame_id % 50 == 0:
                print(f"  已生成 {frame_id} 帧 "
                      f"[lane0: p={traffic['lanes'][0]['person_count']} "
                      f"v={traffic['lanes'][0]['vehicle_count']} "
                      f"valid={traffic['lanes'][0]['data_valid']}] "
                      f"[face: id={face['person_id']} conf={face['confidence']}]")

    except KeyboardInterrupt:
        print(f"\n停止。共生成 {frame_id} 帧。")


if __name__ == "__main__":
    main()
