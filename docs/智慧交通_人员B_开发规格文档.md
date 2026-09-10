# 智慧交通项目 — 人员B 开发规格文档

**文档版本**：v1.0
**日期**：2026-07-22
**目标**：供另一个开发平台的 AI 接手实现


## 1. 项目背景

智慧交通项目共 7 人，3 个小组。本项目的硬件平台为 **UUSIMA 智慧教学实验平台**，执行器为 **ADAM-4150 数字 IO 模块**（通过 Modbus TCP 控制继电器 → 电磁锁/交通灯）。

| 小组 | 人员 | 职责 |
|------|------|------|
| 核心技术攻坚组 | A | YOLO11 模型部署、人车识别、红绿灯智能配时 |
| 核心技术攻坚组 | **B（本规格）** | 交通态势数据算法、闸机安全风控、系统全局状态框架 |
| 软硬件落地组 | C, D | 硬件组装、传感器驱动、设备控制、STM32 嵌入式开发 |
| 数据平台与运维组 | E, F, G | 云端平台、可视化大屏、项目管理与结题 |

**人员B 的定位**：承接 A 的 AI 推理结果，处理后输出给 D（设备控制）和 E（云平台），是串联算法-硬件-平台的中间核心环节。

**最终部署环境**：边缘计算套件（Linux），Python 3.x。

**开发环境**：开发者 PC（Windows），Python 3.x。


## 2. 整体架构

```
  [USB摄像头] → [YOLO11 @ 边缘计算套件]
                      │
              JSON 文件输出（1Hz）
      (yolo_output.json, 同机/局域网可读)
                      │
        ┌─────────────▼─────────────────────────┐
        │         人员B 程序（Python）            │
        │                                        │
        │  ┌──────────────────────────────────┐  │
        │  │ 模块① 交通态势数据算法            │  │
        │  │  - EWMA 平滑                     │  │
        │  │  - 人流/车流密度计算              │  │
        │  │  - 数据质量评估                   │  │
        │  └──────────────┬───────────────────┘  │
        │                 │                      │
        │  ┌──────────────▼───────────────────┐  │
        │  │ 模块② 闸机安全风控                │  │
        │  │  - 状态机                    │  │
        │  │  - 尾随判定（YOLO + PIR 双通道）  │  │
        │  │  - 人脸置信度梯度决策             │  │
        │  │  - 陌生人/闯闸告警               │  │
        │  └──────────────┬───────────────────┘  │
        │                 │                      │
        │  ┌──────────────▼───────────────────┐  │
        │  │ 模块③ 全局状态框架                │  │
        │  │  - 4 级系统状态                  │  │
        │  │  - 心跳巡检                      │  │
        │  │  - 跳转表（事件驱动升级）          │  │
        │  │  - 定时器自动恢复                 │  │
        │  └──────────────┬───────────────────┘  │
        │                 │                      │
        │  ┌──────────────▼───────────────────┐  │
        │  │ 数据接口与日志                    │  │
        │  │  - 标准化输出（给 D/E）           │  │
        │  │  - 环形日志缓冲区                 │  │
        │  └──────────────┬───────────────────┘  │
        └─────────────────┼──────────────────────┘
                          │
                   Modbus TCP（局域网）
                          │
                   ADAM-4150 数字 IO
                          │
                  继电器 → 电磁锁/交通灯
```


## 3. 外部接口（已有约定）

### 3.1 YOLO11 输入（来自人员A）

- 格式：JSON 文件（UTF-8，无 BOM），每秒覆盖写入
- 文件名：`yolo_output.json`
- 写入方式：先写 `.tmp` 再 rename
- 完整 schema：见附录 A

### 3.2 Modbus TCP 输出（发往 ADAM-4150）

- 协议：Modbus TCP
- 从站地址：1
- 线圈地址：22（控制电磁锁/闸机）
- 库：`pymodbus`

操作模式：
- 写线圈 22 = True → 继电器吸合 → 闸机开/锁开
- 写线圈 22 = False → 继电器断开 → 闸机关/锁关

### 3.3 交通态势数据输出（发往人员E/D）

- 格式：JSON（后续按实际需求对接 MQTT/HTTP）
- 频率：1Hz 给 A（配时用），5 秒周期给 E（上云展示）


## 4. 模块① 交通态势数据算法

