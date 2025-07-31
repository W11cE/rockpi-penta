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
def find_hwmon_pwm() -> Path:
    for p in Path("/sys/class/hwmon").glob("hwmon*/pwm1"):
        try:
            with open(p, "r+"):
                logging.debug("using hwmon pwm node %s", p)
                return p
        except PermissionError:
            logging.warning("%s not writable – skipping", p)
    raise FileNotFoundError("no writable pwm1 found under /sys/class/hwmon")

class HwmonFan:
    """write(duty) expects 0.0…1.0, converts to 0…255 (inverted)."""
    def __init__(self):
        self.pwm_file = find_hwmon_pwm()
        ena = self.pwm_file.with_name("pwm1_enable")
        if ena.exists():
            ena.write_text("1")           # 1 = manual mode
        logging.info("hwmon pwm ready: %s", self.pwm_file)

    def write(self, duty: float):
        # Rock 5C polarity: 0 → full, 255 → stop → invert
        value = max(0, min(255, int((1.0 - duty) * 255)))
        self.pwm_file.write_text(str(value))

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

_prev = None
while True:
    temp = effective_temp()
    dc   = misc.fan_temp2dc(temp)
    if dc != _prev:
        fan.write(dc)
        _prev = dc
        logging.info("temp=%.1f °C  fan=%.0f %%", temp, (1-dc)*100)
    time.sleep(1)
