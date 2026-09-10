"""
全局配置 — 智慧交通项目 人员B
所有可调参数集中管理，其他模块通过 from config import XXX 导入。
"""

import os

# ============================================================
# EWMA 平滑参数
# ============================================================
# α = 1 - exp(-1 / (HZ × TAU))
# HZ=1, TAU=3s → α ≈ 0.283
EWMA_ALPHA = 0.283
EWMA_TAU_SEC = 3.0

# ============================================================
# 交通态势参数
# ============================================================
WAITING_AREA_PER_LANE = 30.0       # 每车道等待区面积（m²）
LANE_COUNT = 2                      # 车道数量（固定 2 车道）

# ============================================================
# 闸机安全参数
# ============================================================
MAX_SINGLE_PASS_MS = 1500           # 单人 PIR 通过时间阈值（ms），超此判定尾随
FACE_TIMEOUT_MS = 2000              # 人脸校验超时（ms）
DEBOUNCE_MS = 30                    # PIR 消抖窗口（ms）
LOCKED_RECOVERY_SEC = 10            # LOCKED 状态自动恢复时间（秒）

# 人脸置信度阈值
FACE_CONF_HIGH = 80                 # ≥80：直接放行
FACE_CONF_MID = 50                  # ≥50：放行但记录低置信度日志

# ============================================================
# 系统状态参数
# ============================================================
SENSOR_TIMEOUT_SEC = 30             # 传感器数据超时阈值（秒）
FACE_STALL_SEC = 10                 # 人脸管道阻塞阈值（秒）
DEGRADED_RECOVERY_SEC = 5           # 降级恢复所需无异常时长（秒）
PARTIAL_FAULT_RECOVERY_SEC = 10     # 部分故障恢复所需正常心跳时长（秒）
MAX_MODULES = 8                     # 最大模块槽位数
HEARTBEAT_CHECK_INTERVAL = 3        # 心跳巡检间隔（主循环周期数）

# ============================================================
# 主循环参数
# ============================================================
LOOP_INTERVAL_MS = 100              # 主循环周期（ms）
YOLO_READ_INTERVAL = 10             # YOLO 读取间隔（主循环周期数，10×100ms=1s）
DATA_OUTPUT_1HZ_INTERVAL = 10       # 1Hz 输出间隔（主循环周期数）
DATA_OUTPUT_5S_INTERVAL = 50        # 5秒输出间隔（主循环周期数，50×100ms=5s）

# ============================================================
# 闸机门锁 Modbus RTU 参数（TCP → Windows UUSIMA 仿真平台）
# ============================================================
# 报文: 01 05 00 14 FF 00 CC 3E (ON) / 01 05 00 14 00 00 8D CE (OFF)
# RTU 帧不变，通过 TCP 透传到 Windows 上的 tcp_bridge.py → COM200 → UUSIMA
GATE_TCP_HOST = "192.168.0.100"      # Windows 的 IP（部署时修改）
GATE_TCP_PORT = 5020                  # TCP 桥接端口
GATE_SLAVE_ID = 0x01                  # 从站地址
GATE_COIL_ADDRESS = 0x0014            # 线圈地址（20）
GATE_TIMEOUT = 1.0                    # TCP 超时（秒）

# ============================================================
# PIR 多合一传感器参数（STM32 → 串口 ASCII 文本协议）
# ============================================================
# 格式: "红外传感器值: 1"（1=有人, 0=无人）
PIR_SERIAL_PORT = "/dev/ttyUSB0"     # USB 转串口（STM32 连接）
# ============================================================
# 文件路径
# ============================================================
YOLO_JSON_PATH = "./output/yolo_output.json"
TRAFFIC_DATA_OUTPUT_PATH = "./output/traffic_data.json"  # 给 A 的 traffic_light.py 读取

# ============================================================
# ThingsBoard MQTT 上云参数
# ============================================================
THINGSBOARD_HOST = "192.168.0.107"     # ThingsBoard Broker IP
THINGSBOARD_PORT = 1883              # MQTT 端口
THINGSBOARD_TOPIC = "v1/devices/me/telemetry"

# 设备 Access Token 从环境变量读取，不写入仓库。
# 本地开发：  export THINGSBOARD_TOKEN="你的token"   (Windows: set THINGSBOARD_TOKEN=...)
# 边缘套件：  在 systemd service 中配置 Environment="THINGSBOARD_TOKEN=..."
# 未配置时 MQTT 连接失败 → 仅上云不可用，本地功能不受影响。
THINGSBOARD_TOKEN = os.environ.get("THINGSBOARD_TOKEN", "")
MAX_LOG_ENTRIES = 100               # 环形缓冲区最大条目数

# ============================================================
# 数据跳变检测
# ============================================================
DATA_ANOMALY_RATIO = 10.0           # 单次计数跳变超此倍率 → 触发异常事件
