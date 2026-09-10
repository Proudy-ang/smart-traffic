# 智慧交通边缘计算系统 — 核心中间层

UUSIMA 智慧教学实验平台上的智慧交通项目（7 人 / 3 组协作），本仓库为**核心技术攻坚组的中间层模块**：承接上游 AI 推理结果，输出给下游硬件控制与云平台。

```
[摄像头] → [YOLO11 人脸+车辆检测]  →  本仓库  →  [Modbus 闸机] / [MQTT 云平台]
              上游（人员A）          中间层        下游（C/D、E）
```

---

## 模块

### ① 交通态势数据算法 `traffic_data.py`

将 YOLO 的逐帧原始计数转化为工程可用指标。

- **EWMA 指数平滑**：`α = 1 - exp(-1/(HZ×TAU))`，取 1Hz、τ=3s → α≈0.283
- **密度换算**：排队密度 = 平滑值 / 等待区面积（30 m²/车道）
- **流率换算**：瞬时流率 × 60 → 辆/分钟，作为红绿灯配时输入
- **数据质量评分**：0–100，连续无效帧衰减，30s 归零并触发系统降级
- **容错**：Hold-Last-Value + 计数跳变检测（单次超 10 倍触发告警）

### ② 闸机安全风控 `gate_safety.py`

8 状态状态机，防尾随 / 防闯闸。

```
IDLE → APPROACHING → DECIDING → OPEN → CLOSING → IDLE   正常通行
                                  ├→ TAILGATE → ALERT → LOCKED
                                  └→ 闯闸检测（IDLE/CLOSING 状态意外触发）
```

- **人脸置信度梯度决策**：≥80 放行 / 50–79 放行并记录 / <50 锁定告警
- **双通道尾随检测**：YOLO 人数 ≥2（主力）+ PIR 持续 >1.5s（兜底）
- **PIR 消抖**：30ms 窗口
- **自动恢复**：LOCKED 10s 后回到 IDLE

### ③ 系统全局状态框架 `sys_state.py`

4 级健康状态，事件驱动升级 + 定时器延迟恢复。

| 状态 | 含义 | 系统行为 |
|------|------|----------|
| `SYS_OK` | 正常 | 全部功能正常 |
| `SYS_DEGRADED` | 降级 | 数据 Hold-Last-Value，闸机继续工作 |
| `SYS_PARTIAL_FAULT` | 部分故障 | 闸机全锁，交通灯切固定周期 |
| `SYS_CRITICAL` | 严重故障 | 全停，等外部复位 |

- 跳转表驱动（当前状态 × 事件 → 目标状态），无匹配则保持
- 8 个模块心跳槽位，清零检测模式
- 恢复：DEGRADED 5s 无异常 → OK；PARTIAL_FAULT 10s 正常 → DEGRADED；CRITICAL 不自动恢复

### 支撑模块

| 文件 | 职责 |
|------|------|
| `modbus_client.py` | Modbus RTU over TCP 客户端（功能码 05 写线圈，自实现 CRC16） |
| `yolo_reader.py` | 数据读取器：桥接上游检测器，输出标准化 dict；含文件模式后备 |
| `pir_reader.py` | PIR 人体红外读取（STM32 串口 ASCII 协议） |
| `data_output.py` | 标准化输出：1Hz 给配时、5s 给云平台，原子写文件 |
| `log_writer.py` | 环形缓冲区日志（deque, maxlen=100） |
| `mock_yolo_generator.py` | 假数据生成器，无硬件即可调试（含异常注入） |

---

## 主循环调度

100 ms 周期（`main.py`）：

| 周期 | 动作 |
|------|------|
| 每周期 | 读取 PIR（含消抖）、更新闸机状态机 |
| 每 3 周期 | 心跳巡检 |
| 每 10 周期（≈1s） | 读取上游数据、更新交通数据、1Hz 输出 |
| 每 50 周期（≈5s） | 5s 综合输出 + MQTT 上报 |

---

## 快速开始

```bash
pip install -r smart_traffic/requirements.txt

# 无硬件调试：终端 A 生成假数据，终端 B 跑主循环
python smart_traffic/mock_yolo_generator.py
python smart_traffic/main.py
```

> `yolo_reader.py` 默认 `mode="bridge"`，会尝试加载人员A 的检测器交付包（`../A人员交付文件3.0/`，**未包含在本仓库中**）。
> 该目录不存在时自动回退 `mode="file"`，读取 `YOLO_JSON_PATH` 指定的 JSON 文件——配合 `mock_yolo_generator.py` 即可完整跑通。

---

## 测试

```bash
cd smart_traffic && python -m pytest tests/ -v
```

76 个用例，覆盖三个核心模块的纯逻辑：

| 文件 | 用例数 | 覆盖内容 |
|------|--------|----------|
| `test_gate_safety.py` | 30 | 状态转换、人脸梯度决策、尾随/闯闸告警、PIR 消抖、线圈写入 |
| `test_sys_state.py` | 26 | 跳转表、心跳巡检、分级恢复、外部复位 |
| `test_traffic_data.py` | 20 | EWMA 收敛、密度换算、质量评分衰减、跳变检测、容错 |

> 说明：测试仅覆盖以上三个纯逻辑模块，通过依赖注入 + `unittest.mock` 隔离 Modbus。
> `modbus_client` / `pir_reader` / `data_output` / MQTT 上报及主循环调度**未纳入单元测试**。

---

## 部署（ARM64 Linux 边缘套件）

```bash
sudo bash smart_traffic/deploy.sh
```

脚本会安装依赖、部署到 `/opt/smart_traffic`、注册 systemd 服务并开机自启。

```bash
sudo systemctl status smart-traffic
sudo journalctl -u smart-traffic -f
```

### 跨平台通信桥接

边缘套件通过 TCP 把 **RTU 帧原样透传**到 Windows，再由串口送给 UUSIMA 仿真平台：

```
边缘套件 main.py ── TCP:5020 ──► tcp_bridge.py ── COM200 ──► UUSIMA
```

```bash
# Windows 端需管理员权限（占用串口）
python tcp_bridge/tcp_bridge.py --port 5020 --com COM200
```

> 注意：这是 **Modbus RTU over TCP**（保留从站地址与 CRC16），**不是标准 Modbus TCP**（无 MBAP 头）。
> 标准 Modbus TCP 主站无法直接连接本端口，需按 RTU 帧构造报文。

---

## 配置

所有可调参数集中在 `smart_traffic/config.py`。部署前需按实际环境修改：

| 参数 | 说明 |
|------|------|
| `GATE_TCP_HOST` / `GATE_TCP_PORT` | TCP 桥接地址（Windows 端 IP） |
| `PIR_SERIAL_PORT` | PIR 串口设备路径 |
| `THINGSBOARD_HOST` | 云平台 Broker 地址 |
| `GATE_COIL_ADDRESS` | 闸机电磁锁线圈地址 |

ThingsBoard 设备 Access Token **不写入仓库**，通过环境变量提供：

```bash
export THINGSBOARD_TOKEN="你的设备token"     # Windows: set THINGSBOARD_TOKEN=...
```

`deploy.sh` 会把该变量自动注入 systemd 服务；未设置时 MQTT 连接失败，**仅上云不可用，本地功能不受影响**。

---

## 技术栈

Python · Modbus RTU/TCP · MQTT (ThingsBoard) · pyserial · systemd · ARM64 Linux · pytest
