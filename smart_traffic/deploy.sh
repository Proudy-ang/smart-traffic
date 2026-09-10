#!/bin/bash
# 智慧交通 人员B — ARM64 (aarch64) Linux 部署脚本
# 在边缘套件上以 root 权限执行: sudo bash deploy.sh

set -e

APP_DIR="/opt/smart_traffic"
SERVICE_NAME="smart-traffic"

echo "=== 智慧交通 人员B 部署 ==="
echo "架构: $(uname -m)"

# ---- 1. 系统依赖（apt） ----
echo "[1/4] 安装系统依赖..."
sudo apt update
sudo apt install -y python3-opencv python3-pip python3-serial

# ---- 2. Python 依赖（pip） ----
echo "[2/4] 安装 Python 依赖..."
cd "$(dirname "$0")"
pip install ultralytics pyserial paho-mqtt joblib pytest

# 避免 pip 的 opencv 覆盖 apt 的
pip uninstall opencv-python opencv-contrib-python -y 2>/dev/null || true

# numpy 版本：apt 自带旧版，升级到 ultralytics 兼容版本
pip install "numpy>=1.23.5,<2"

# ---- 3. 部署代码 ----
echo "[3/4] 部署代码到 $APP_DIR..."
sudo mkdir -p "$APP_DIR" "$APP_DIR/output"
sudo cp -r ./* "$APP_DIR/"
sudo chmod +x "$APP_DIR/deploy.sh"

# ---- 4. systemd 服务 ----
echo "[4/4] 配置 systemd 服务..."

# ThingsBoard Token 从环境变量注入，不硬编码在代码里
if [ -n "${THINGSBOARD_TOKEN:-}" ]; then
    TOKEN_LINE="Environment=\"THINGSBOARD_TOKEN=${THINGSBOARD_TOKEN}\""
    echo "  ThingsBoard Token: 已从环境变量注入"
else
    TOKEN_LINE=""
    echo "  警告: 未设置 THINGSBOARD_TOKEN，上云功能将不可用（本地功能正常）"
fi

cat << EOF | sudo tee /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=智慧交通 人员B 主程序
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/smart_traffic
ExecStart=/usr/bin/python3 /opt/smart_traffic/main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
Environment="PYTHONUNBUFFERED=1"
${TOKEN_LINE}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service
sudo systemctl restart ${SERVICE_NAME}.service

echo ""
echo "=== 部署完成 ==="
echo "查看状态:  sudo systemctl status ${SERVICE_NAME}"
echo "查看日志:  sudo journalctl -u ${SERVICE_NAME} -f"
