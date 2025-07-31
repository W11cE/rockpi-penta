#!/usr/bin/env python3
"""
misc.py – common helpers & configuration loader
Rock 5 C edition – no OLED / button
"""

import os, re, time, subprocess, traceback, logging
from configparser import ConfigParser
from collections  import defaultdict, OrderedDict

# ────────── logging ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ────────── shell helpers ───────────────────────────────────
def check_output(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True).decode().strip()

def check_call(cmd: str) -> int:
    return subprocess.check_call(cmd, shell=True)

# ────────── default config values ───────────────────────────
_DEFAULTS = {
    "fan": {
        "lv0": "35",      # °C where fan starts
        "lv1": "40",
        "lv2": "45",
        "lv3": "50",      # °C where fan is full
        "hysteresis": "2",
        "average_samples": "5",
        "dc_min": "0.999" # 0.999 ≈ off (invert later)
    },
    "temperature": {
        "source": "cpu"   # cpu | drives | both
    }
}

# ────────── load / reload config  ───────────────────────────
def read_conf() -> dict:
    """Parse /etc/rockpi-penta.conf, return nested dict with fallbacks."""
    cfg = ConfigParser()
    cfg.read_dict(_DEFAULTS)                  # preload defaults
    try:
        cfg.read("/etc/rockpi-penta.conf")
    except Exception as e:
        logging.warning("config read error: %s – using defaults", e)

    conf = defaultdict(dict)
    # fan section
    for key in ("lv0", "lv1", "lv2", "lv3",
                "hysteresis", "average_samples", "dc_min"):
        conf["fan"][key] = cfg.getfloat("fan", key)
    # temperature section
    conf["temperature"]["source"] = cfg.get(
        "temperature", "source", fallback="cpu").lower()
    return conf

# load once at import
conf = read_conf()

# ────────── fan helper: temperature→duty conversion ─────────
# duty mapping table (invert because hwmon 0=full,255=off)
_T2DC = OrderedDict([
    ("lv3", 0.0),    # full speed
    ("lv2", 0.25),
    ("lv1", 0.5),
    ("lv0", 0.75),
])
_last_dc = None
_temp_hist = []

def fan_temp2dc(t: float) -> float:
    """Map temperature to duty cycle (0=full, 1=stop) using linear ramp."""
    global _last_dc, _temp_hist

    # moving average
    _temp_hist.append(t)
    N = int(conf["fan"]["average_samples"])
    if len(_temp_hist) > N:
        _temp_hist.pop(0)
    t_avg = sum(_temp_hist) / len(_temp_hist)

    t_min = conf["fan"]["lv0"]
    t_max = conf["fan"]["lv3"]
    dc_min = conf["fan"]["dc_min"]  # near-stop
    dc_max = 0.0                    # full speed

    if t_avg <= t_min:
        dc = dc_min
    elif t_avg >= t_max:
        dc = dc_max
    else:
        span = t_max - t_min
        dc = dc_min - (t_avg - t_min) / span * (dc_min - dc_max)

    # hysteresis band
    hyster = conf["fan"]["hysteresis"]
    if _last_dc is not None:
        thresh = (hyster / (t_max - t_min)) * (dc_min - dc_max)
        if abs(dc - _last_dc) < thresh:
            dc = _last_dc
    _last_dc = dc
    return dc

# ────────── optional helper to toggle fan control on/off ────
_RUN = True
def fan_switch():
    """Toggle fan algorithm on/off; write duty=0.999 when off."""
    global _RUN
    _RUN = not _RUN
    state = "enabled" if _RUN else "disabled"
    logging.info("fan algorithm %s via fan_switch()", state)
    return _RUN
