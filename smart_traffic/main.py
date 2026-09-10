"""
主循环入口 — 智慧交通项目 人员B
100ms 周期调度：读取 YOLO → 业务模块 → 输出
"""

import json
import time
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    LOOP_INTERVAL_MS,
    YOLO_READ_INTERVAL,
    HEARTBEAT_CHECK_INTERVAL,
    DATA_OUTPUT_1HZ_INTERVAL,
    DATA_OUTPUT_5S_INTERVAL,
    THINGSBOARD_HOST,
    THINGSBOARD_PORT,
    THINGSBOARD_TOKEN,
    THINGSBOARD_TOPIC,
)
from yolo_reader import YoloReader
from traffic_data import TrafficDataProcessor
from gate_safety import GateSafetyController, GateState
from sys_state import SystemStateManager, SysState, SystemEvent
from modbus_client import ModbusRTUClient
from log_writer import write_log, get_logs
from data_output import DataOutput
from pir_reader import PirReader


def _publish_mqtt(client, traffic_data, gate_status, system_status):
    """将综合数据通过 MQTT 发往 ThingsBoard。"""
    payload = {
        "traffic": traffic_data,
        "gate": gate_status,
        "system": system_status,
    }
    try:
        client.publish(THINGSBOARD_TOPIC, json.dumps(payload, ensure_ascii=False), qos=1)
    except Exception:
        pass  # 网络抖动不影响主循环


def main_loop(
    yolo_reader: YoloReader,
    traffic: TrafficDataProcessor,
    gate: GateSafetyController,
    sys_state: SystemStateManager,
    data_out: DataOutput,
    pir_reader=None,
    mqtt_client=None,
):
    """
    主循环：100ms 周期运行。

    调度策略：
    - 每个周期：读取 PIR（消抖）、更新闸机状态机
    - 每 10 周期（≈1s）：读取 YOLO JSON、更新交通数据、1Hz 输出
    - 每 3 周期：心跳巡检
    - 每 50 周期（≈5s）：5s 周期输出
    """
    cycle = 0
    write_log("INFO", "system", 0, "主循环启动")

    while True:
        t_start = time.time()

        # --------------------------------------------------
        # 1. 读取 YOLO JSON（1Hz）
        # --------------------------------------------------
        yolo_data = None
        if cycle % YOLO_READ_INTERVAL == 0:
            yolo_data = yolo_reader.read()

        # --------------------------------------------------
        # 2. 心跳巡检（每 3 周期 ≈ 300ms）
        # --------------------------------------------------
        if cycle % HEARTBEAT_CHECK_INTERVAL == 0:
            sys_state.heartbeat_check()

        # --------------------------------------------------
        # 3. 业务模块更新
        # --------------------------------------------------
        # 模块① 交通态势数据
        traffic.update_from_yolo(yolo_data)

        # 模块② 闸机安全风控 -- 人脸触发（绕过红外）
        pir_raw = None
        if yolo_data is not None:
            pir_raw = yolo_data.get("face", {}).get("detected", None)
        gate.update(yolo_data, pir_raw=pir_raw)
        if cycle % YOLO_READ_INTERVAL == 0:
            print(f"[DEBUG] face={pir_raw} gate={gate.get_status()['state']}")

        # --------------------------------------------------
        # 4. 数据输出
        # --------------------------------------------------
        # 1Hz 输出（给人员 A 配时用）
        if cycle % DATA_OUTPUT_1HZ_INTERVAL == 0:
            traffic_1hz = traffic.get_output()
            data_out.push_traffic_1hz(traffic_1hz)

        # 5 秒周期输出（给人员 E 上云 + ThingsBoard MQTT）
        if cycle % DATA_OUTPUT_5S_INTERVAL == 0:
            traffic_5s = traffic.get_output()
            gate_status = gate.get_status()
            system_status = sys_state.get_status()
            data_out.push_traffic_5s(traffic_5s, gate_status, system_status)

            # ThingsBoard MQTT 上报
            if mqtt_client is not None:
                _publish_mqtt(mqtt_client, traffic_5s, gate_status, system_status)

        # --------------------------------------------------
        # 5. 控制周期节拍
        # --------------------------------------------------
        elapsed_ms = (time.time() - t_start) * 1000
        if elapsed_ms < LOOP_INTERVAL_MS:
            time.sleep((LOOP_INTERVAL_MS - elapsed_ms) / 1000)

        cycle += 1


def main():
    """程序入口：初始化各模块并启动主循环。"""
    write_log("INFO", "system", 0, "智慧交通系统 人员B 启动中...")

    # ---- 初始化闸机门锁（Modbus RTU over TCP → Windows UUSIMA） ----
    modbus = ModbusRTUClient()
    connected = modbus.connect()
    if connected:
        write_log("INFO", "system", 0, f"闸机门锁已连接 ({modbus.host}:{modbus.port})")
    else:
        write_log("WARNING", "system", 1, f"闸机门锁连接失败 ({modbus.host}:{modbus.port})，闸机控制不可用")

    # ---- 初始化各模块（依赖注入） ----
    yolo_reader = YoloReader(mode="bridge")  # 桥接模式：调A的人脸库+摄像头
    if yolo_reader._bridge_ok:
        write_log("INFO", "system", 0, "数据读取器: 桥接模式（A的人脸库+摄像头）")
    else:
        write_log("WARNING", "system", 2, "数据读取器: 桥接失败，回退文件模式")

    traffic = TrafficDataProcessor()
    gate = GateSafetyController(modbus)

    # ---- 初始化 PIR 多合一传感器（串口 Modbus RTU）----
    pir_reader = PirReader()
    if pir_reader.open():
        write_log("INFO", "system", 0, f"PIR 传感器已连接 ({pir_reader.port})")
    else:
        write_log("WARNING", "system", 1, f"PIR 传感器连接失败 ({pir_reader.port})，闸机风控降级")
    sys_state_mgr = SystemStateManager()
    data_out = DataOutput()

    # 注册心跳槽位
    sys_state_mgr.register_module(0)  # slot 0: 交通数据模块
    sys_state_mgr.register_module(1)  # slot 1: 闸机风控模块

    # 注入系统状态管理器引用（供业务模块触发事件）
    traffic.set_sys_state_manager(sys_state_mgr)
    gate.set_sys_state_manager(sys_state_mgr)

    # ---- 初始化 ThingsBoard MQTT ----
    mqtt_client = None
    try:
        import paho.mqtt.client as mqtt
        mqtt_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        mqtt_client.username_pw_set(THINGSBOARD_TOKEN)
        mqtt_client.connect(THINGSBOARD_HOST, THINGSBOARD_PORT, keepalive=30)
        mqtt_client.loop_start()
        write_log("INFO", "system", 0, f"MQTT 已连接: {THINGSBOARD_HOST}:{THINGSBOARD_PORT}")
    except Exception as e:
        write_log("WARNING", "system", 3, f"MQTT 连接失败: {e}（上云不可用，本地功能正常）")
        mqtt_client = None

    try:
        main_loop(yolo_reader, traffic, gate, sys_state_mgr, data_out, pir_reader, mqtt_client)
    except KeyboardInterrupt:
        write_log("INFO", "system", 0, "收到中断信号，系统退出")
    finally:
        if mqtt_client:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
        yolo_reader.close()
        pir_reader.close()
        modbus.disconnect()
        write_log("INFO", "system", 0, "系统已停止")


if __name__ == "__main__":
    main()
