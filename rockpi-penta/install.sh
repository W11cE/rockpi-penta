#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST=/usr/src/rockpi-penta

echo "[1/3] apt packages…"
sudo apt update
sudo apt install -y python3 python3-venv smartmontools gpiod

echo "[2/3] venv & sources…"
sudo mkdir -p "$DEST"
sudo cp -rT "$SCRIPT_DIR" "$DEST"
sudo python3 -m venv /opt/rockpi-penta-venv
sudo /opt/rockpi-penta-venv/bin/pip install -U pip

echo "[3/3] systemd…"
sudo install -Dm644 "$DEST/systemd/rockpi-penta.service" /etc/systemd/system/rockpi-penta.service
sudo systemctl daemon-reload
sudo systemctl enable --now rockpi-penta.service

echo "✓ done  (logs: sudo journalctl -u rockpi-penta -f)"
