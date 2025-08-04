#!/usr/bin/env python3
"""
Rock 5 C – Penta SATA HAT fan controller (kernel pwm-fan only).
No OLED, no button, no GPIO fallback.
"""

import time, subprocess, logging, sys, re, glob
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

def drive_temps_hwmon() -> list[int]:
    """
    Return a list of °C temperatures read via the kernel’s drivetemp hwmon
    interface.  Works on kernels ≥ 5.6 once the drivetemp module is loaded.
    Falls back to an empty list if no drivetemp devices exist.
    """
    temps_c = []

    # Every hwmon device has a “name” file.  The drivetemp driver uses names
    # like “drivetemp0”, “drivetemp1”, …  Each of them exposes temp1_input.
    for name_file in glob.glob("/sys/class/hwmon/*/name"):
        try:
            if not Path(name_file).read_text().strip().startswith("drivetemp"):
                continue

            base = Path(name_file).parent
            # There can be temp1_input, temp2_input … for multi-LUN enclosures.
            for tfile in base.glob("temp*_input"):
                milli_c = int(tfile.read_text().strip())
                temps_c.append(milli_c // 1000)      # convert to plain °C
        except (FileNotFoundError, PermissionError, ValueError):
            # Ignore devices that disappear or return garbage while we iterate
            continue

    return temps_c

# ────────── main loop ────────────────────────────────────────────
hat_fan = HwmonFan("pwm-fan-hat", conf["hat_fan"].get("hwmon_path"))
cpu_fan = HwmonFan("pwm-fan-cpu", conf["cpu_fan"].get("hwmon_path"))
# cpu_fan = HwmonFan("pwm-fan-cpu", conf["cpu_fan"].get("hwmon_path"))
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
    drv = drive_temps_hwmon()
    if not drv:  # no drives detected – immediately drop to minimum speed
        t_drv = 0
        dc_hat = conf["hat_fan"]["dc_min"]
        _state_hat.clear()
        _state_hat["last_dc"] = dc_hat
    else:
        t_drv = max(drv)
        dc_hat = misc.fan_temp2dc(t_drv, conf["hat_fan"], _state_hat)
    if dc_hat != _prev_hat:
        hat_fan.write(dc_hat)
        _prev_hat = dc_hat
        logging.info("drive temp=%.1f °C  hat fan=%.0f %%", t_drv, dc_hat * 100)

    time.sleep(1)
