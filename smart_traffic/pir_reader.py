"""
PIR 人体红外传感器读取器 — 智慧交通项目 人员B
STM32 → 串口 ASCII 文本协议：每行包含 "红外传感器值: 1" 或 "红外传感器值: 0"
"""

import re
import serial
from config import PIR_SERIAL_PORT


class PirReader:
    """PIR 传感器读取器 — ASCII 文本协议"""

    def __init__(self, port=None):
        self.port = port or PIR_SERIAL_PORT
        self._ser = None
        self._value = 0         # 最新值（0/1）
        self._buf = b""         # 接收缓冲区

    # ---- 连接管理 ----

    def open(self) -> bool:
        try:
            self._ser = serial.Serial(self.port, baudrate=115200, timeout=0.1)
            return self.is_open()
        except serial.SerialException:
            return False

    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def close(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
            self._ser = None

    # ---- 数据读取 ----

    def read(self):
        """
        读取 PIR 状态（非阻塞，有数据就读，没数据返回上次值）。

        Returns:
            bool | None — True=有人, False=没人, None=还没读到过数据
        """
        if not self.is_open():
            return None

        try:
            n = self._ser.in_waiting
            if n > 0:
                self._buf += self._ser.read(n)
                lines = self._buf.split(b"\n")
                self._buf = lines.pop()  # 最后一段可能不完整

                for line in lines:
                    try:
                        text = line.decode("utf-8", errors="ignore").strip()
                    except Exception:
                        continue
                    if not text:
                        continue
                    match = re.search(r"红外传感器值:\s*(\d+)", text)
                    if match:
                        self._value = int(match.group(1))

        except (serial.SerialException, OSError):
            return None

        if self._value == 1:
            return True
        elif self._value == 0:
            return False
        return None
