#!/usr/bin/env python3
import re
import os
import time
import subprocess
import multiprocessing as mp
import traceback
import logging
import sys

import gpiod
from configparser import ConfigParser
from collections import defaultdict, OrderedDict

cmds = {
    'blk': "lsblk | awk '{print $1}'",
    'up': "echo Uptime: `uptime | sed 's/.*up \\([^,]*\\), .*/\\1/'`",
    'temp': "cat /sys/class/thermal/thermal_zone0/temp",
    'ip': "hostname -I | awk '{printf \"IP %s\", $1}'",
    'cpu': "uptime | awk '{printf \"CPU Load: %.2f\", $(NF-2)}'",
    'men': "free -m | awk 'NR==2{printf \"Mem: %s/%sMB\", $3,$2}'",
    'disk': "df -h | awk '$NF==\"/\"{printf \"Disk: %d/%dGB %s\", $3,$2,$5}'"
}

# Global variables
temperature_history = []
last_dc = None

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to see all messages
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout  # Output to stdout
)


def check_output(cmd):
    return subprocess.check_output(cmd, shell=True).decode().strip()


def check_call(cmd):
    return subprocess.check_call(cmd, shell=True)


def get_blk():
    conf['disk'] = [x for x in check_output(cmds['blk']).strip().split('\n') if x.startswith('sd')]


def get_info(s):
    return check_output(cmds[s])


def get_cpu_temp():
    t = float(get_info('temp')) / 1000
    if conf['oled']['f-temp']:
        temp = "CPU Temp: {:.0f}°F".format(t * 1.8 + 32)
    else:
        temp = "CPU Temp: {:.1f}°C".format(t)
    return temp


def read_conf():
    conf = defaultdict(dict)

    try:
        cfg = ConfigParser()
        cfg.read('/etc/rockpi-penta.conf')
        # fan
        conf['fan']['lvMin'] = cfg.getfloat('fan', 'lvMin')
        conf['fan']['lvMax'] = cfg.getfloat('fan', 'lvMax')
        conf['fan']['hysteresis'] = cfg.getfloat('fan', 'hysteresis', fallback=2)
        conf['fan']['average_samples'] = cfg.getint('fan', 'average_samples', fallback=5)
        conf['fan']['dc_min'] = cfg.getfloat('fan', 'dc_min', fallback=0.999)
        # temperature
        conf['temperature']['source'] = cfg.get('temperature', 'source', fallback='cpu')
        # key
        conf['key']['click'] = cfg.get('key', 'click')
        conf['key']['twice'] = cfg.get('key', 'twice')
        conf['key']['press'] = cfg.get('key', 'press')
        # time
        conf['time']['twice'] = cfg.getfloat('time', 'twice')
        conf['time']['press'] = cfg.getfloat('time', 'press')
        # other
        conf['slider']['auto'] = cfg.getboolean('slider', 'auto')
        conf['slider']['time'] = cfg.getfloat('slider', 'time')
        conf['oled']['rotate'] = cfg.getboolean('oled', 'rotate')
        conf['oled']['f-temp'] = cfg.getboolean('oled', 'f-temp')
        
    except Exception:
        traceback.print_exc()
        # fan
        conf['fan']['lvMin'] = 35
        conf['fan']['lvMax'] = 50
        conf['fan']['hysteresis'] = 2
        conf['fan']['average_samples'] = 5
        conf['fan']['dc_min'] = 0.999
        # temperature
        conf['temperature']['source'] = 'cpu'
        # key
        conf['key']['click'] = 'slider'
        conf['key']['twice'] = 'switch'
        conf['key']['press'] = 'none'
        # time
        conf['time']['twice'] = 0.7  # second
        conf['time']['press'] = 1.8
        # other
        conf['slider']['auto'] = True
        conf['slider']['time'] = 10  # second
        conf['oled']['rotate'] = False
        conf['oled']['f-temp'] = False



    return conf


def read_key(pattern, size):
    CHIP_NAME = os.environ['BUTTON_CHIP']
    LINE_NUMBER = os.environ['BUTTON_LINE']

    s = ''
    chip = gpiod.Chip(str(CHIP_NAME))
    line = chip.get_line(int(LINE_NUMBER))
    line.request(consumer='hat_button', type=gpiod.LINE_REQ_DIR_OUT)
    line.set_value(1)

    while True:
        s = s[-size:] + str(line.get_value())
        for t, p in pattern.items():
            if p.match(s):
                return t
        time.sleep(0.1)


def watch_key(q=None):
    size = int(conf['time']['press'] * 10)
    wait = int(conf['time']['twice'] * 10)
    pattern = {
        'click': re.compile(r'1+0+1{%d,}' % wait),
        'twice': re.compile(r'1+0+1+0+1{3,}'),
        'press': re.compile(r'1+0{%d,}' % size),
    }

    while True:
        q.put(read_key(pattern, size))


def get_disk_info(cache={}):
    if not cache.get('time') or time.time() - cache['time'] > 30:
        info = {}
        cmd = "df -h | awk '$NF==\"/\"{printf \"%s\", $5}'"
        info['root'] = check_output(cmd)
        for x in conf['disk']:
            cmd = "df -Bg | awk '$1==\"/dev/{}\" {{printf \"%s\", $5}}'".format(x)
            info[x] = check_output(cmd)
        cache['info'] = list(zip(*info.items()))
        cache['time'] = time.time()

    return cache['info']


def slider_next(pages):
    conf['idx'].value += 1
    return pages[conf['idx'].value % len(pages)]


def slider_sleep():
    time.sleep(conf['slider']['time'])



def fan_temp2dc(t):
    global last_dc

    # Temperature Averaging
    temperature_history.append(t)
    N = conf['fan']['average_samples']
    if len(temperature_history) > N:
        temperature_history.pop(0)
    avg_temp = sum(temperature_history) / len(temperature_history)
    logging.debug("Average temperature over last %d samples: %.1f°C", N, avg_temp)

    # Define temperature and duty cycle ranges
    t_min = conf['fan']['lvMin']
    t_max = conf['fan']['lvMax']
    dc_min = conf['fan'].get('dc_min', 0.999)  # Default to 0.999 if not set
    dc_max = 0  # Fan at maximum speed

    # Linear interpolation
    if avg_temp <= t_min:
        dc = dc_min
    elif avg_temp >= t_max:
        dc = dc_max
    else:
        dc = dc_min - ((avg_temp - t_min) * (dc_min - dc_max) / (t_max - t_min))

    # Hysteresis
    hysteresis = conf['fan']['hysteresis']
    if last_dc is not None:
        dc_threshold = (hysteresis / (t_max - t_min)) * (dc_min - dc_max)
        if abs(dc - last_dc) < dc_threshold:
            dc = last_dc  # Keep previous duty cycle
            logging.debug("Duty cycle within hysteresis threshold; keeping last duty cycle")
    last_dc = dc

    # Calculate fan speed percentage
    fan_speed_percent = (1 - dc) * 100  # Assuming dc ranges from 0 (full speed) to 0.999 (off)
    logging.info("Calculated duty cycle: %.3f, Fan speed: %.1f%%", dc, fan_speed_percent)

    return dc



def fan_switch():
    conf['run'].value = not conf['run'].value


def get_func(key):
    return conf['key'].get(key, 'none')


conf = {'disk': [], 'idx': mp.Value('d', -1), 'run': mp.Value('d', 1)}
conf.update(read_conf())