### 4.1 功能

从 YOLO11 输出的 JSON 中提取每车道的人/车计数，做平滑和密度换算，输出标准化交通态势数据。

### 4.2 核心算法：EWMA 平滑

```python
# 指数加权移动平均
# α = 1 - exp(-1 / (HZ × TAU))
# 默认：HZ=1, TAU=3s → α ≈ 0.283

alpha = 0.283  # 可配置
traffic_state = {
    "lane_0": {"person_smooth": 0.0, "vehicle_smooth": 0.0},
    "lane_1": {"person_smooth": 0.0, "vehicle_smooth": 0.0},
}

def update_ewma(lane_id, person_count, vehicle_count, data_valid):
    if not data_valid:
        return  # 无效数据跳过，保持上次值

    s = traffic_state[f"lane_{lane_id}"]
    s["person_smooth"]  = alpha * person_count  + (1 - alpha) * s["person_smooth"]
    s["vehicle_smooth"] = alpha * vehicle_count + (1 - alpha) * s["vehicle_smooth"]
```

### 4.3 密度指标

```python
WAITING_AREA_PER_LANE = 30.0  # m²，等待区面积（可配置）

# 排队密度 = 平滑人数 / 等待区面积
person_density = person_smooth / WAITING_AREA_PER_LANE

# 流量密度 = 平滑瞬时流率（人/分钟或辆/分钟）
person_flow_rate = person_smooth * 60   # 1Hz → 每分钟
vehicle_flow_rate = vehicle_smooth * 60
```

### 4.4 数据质量评估

```python
consecutive_invalid = 0

def assess_quality():
    """根据连续无效帧数评估数据质量 0-100"""
    if consecutive_invalid == 0:
        return 100
    elif consecutive_invalid < 5:
        return 100 - consecutive_invalid * 15
    elif consecutive_invalid < 30:
        return 30
    else:
        return 0  # >30秒连续无效 → 质量归零
```

### 4.5 输出结构

```python
{
    "timestamp_ms": int,
    "lanes": [
        {
            "lane_id": 0,
            "person_density": float,   # 排队人数
            "vehicle_density": float,  # 排队车数
            "person_flow_rate": float, # 人流量（人/分钟）
            "vehicle_flow_rate": float,# 车流量（辆/分钟）
            "data_quality": int        # 0-100
        },
        # lane_id=1 同上
    ]
}
```

### 4.6 容错：Hold-Last-Value

YOLO 数据中断时，保持最近有效值输出，并在 `data_quality` 中体现衰减。超过 30 秒连续无效 → 触发 `EVENT_SENSOR_TIMEOUT`。


## 5. 模块② 闸机安全风控

### 5.1 硬件约束

- **只有 1 个人体红外感应器（PIR）**，无出入口之分，仅输出「有人/无人」
- 人脸识别依赖 YOLO11 输出的 `face` 字段
- 无法通过红外判断方向和人数 → **YOLO 人数为主判断，PIR 时长辅助兜底**

> **PIR 信号来源**：PIR 通过 GPIO 直连边缘计算套件，或通过 Modbus 读取 ADAM-4150 的 DI 通道。具体接线方式需与人员 C/D 确认，代码中应抽象为 ead_pir() -> bool 统一接口。

### 5.2 状态机

```
IDLE → APPROACHING → DECIDING → OPEN → CLOSING → IDLE
                                       ├→ TAILGATE（尾随确认）→ ALERT → LOCKED
                                       └→（恢复中）

任何状态都可能触发 → ALERT → LOCKED →（人工复位/10s超时恢复）→ IDLE
```

| 状态 | 说明 | 进入条件 | 退出条件 |
|------|------|----------|----------|
| `IDLE` | 闸机关闭，等待触发 | 初始 / CLOSING 完成 / LOCKED 恢复 | PIR 触发（上升沿）|
| `APPROACHING` | 有人靠近，等待 YOLO 结果 | PIR 触发 | 收到 YOLO face 数据或超时 2s |
| `DECIDING` | 判定：放行 or 拦截 | YOLO 结果就绪 | 判定完成 |
| `OPEN` | 闸机开启，行人通过 | 判定通过 | PIR 变 LOW（人走完）或 PIR 超时 |
| `CLOSING` | 正常关闭 | PIR 变 LOW | 关闭完成 |
| `TAILGATE` | 尾随确认 | OPEN 期间 PIR 持续 >1.5s 或 YOLO ≥2 人 | 立即 → ALERT |
| `ALERT` | 告警状态 | 陌生人/尾随/闯闸/超时 | 转为 LOCKED |
| `LOCKED` | 完全锁定 | ALERT 后 | 人工复位 或 10s 超时 |

