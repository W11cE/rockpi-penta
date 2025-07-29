#!/usr/bin/env python3
import os.path
import time
import traceback
import threading
import subprocess
import re
import logging
import sys

import gpiod

import misc

from pathlib import Path

pin = None

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG to see all messages
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout  # Output to stdout
)

conf = misc.read_conf()




def read_cpu_temp():
    with open('/sys/class/thermal/thermal_zone0/temp') as f:
        t = int(f.read().strip()) / 1000.0
    logging.debug("CPU temperature: %.1f°C", t)
    return t

def read_temp():
    source = misc.conf['temperature']['source']   # use global config
    cpu_temp = read_cpu_temp()
    drive_temps = read_drive_temps()
    if source == 'cpu':
        temp = cpu_temp
    elif source == 'drives':
        if drive_temps:
            temp = max(drive_temps)
        else:
            temp = cpu_temp
            logging.warning("No drive temperatures available; using CPU temperature")
    elif source == 'both':
        temps = [cpu_temp] + drive_temps if drive_temps else [cpu_temp]
        temp = max(temps)
    else:
        temp = cpu_temp
    logging.debug("Temperature used for fan control: %.1f°C", temp)
    return temp

drive_temp_cache = {'time': 0, 'temps': []}

def read_drive_temps():
    cache_duration = 60  # Seconds to cache temperatures
    current_time = time.time()
    if current_time - drive_temp_cache['time'] < cache_duration:
        logging.debug("Using cached drive temperatures: %s", drive_temp_cache['temps'])
        return drive_temp_cache['temps']
    drive_temps = []
    drives = list_non_boot_drives()
    logging.debug("Found drives: %s", drives)
    for drive in drives:
        try:
            # Run smartctl to get drive attributes
            output = subprocess.check_output(['smartctl', '-A', drive]).decode()
            temp = None
            # Parse the temperature from the output
            for line in output.split('\n'):
                if any(keyword in line for keyword in [
                    'Temperature_Celsius',
                    'Temperature_Internal',
                    'Temperature',
                    'Current Temperature',
                    'Drive Temperature',
                    'Airflow_Temperature_Cel',
                    'Composite Temperature'
                ]):
                    parts = line.split()
                    # Extract temperature value
                    temp_values = [int(s) for s in parts if s.isdigit()]
                    if temp_values:
                        temp = temp_values[-1]  # Use the last numeric value
                        break
                elif re.match(r'^\s*(190|194)\s', line):  # Common IDs
                    parts = line.split()
                    temp = int(parts[-1])
                    break
            if temp is not None:
                drive_temps.append(temp)
                logging.debug("Read temperature %d°C from %s", temp, drive)
            else:
                logging.warning("Temperature not found for %s", drive)
        except Exception as e:
            logging.error("Error reading temperature from %s: %s", drive, e)
    drive_temp_cache['time'] = current_time
    drive_temp_cache['temps'] = drive_temps
    logging.debug("Drive temperatures: %s", drive_temps)
    return drive_temps



def list_non_boot_drives():
    # Get the boot drive (e.g., /dev/mmcblk0)
    try:
        boot_drive = subprocess.check_output(
            "findmnt -n -o SOURCE / | sed 's/[0-9]*$//'", shell=True
        ).decode().strip()
    except subprocess.CalledProcessError:
        boot_drive = '/dev/mmcblk0'  # Default if detection fails

    # List all disk drives excluding the boot drive
    try:
        output = subprocess.check_output(
            "lsblk -nd -o NAME,TYPE | awk '$2==\"disk\" {print \"/dev/\"$1}'",
            shell=True
        ).decode()
        all_drives = output.strip().split('\n')
        # Exclude the boot drive
        drives = [drive for drive in all_drives if drive != boot_drive]
        return drives
    except Exception as e:
        logging.error("Error listing drives: %s", e)
        return []


def get_dc(cache={}):
    if misc.conf['run'].value == 0:
        return 0.999

    if time.time() - cache.get('time', 0) > 20:
        cache['time'] = time.time()
        cache['dc'] = misc.fan_temp2dc(read_temp())

    return cache['dc']


def change_dc(dc, cache={}):
    if dc != cache.get('dc'):
        cache['dc'] = dc
        pin.write(dc)
        # Calculate fan speed percentage
        fan_speed_percent = (1 - dc) * 100  # Adjust calculation if necessary
        logging.info("Fan speed changed to %.1f%%", fan_speed_percent)
        # Send status update to systemd if needed
        # daemon.notify("STATUS=Fan speed set to {:.1f}%".format(fan_speed_percent))

def _find_hwmon_pwm():
    """
    Return Path to the first writable */pwm1 produced by the pwm-fan driver.
    Raises FileNotFoundError if none is found.
    """
    for pwm_path in Path("/sys/class/hwmon").glob("hwmon*/pwm1"):
        try:
            with open(pwm_path, "r+"):
                return pwm_path
        except PermissionError:
            continue           # not writable by this user – keep looking
    raise FileNotFoundError("kernel pwm-fan hwmon node not found")

class HwmonFan:
    def __init__(self):
        self.pwm_file = _find_hwmon_pwm()            # find /sys/class/hwmon*/pwm1
        pwm_enable = self.pwm_file.with_name("pwm1_enable")
        if pwm_enable.exists():
            pwm_enable.write_text("1")               # set manual mode
    def write(self, duty: float):
        value = max(0, min(255, int((1.0 - duty) * 255)))
        self.pwm_file.write_text(str(value))


def running():
    global pin
    try:
        pin = HwmonFan()
        logging.info("Using kernel hwmon PWM interface for fan control")
        # Log config thresholds (lvMin, lvMax, etc.) on startup
        logging.info(
            "Fan control config: lvMin=%.1f°C, lvMax=%.1f°C, hysteresis=%.1f°C, "
            "avg_samples=%d, dc_min=%.3f, temp_source=%s",
            misc.conf['fan']['lvMin'], misc.conf['fan']['lvMax'],
            misc.conf['fan']['hysteresis'], misc.conf['fan']['average_samples'],
            misc.conf['fan']['dc_min'], misc.conf['temperature']['source']
        )
    except FileNotFoundError:
        logging.error("Hardware PWM fan interface not found. Exiting fan control thread.")
        return
    # Fan control loop (1 Hz)
    while True:
        change_dc(get_dc())
        time.sleep(1)




if __name__ == '__main__':
    running()
