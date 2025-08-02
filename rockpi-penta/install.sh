#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEST=/usr/src/rockpi-penta

echo "[1/4] apt packages…"
sudo apt update
sudo apt install -y python3 python3-venv smartmontools gpiod

echo "[2/4] device-tree overlays…"
sudo armbian-add-overlay "$SCRIPT_DIR/rk3588-pwm-fan.dtbo"
sudo armbian-add-overlay "$SCRIPT_DIR/rk3588-pwm14-fan.dtbo"
if [ -f /boot/dtb/rockchip/overlay/rk3588-pwm14-m1.dtbo ]; then
    sudo armbian-add-overlay /boot/dtb/rockchip/overlay/rk3588-pwm14-m1.dtbo
fi
ARM_ENV=/boot/armbianEnv.txt
if grep -q "^user_overlays=" "$ARM_ENV"; then
    current=$(grep "^user_overlays=" "$ARM_ENV" | cut -d= -f2-)
    clean=$(echo "$current" | tr ' ' '\n' | grep -vE '^(rk3588-pwm-fan|rk3588-pwm14-fan)$' | xargs)
    sed -i "s/^user_overlays=.*/user_overlays=$clean rk3588-pwm-fan rk3588-pwm14-fan /" "$ARM_ENV"
else
    echo "user_overlays=rk3588-pwm-fan rk3588-pwm14-fan" >>"$ARM_ENV"
fi
if grep -q "^overlays=" "$ARM_ENV"; then
    current=$(grep "^overlays=" "$ARM_ENV" | cut -d= -f2-)
    clean=$(echo "$current" | tr ' ' '\n' | grep -vE '^(pwm14-m1)$' | xargs)
    sed -i "s/^overlays=.*/overlays=$clean pwm14-m1/" "$ARM_ENV"
else
    echo "overlays=pwm14-m1" >>"$ARM_ENV"
fi
sudo sync

echo "[3/4] venv & sources…"
sudo mkdir -p "$DEST"
sudo cp -rT "$SCRIPT_DIR" "$DEST"
sudo python3 -m venv /opt/rockpi-penta-venv
sudo /opt/rockpi-penta-venv/bin/pip install -U pip
sudo /opt/rockpi-penta-venv/bin/pip install "$DEST"

echo "[4/4] systemd…"
sudo install -Dm644 "$DEST/systemd/rockpi-penta.service" /etc/systemd/system/rockpi-penta.service
sudo systemctl daemon-reload
sudo systemctl enable --now rockpi-penta.service

echo "✓ done  (logs: sudo journalctl -u rockpi-penta -f)"
