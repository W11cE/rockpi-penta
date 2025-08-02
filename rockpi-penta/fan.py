#!/usr/bin/env python3
"""
Rock 5 C – Penta SATA HAT fan controller (kernel pwm-fan only).
No OLED, no button, no GPIO fallback.
"""

import os, time, subprocess, logging, sys, re
from pathlib import Path
import misc                    # re-use existing misc.read_conf, fan_temp2dc

# ────────── logging ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)

# ────────── locate /sys/class/hwmon/.../pwm1 ─────────────────────
def find_hat_hwmon(node_name: str = "pwm-fan-hat", label: str = "pwmfan") -> Path:
    """Return ``Path('/sys/class/hwmon/hwmonX')`` for the HAT fan.

    Older kernels sometimes lack the ``device/of_node`` symlink used to
    identify the overlay device.  In that case, fall back to matching the
    device directory name which typically contains ``node_name``.
    """
    for d in Path("/sys/class/hwmon").glob("hwmon*"):        
        target = d.resolve()         
        if label in str(target):
            return d
    raise RuntimeError(f"hwmon entry with name '{label}' not found")

class HwmonFan:
    """Wrapper around the pwm-fan device created by the overlay."""

    def __init__(self, node_name: str = "pwm-fan-hat"):
        path = conf["fan"].get("hwmon_path")
        if path:
            hat = Path(path)
        else:
            hat = find_hat_hwmon(node_name)
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

def effective_temp() -> float:
    src = conf["temperature"]["source"]
    cpu = cpu_temp()
    drv = drive_temps()
    if src == "cpu":
        return cpu
    if src == "drives":
        return max(drv) if drv else cpu
    return max([cpu] + drv) if drv else cpu

# ────────── main loop ────────────────────────────────────────────
fan = HwmonFan()
logging.info("temperature source = %s", conf["temperature"]["source"])
logging.info("hwmon path = %s", fan.pwm.parent)

_prev = None
while True:
    temp = effective_temp()
    dc   = misc.fan_temp2dc(temp)
    if dc != _prev:
        fan.write(dc)
        _prev = dc
        logging.info("temp=%.1f °C  fan=%.0f %%", temp, dc*100)
    time.sleep(1)