> **自动流转**：ALERT → LOCKED 在同一周期内完成，对外表现为「检测到问题 → 立即锁定」。LOCKED 超时后自动回到 IDLE。
> **闯闸检测**（未在状态图中画出的独立路径）：在 IDLE 或 CLOSING 状态下，若 PIR 意外触发且无人脸校验通过，触发 ALERT_FORCED_ENTRY，直接进入 ALERT → LOCKED。

### 5.3 尾随判定逻辑（双通道）

**通道① — YOLO 人数（主力）**：
```python
if yolo_person_count >= 2:
    trigger_alert(ALERT_TAILGATE, "YOLO检测到多人")
```

**通道② — PIR 持续时长（兜底）**：
```python
# OPEN 状态下，PIR 持续 HIGH 超过单人正常通过时间
MAX_SINGLE_PASS_MS = 1500  # 1.5s，可调
if state == OPEN and pir_high_duration_ms > MAX_SINGLE_PASS_MS:
    trigger_alert(ALERT_TAILGATE, "PIR时长异常")
```

### 5.4 人脸置信度梯度决策

```python
def assess_face(face):
    """
    face: {"detected": bool, "person_id": int, "confidence": int, "is_stranger": bool}
    返回: "PASS" | "PASS_WARN" | "ALERT_LOCK"
    """
    if not face["detected"] or face["person_id"] == -3:
        return "ALERT_LOCK"  # 没检测到人脸

    if face["is_stranger"] or face["person_id"] == -1:
        return "ALERT_LOCK"  # 陌生人

    if face["person_id"] == -2:
        return "ALERT_LOCK"  # 检测到但无法识别

    # 已知人员，看置信度
    conf = face["confidence"]
    if conf >= 80:
        return "PASS"
    elif conf >= 50:
        return "PASS_WARN"   # 放行但记录低置信度日志
    else:
        return "ALERT_LOCK"  # 置信度过低
```

### 5.5 告警类型

```python
ALERT_NONE                  # 无告警
ALERT_STRANGER              # 陌生人
ALERT_FACE_TIMEOUT          # 人脸校验超时（2s）
ALERT_TAILGATE_YOLO         # YOLO 多人（尾随）
ALERT_TAILGATE_PIR          # PIR 时长异常（尾随）
ALERT_TAILGATE_CONFIRMED    # 出口确认多人（多 PIR 场景预留）
ALERT_FORCED_ENTRY          # 闯闸（IDLE/CLOSING 状态 PIR 触发但无人脸通过）
```

### 5.6 PIR 消抖

```python
DEBOUNCE_MS = 30  # 30ms 消抖窗口

def pir_update(raw_level):
    """PIR 信号消抖，输出边沿标记"""
    # 信号变化后维持 30ms 才确认
    # 输出：triggered, just_triggered（上升沿）, just_cleared（下降沿）, high_duration_ms
```


## 6. 模块③ 系统全局状态框架

### 6.1 四级系统状态

| 状态 | 含义 | 系统行为 |
|------|------|----------|
| `SYS_OK` | 正常运行 | 全部功能正常 |
| `SYS_DEGRADED` | 降级运行 | 交通数据标记 hold-last-value，闸机继续工作 |
| `SYS_PARTIAL_FAULT` | 部分故障 | 闸机全锁，交通灯切固定周期 |
| `SYS_CRITICAL` | 严重故障 | 全停，等看门狗复位 |

- **升级**：事件驱动（立即响应）
- **恢复**：定时器驱动（等稳定后再升回去）

### 6.2 异常事件

```python
EVENT_SENSOR_TIMEOUT       # 传感器 >30s 无数据
EVENT_DATA_ANOMALY         # 单次计数跳变超阈值（如 5→50）
EVENT_QUEUE_OVERFLOW       # 推送队列满
EVENT_FACE_PIPELINE_STALL  # 人脸结果 >10s 未更新
EVENT_MODULE_HEARTBEAT_LOST# 某模块连续 3 周期无心跳
EVENT_GATE_INCONSISTENT    # 闸机命令 vs 实际状态不一致
EVENT_WATCHDOG_WARNING     # 看门狗预警
```

