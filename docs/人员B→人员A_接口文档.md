# 人员B → 人员A 交付文档

**版本**：v1.0
**日期**：2026-07-23

---

## 1. 我们之间的关系

```
人员A（你）                          人员B（我）
─────────                          ─────────
摄像头 → 人脸检测 + 车辆检测          拿到你的原始数据
        │                                │
        │  你的 face_detector.py          │
        │  你的 vehicle_detector.py       │
        │                                │
        ├──────────────────────────►     │
        │   YoloReader 直接调你的 API     │
        │                                │
        │                          EWMA平滑 + 流量换算
        │                          闸机安全状态机
        │                          系统健康监控
        │                                │
        │◄──────────────────────────     │
        │   交通态势数据（1秒1次）         │
        │                                │
   红绿灯配时                          继续处理 → 给D/E
```

**你负责"看"，我负责"算"。你把画面变成数字，我把数字变成工程指标。**

---

## 2. 我这边怎么用你的代码（你不需要做任何事）

我的 `yolo_reader.py` 启动时自动加载你的两个模块：

```python
from face_detector import FaceDetector        # 你的 v2.0
from vehicle_detector import VehicleCounter    # 你的 v2.0
```

然后每帧调用：

```python
# 人脸（你的 YuNet + LBPH）
faces = face_detector.detect(frame)               # → [(x1,y1,x2,y2,conf), ...]
label, score = recognizer.predict(face_roi)       # → (label, score)

# 车辆（你的 YOLO11n）
result = vehicle_counter.update(frame)            # → {total, current, vehicles: [...]}
```

**所以你的 `face_detector.py` 和 `vehicle_detector.py` 接口不要变。** 模型路径保持不变，API 保持不变，我就一直能用。

---

## 3. 我返回给你的数据（你做配时用）

**频率**：每秒 1 次
**格式**：JSON

```json
{
  "timestamp_ms": 1721635201000,
  "lanes": [
    {
      "lane_id": 0,
      "person_density": 0.0000,
      "vehicle_density": 0.1000,
      "person_flow_rate": 0.00,
      "vehicle_flow_rate": 12.00,
      "data_quality": 100
    },
    {
      "lane_id": 1,
      "person_density": 0.0000,
      "vehicle_density": 0.0333,
      "person_flow_rate": 0.00,
      "vehicle_flow_rate": 4.00,
      "data_quality": 100
    }
  ]
}
```

**字段说明**：

| 字段 | 含义 | 你怎么用 |
|------|------|----------|
| `lane_id` | 车道编号（0=左，1=右） | 区分两个方向的车流 |
| `vehicle_flow_rate` | 车流量（**辆/分钟**） | **这是你配时的核心输入**：值越大 → 绿灯越长 |
| `vehicle_density` | 排队密度（辆/m²） | 判断拥堵程度 |
| `person_flow_rate` | 人流量（目前填0，如果你后续加了人流检测就有值） | 人行绿灯配时 |
| `data_quality` | 数据可信度 0-100 | **<30 就不要用来配时了**，切固定周期 |

**和你的原始数据的区别**：

| | 你的原始输出 | 我处理后返回给你的 |
|------|-------------|-------------------|
| 数值 | `current: 5`（这一帧画面里有5辆） | `vehicle_flow_rate: 12.0`（每分钟过12辆） |
| 稳定性 | 每帧跳动（5→3→8→0→6） | EWMA平滑，不会跳 |
| 可靠性 | 不知道 | 有 data_quality 打分 |

**配时伪代码示例**：

```python
flow_rate = data["lanes"][0]["vehicle_flow_rate"]
quality = data["lanes"][0]["data_quality"]

if quality < 30:
    green_time = DEFAULT_GREEN  # 数据不可靠，用固定值
elif flow_rate > 20:
    green_time = 60   # 车多，绿灯给长
elif flow_rate > 10:
    green_time = 40
else:
    green_time = 20   # 车少，绿灯缩短
```

---

## 4. 你当前交付中缺的东西（不影响我运行，但影响数据完整度）

| 缺失项 | 影响 | 优先级 |
|--------|------|--------|
| 车道**人流**检测 | `person_flow_rate` 暂时恒为 0，人行道配时无数据支撑 | 低（先跑通车流） |
| `person_count` in lanes | 同上 | 低 |

如果你后续在 `vehicle_detector.py` 里加了行人检测（COCO class 0），我这边不需要改任何代码——`_build_lanes()` 已经留好了 `person_count` 槽位。

---

## 5. 你不需要关心的（我这边已经做好的）

以下是我的内部模块，跟你无关：

- EWMA 平滑算法、密度计算公式
- 闸机 8 状态机（尾随检测、闯闸告警、陌生人拦截）
- 系统 4 级健康监控（SYS_OK → DEGRADED → PARTIAL_FAULT → CRITICAL）
- Modbus TCP 控制 ADAM-4150（给人员D 的）
- 5 秒周期云平台数据输出（给人员E 的）
- 76 个单元测试
- 假数据生成器（你不在的时候我自己调）

---

## 6. 你现在需要对接的事项

1. **什么都不用改**——你现有的 `face_detector.py` + `vehicle_detector.py` 接口完全够用
2. **拿到我的数据**——我通过 `DataOutput.get_1hz_output()` 产出 JSON 字符串，当前打印到控制台。你希望用什么方式接收？（直接内存调用 / 文件 / socket / 其他），告诉我，我来对接
3. **配时算法**——你拿到 `vehicle_flow_rate` 后自己设计红绿灯配时逻辑，这是你的模块
