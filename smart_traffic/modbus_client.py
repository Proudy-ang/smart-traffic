"""
Modbus RTU over TCP 客户端 — 智慧交通项目 人员B
RTU 帧不变，通过 TCP 透传到 Windows 上的 tcp_bridge.py → COM200 → UUSIMA。

报文:
  ON:  01 05 00 14 FF 00 CC 3E
  OFF: 01 05 00 14 00 00 8D CE
"""

import struct
import socket

from config import (
    GATE_TCP_HOST,
    GATE_TCP_PORT,
    GATE_SLAVE_ID,
    GATE_COIL_ADDRESS,
    GATE_TIMEOUT,
)


def _crc16(data: bytes) -> int:
    """Modbus CRC16"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_write_cmd(slave: int, addr: int, on: bool) -> bytes:
    """构建写单个线圈命令（功能码 05）。"""
    value = 0xFF00 if on else 0x0000
    payload = struct.pack(">BBHH", slave, 0x05, addr, value)
    crc = _crc16(payload)
    return payload + struct.pack("<H", crc)


class ModbusRTUClient:
    """Modbus RTU over TCP — RTU 帧通过 TCP 透传到 Windows 仿真平台。"""

    def __init__(self, host=None, port=None):
        self.host = host or GATE_TCP_HOST
        self.port = port or GATE_TCP_PORT
        self._sock = None

    # ---- 连接管理 ----

    def connect(self):
        """建立 TCP 连接，返回 True/False。"""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(GATE_TIMEOUT)
            self._sock.connect((self.host, self.port))
            return True
        except (socket.error, OSError):
            self._sock = None
            return False

    def disconnect(self):
        """关闭 TCP 连接。"""
        if self._sock:
            self._sock.close()
            self._sock = None

    def is_connected(self):
        return self._sock is not None

    # ---- 线圈操作 ----

    def write_coil(self, address=None, value=False):
        """
        写单个线圈（功能码 05），通过 TCP 发送 RTU 帧。

        Args:
            address: 线圈地址，默认 GATE_COIL_ADDRESS (0x14=20)
            value: True=开闸, False=关闸

        Returns:
            True 表示写入成功

        Raises:
            ConnectionError: 未连接
            TimeoutError: 写入失败
        """
        if address is None:
            address = GATE_COIL_ADDRESS

        if not self.is_connected():
            raise ConnectionError(f"TCP 未连接 ({self.host}:{self.port})")

        cmd = _build_write_cmd(GATE_SLAVE_ID, address, value)

        try:
            self._sock.sendall(cmd)
            resp = self._sock.recv(8)
            if len(resp) < 8:
                raise TimeoutError(f"TCP 响应超时: addr={address}, value={value}")
        except socket.timeout:
            raise TimeoutError(f"TCP 超时: addr={address}, value={value}")

        return True
