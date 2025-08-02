#!/usr/bin/env python3
"""
Rock 5 C – Penta SATA HAT fan controller (kernel pwm-fan only).
No OLED, no button, no GPIO fallback.
"""

import time, subprocess, logging, sys, re
from pathlib import Path
import misc                    # re-use existing misc.read_conf, fan_temp2dc

# ────────── logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)

# ────────── locate /sys/class/hwmon/.../pwm1 ─────────────────────
def find_hwmon(label: str) -> Path:
    """Return ``Path('/sys/class/hwmon/hwmonX')`` for a pwm-fan device.

    Older kernels sometimes lack the ``device/of_node`` symlink used to
    identify the overlay device.  In that case, fall back to matching the
    device directory name which typically contains ``label``.
    """
    for d in Path("/sys/class/hwmon").glob("hwmon*"):
        target = d.resolve()
        if label in str(target):
            return d
    raise RuntimeError(f"hwmon entry with name '{label}' not found")

class HwmonFan:
    """Wrapper around a pwm-fan device created by a device tree overlay."""

    def __init__(self, node_name: str = "pwm-fan-hat", hwmon_path: str = ""):
        if hwmon_path:
            hat = Path(hwmon_path)
        else:
            hat = find_hwmon(node_name)
        self.pwm = hat / "pwm1"
        self.en = hat / "pwm1_enable"
        if self.en.exists():
            self.en.write_text("1")  # manual mode

    # duty is 0.0 … 1.0 (1 = full, 0 = off)
    def write(self, duty: float):
        value = max(0, min(255, int(duty * 255)))
        self.pwm.write_text(str(value))

# ────────── temperature helpers ──────────────────────────────────
_CACHE = {"drives": [], "time": 0}
conf   = misc.read_conf()


def cpu_temp() -> float:
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        t = int(f.read().strip()) / 1000.0
    logging.debug("cpu=%.1f °C", t)
    return t

def drive_temps() -> list[float]:
    if time.time() - _CACHE["time"] < 60:
        return _CACHE["drives"]
    temps = []
    boot = subprocess.check_output("findmnt -n -o SOURCE / | sed 's/[0-9]*$//'",
                                   shell=True).decode().strip()
    drives = subprocess.check_output(
        "lsblk -nd -o NAME,TYPE | awk '$2==\"disk\" {print \"/dev/\"$1}'",
        shell=True).decode().split()
    for d in [x for x in drives if x != boot]:
        try:
            out = subprocess.check_output(["smartctl", "-A", d]).decode()
            m = re.search(r"Temperature.*?(\d+)", out)
            if m:
                temps.append(int(m.group(1)))
                logging.debug("%s=%s °C", d, m.group(1))
        except Exception as e:
            logging.warning("smartctl %s: %s", d, e)
    _CACHE.update(time=time.time(), drives=temps)
    return temps

# ────────── main loop ────────────────────────────────────────────
hat_fan = HwmonFan("pwm-fan-hat", conf["hat_fan"].get("hwmon_path"))
cpu_fan = HwmonFan("pwm-fan-cpu", conf["cpu_fan"].get("hwmon_path"))
logging.info("hat fan hwmon path = %s", hat_fan.pwm.parent)
logging.info("cpu fan hwmon path = %s", cpu_fan.pwm.parent)

_prev_hat = None
_prev_cpu = None
_state_hat: dict = {}
_state_cpu: dict = {}

while True:
    # CPU fan control
    t_cpu = cpu_temp()
    dc_cpu = misc.fan_temp2dc(t_cpu, conf["cpu_fan"], _state_cpu)
    if dc_cpu != _prev_cpu:
        cpu_fan.write(dc_cpu)
        _prev_cpu = dc_cpu
        logging.info("cpu temp=%.1f °C  cpu fan=%.0f %%", t_cpu, dc_cpu * 100)

    # Drive fan control
    drv = drive_temps()
    t_drv = max(drv) if drv else 0
    dc_hat = misc.fan_temp2dc(t_drv, conf["hat_fan"], _state_hat)
    if dc_hat != _prev_hat:
        hat_fan.write(dc_hat)
        _prev_hat = dc_hat
        logging.info("drive temp=%.1f °C  hat fan=%.0f %%", t_drv, dc_hat * 100)

    time.sleep(1)