### 6.3 跳转表

```python
TRANSITION_TABLE = [
    # (当前状态, 事件, 目标状态)
    (SYS_OK,            EVENT_SENSOR_TIMEOUT,       SYS_DEGRADED),
    (SYS_OK,            EVENT_DATA_ANOMALY,         SYS_DEGRADED),
    (SYS_OK,            EVENT_QUEUE_OVERFLOW,       SYS_DEGRADED),
    (SYS_OK,            EVENT_FACE_PIPELINE_STALL,  SYS_DEGRADED),

    (SYS_DEGRADED,      EVENT_MODULE_HEARTBEAT_LOST,SYS_PARTIAL_FAULT),
    (SYS_DEGRADED,      EVENT_GATE_INCONSISTENT,    SYS_PARTIAL_FAULT),
    (SYS_DEGRADED,      EVENT_WATCHDOG_WARNING,     SYS_PARTIAL_FAULT),

    (SYS_PARTIAL_FAULT, EVENT_MODULE_HEARTBEAT_LOST,SYS_CRITICAL),
    (SYS_PARTIAL_FAULT, EVENT_WATCHDOG_WARNING,     SYS_CRITICAL),
]

def lookup_transition(current, event):
    for cur, evt, nxt in TRANSITION_TABLE:
        if cur == current and evt == event:
            return nxt
    return current  # 无匹配 → 保持
```

### 6.4 恢复规则

| 当前状态 | 恢复条件 | 目标 |
|----------|----------|------|
| `SYS_DEGRADED` | 连续 5 秒无新异常 | `SYS_OK` |
| `SYS_PARTIAL_FAULT` | 所有模块心跳连续 10 秒正常 | `SYS_DEGRADED` |
| `SYS_CRITICAL` | 不自动恢复，等外部复位 | — |

### 6.5 心跳巡检

每个模块注册一个心跳槽位：
```python
MAX_MODULES = 8

module_health = [
    {"heartbeat": 0, "error_code": 0, "active": True},  # slot 0: 交通数据模块
    {"heartbeat": 0, "error_code": 0, "active": True},  # slot 1: 闸机风控模块
    {"heartbeat": 0, "error_code": 0, "active": True},  # slot 2: 预留
    # slot 3-7: 供其他组注册
]
```

巡检逻辑（每 3 个主循环周期执行一次）：
```python
def heartbeat_check():
    for m in module_health:
        if not m["active"]:
            continue
        if m["heartbeat"] == 0:
            m["error_code"] = 1
            raise_event(EVENT_MODULE_HEARTBEAT_LOST)
        else:
            m["heartbeat"] = 0  # 清零，等下一次更新
            m["error_code"] = 0
```


## 7. 数据接口与日志

### 7.1 环形日志缓冲区

```python
from collections import deque

MAX_LOG_ENTRIES = 100

log_buffer = deque(maxlen=MAX_LOG_ENTRIES)

def write_log(level, module, error_code, context=""):
    """
    level: "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    module: "traffic" | "gate" | "system"
    """
    entry = {
        "timestamp_ms": int(time.time() * 1000),
        "level": level,
        "module": module,
        "error_code": error_code,
        "context": context
    }
    log_buffer.append(entry)
    print(f"[{entry['level']}][{module}] {context}")
```

### 7.2 主循环调度

```python
import time

LOOP_INTERVAL_MS = 100  # 100ms 主循环周期

def main_loop():
    cycle = 0
    while True:
        t_start = time.time()

        # 1. 读取 YOLO JSON 输入（每秒读一次，避免频繁 IO）
        if cycle % 10 == 0:  # 每 10 个周期 ≈ 1s
            yolo_data = read_yolo_json()

        # 2. 最高优先级：状态机巡检（每 3 周期做心跳检查）
        if cycle % 3 == 0:
            sys_state_run()

        # 3. 业务模块更新
        traffic_data_update(yolo_data)
        gate_safety_update(yolo_data)

        # 4. 数据输出
        data_output_push()

        # 5. 控制周期节拍
        elapsed = (time.time() - t_start) * 1000
        if elapsed < LOOP_INTERVAL_MS:
            time.sleep((LOOP_INTERVAL_MS - elapsed) / 1000)

        cycle += 1
```


