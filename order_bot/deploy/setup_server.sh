#!/bin/bash
# Установка бота на сервер Ubuntu и автозапуск через systemd.
# Запуск (из папки бота на сервере):  bash deploy/setup_server.sh
set -e
cd "$(dirname "$0")/.."
BOT_DIR="$(pwd)"

echo "==> Installing Python..."
apt-get update -y
apt-get install -y python3-venv

echo "==> Installing libraries..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==> Creating service..."
cat > /etc/systemd/system/orderbot.service <<UNIT
[Unit]
Description=Order aggregator bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now orderbot
sleep 5
systemctl --no-pager status orderbot | head -n 15
echo
echo "==> Done. Bot is running and will start automatically after reboot."
echo "    Logs:    journalctl -u orderbot -f"
echo "    Restart: systemctl restart orderbot"
