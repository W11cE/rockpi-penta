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
_FAN_DEFAULT = {
    "lv0": "35",      # °C where fan starts
    "lv1": "40",
    "lv2": "45",
    "lv3": "50",      # °C where fan is full
    "hysteresis": "2",
    "average_samples": "5",
    "dc_min": "0.001", # 0.001 ≈ off
    "hwmon_path": ""    # override auto-detection
}

_DEFAULTS = {
    "hat_fan": _FAN_DEFAULT.copy(),
    "cpu_fan": _FAN_DEFAULT.copy(),
}

# ────────── load / reload config  ───────────────────────────
def read_conf() -> dict:
    """Parse /etc/rockpi-penta.conf, return nested dict with fallbacks."""
    cfg = ConfigParser(inline_comment_prefixes=("#", ";"))
    cfg.read_dict(_DEFAULTS)                  # preload defaults
    try:
        cfg.read("/etc/rockpi-penta.conf")
    except Exception as e:
        logging.warning("config read error: %s – using defaults", e)

    conf = defaultdict(dict)
    for sect in ("hat_fan", "cpu_fan"):
        for key in ("lv0", "lv1", "lv2", "lv3",
                    "hysteresis", "average_samples", "dc_min"):
            conf[sect][key] = cfg.getfloat(sect, key)
        conf[sect]["hwmon_path"] = cfg.get(sect, "hwmon_path", fallback="")
    return conf

# load once at import
conf = read_conf()

# ────────── fan helper: temperature→duty conversion ─────────
# duty mapping table (0 = off, 1 = full)
_T2DC = OrderedDict([
    ("lv3", 1.00),    # full speed
    ("lv2", 0.75),
    ("lv1", 0.5),
    ("lv0", 0.25),
])
_STATE_DEFAULT = {"last_dc": None, "temp_hist": []}


def fan_temp2dc(t: float, cfg: dict | None = None,
                state: dict | None = None) -> float:
    """Map temperature to duty cycle (0=off, 1=full) using linear ramp.

    ``cfg`` is the per-fan configuration dictionary. ``state`` maintains
    per-fan history and hysteresis. When omitted a module-level default
    state is used, preserving the previous behaviour of a single global
    fan.
    """
    if cfg is None:
        cfg = conf.get("hat_fan", {})
    if state is None:
        state = _STATE_DEFAULT

    # moving average
    hist = state.setdefault("temp_hist", [])
    hist.append(t)
    N = int(cfg["average_samples"])
    if len(hist) > N:
        hist.pop(0)
    t_avg = sum(hist) / len(hist)

    t_min = cfg["lv0"]
    t_max = cfg["lv3"]
    dc_min = cfg["dc_min"]  # near-stop
    dc_max = 1.0              # full speed

    if t_avg <= t_min:
        dc = dc_min
    elif t_avg >= t_max:
        dc = dc_max
    else:
        span = t_max - t_min
        dc = dc_min + (t_avg - t_min) / span * (dc_max - dc_min)

    # hysteresis band
    hyster = cfg["hysteresis"]
    last_dc = state.get("last_dc")
    if last_dc is not None:
        thresh = (hyster / (t_max - t_min)) * (dc_max - dc_min)
        if abs(dc - last_dc) < thresh:
            dc = last_dc
    state["last_dc"] = dc
    return dc