## 8. 项目文件结构建议

```
smart_traffic/
├── main.py                  # 主循环入口
├── config.py                # 全局配置（Modbus地址、阈值、α值等）
│
├── traffic_data.py          # 模块① 交通态势数据算法
├── gate_safety.py           # 模块② 闸机安全风控
├── sys_state.py             # 模块③ 全局状态框架
│
├── modbus_client.py         # Modbus TCP 客户端封装
├── yolo_reader.py           # YOLO JSON 文件读取
├── log_writer.py            # 日志系统
├── data_output.py           # 数据输出接口
│
└── yolo_schema.md           # YOLO 输入 JSON schema（见附录 A）
```


## 9. 开发次序建议

1. **先骨架后血肉**：先搭 `main.py` 主循环 + config，再逐个模块填充
2. **从已有代码起步**：闸机模块可以直接复用 `仿真智能门锁/lock_manager.py` 和 `modbus_client.py` 的架构（把 RTU 改为 TCP，2 态改为 8 态）
3. **优先调通 Modbus**：先写个简单的脚本，通过 Modbus TCP 控制 ADAM-4150 线圈 22 的开关，确认链路通
4. **用假数据并行开发**：在 A 的 JSON 还没就绪前，自己写个脚本定时生成假 `yolo_output.json` 来调三个模块


## 附录 A：YOLO11 输出 JSON Schema（给人员A的接口规范）

```json
{
  "timestamp_ms": 1721635200000,
  "frame_id": 12345,
  "traffic": {
    "lanes": [
      {
        "lane_id": 0,
        "person_count": 3,
        "vehicle_count": 1,
        "data_valid": true
      },
      {
        "lane_id": 1,
        "person_count": 0,
        "vehicle_count": 2,
        "data_valid": true
      }
    ]
  },
  "face": {
    "detected": true,
    "person_id": 1,
    "person_name": "张三",
    "confidence": 92,
    "is_stranger": false
  },
  "error": null
}
```

**关键约定**：
- 文件写入用临时文件 rename（防止半截读）
- `traffic.lanes` 固定 2 个元素
- `face.person_id`：≥0=已知人员，-1=陌生人，-2=检测到但无法识别，-3=未检测到人脸
- `face.confidence`：0-100 整数
- `traffic.data_valid=false` 时 `person_count/vehicle_count` 填 0
- 更新频率：1 Hz


## 附录 B：关键配置参数汇总

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `YOLO_JSON_PATH` | `./output/yolo_output.json` | JSON 输入路径 |
| `EWMA_ALPHA` | 0.283 | EWMA 平滑系数（对应 1Hz, τ=3s） |
| `EWMA_TAU_SEC` | 3.0 | 平滑响应时间常数 |
| `WAITING_AREA_PER_LANE` | 30.0 | 每车道等待区面积（m²） |
| `MAX_SINGLE_PASS_MS` | 1500 | 单人 PIR 阈值，超此判定尾随 |
| `FACE_TIMEOUT_MS` | 2000 | 人脸校验超时 |
| `DEBOUNCE_MS` | 30 | PIR 消抖窗口 |
| `LOCKED_RECOVERY_SEC` | 10 | 锁定态自动恢复时间 |
| `SENSOR_TIMEOUT_SEC` | 30 | 传感器超时阈值 |
| `FACE_STALL_SEC` | 10 | 人脸管道阻塞阈值 |
| `DEGRADED_RECOVERY_SEC` | 5 | 降级恢复所需无异常时长 |
| `LOOP_INTERVAL_MS` | 100 | 主循环周期 |
| `MODBUS_HOST` | `"ADAM-4150的IP"` | Modbus TCP 地址 |
| `MODBUS_PORT` | 502 | Modbus TCP 端口 |
| `MODBUS_SLAVE_ID` | 1 | 从站地址 |
| `MODBUS_COIL_ADDRESS` | 22 | 线圈地址 |

---

> 如有疑问，对照 `D:\Trae_project\test1\仿真智能门锁\` 下的现有代码参考架构模式。
