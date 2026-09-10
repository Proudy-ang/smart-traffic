"""
TCP 串口桥接 — 智慧交通项目
运行在 Windows 上，将边缘套件发来的 TCP RTU 帧转发到 COM200（UUSIMA 仿真平台）。

用法:
    python tcp_bridge.py [--port 5020] [--com COM200]

架构:
    边缘套件 main.py ── TCP:5020 ──► tcp_bridge.py ── COM200 ──► UUSIMA
"""

import socket
import serial
import argparse
import threading


def handle_client(conn, ser):
    """处理单个 TCP 连接：收 RTU 帧 → 写 COM200 → 读响应 → 回 TCP。"""
    try:
        while True:
            # 收 RTU 帧（最长 256 字节）
            data = conn.recv(256)
            if not data:
                break

            print(f"TCP → COM: {data.hex(' ')}")

            # 写串口
            ser.write(data)

            # 读响应（8 字节）
            resp = ser.read(8)
            if resp:
                print(f"COM → TCP: {resp.hex(' ')}")
                conn.sendall(resp)
    except (socket.error, serial.SerialException, OSError):
        pass
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="TCP → COM 串口桥接")
    parser.add_argument("--port", type=int, default=5020, help="TCP 监听端口")
    parser.add_argument("--com", type=str, default="COM200", help="串口号")
    args = parser.parse_args()

    # 打开串口
    ser = serial.Serial(args.com, baudrate=9600, timeout=0.5)
    print(f"COM open: {args.com}")

    # 监听 TCP
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", args.port))
    server.listen(1)
    print(f"TCP listen: 0.0.0.0:{args.port}")
    print("Waiting for edge kit... (Ctrl+C to quit)")

    try:
        while True:
            conn, addr = server.accept()
            print(f"Client connected: {addr}")
            t = threading.Thread(target=handle_client, args=(conn, ser), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        ser.close()
        server.close()


if __name__ == "__main__":
    main()
