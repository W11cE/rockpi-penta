#!/usr/bin/env bash
set -e
echo "[1/3] apt packages…"
sudo apt update
sudo apt install -y python3 python3-venv smartmontools gpiod
echo "[2/3] venv…"
sudo python3 -m venv /opt/rockpi-penta-venv
sudo /opt/rockpi-penta-venv/bin/pip install -U pip
sudo /opt/rockpi-penta-venv/bin/pip install /usr/src/rockpi-penta   # adjust path
echo "[3/3] systemd…"
sudo install -Dm644 systemd/rockpi-penta.service /etc/systemd/system/rockpi-penta.service
sudo systemctl daemon-reload
sudo systemctl enable --now rockpi-penta.service
echo "✓ done  (logs: sudo journalctl -u rockpi-penta -f)"
