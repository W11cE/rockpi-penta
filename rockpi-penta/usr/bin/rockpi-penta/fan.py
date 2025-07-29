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

class Pwm:
    def __init__(self, chip):
        self.period_value = None
        try:
            int(chip)
            chip = f'pwmchip{chip}'
        except ValueError:
            pass
        self.filepath = f"/sys/class/pwm/{chip}/pwm0/"
        try:
            with open(f"/sys/class/pwm/{chip}/export", 'w') as f:
                f.write('0')
        except OSError:
            print("Warning: init pwm error")
            traceback.print_exc()

    def period(self, ns: int):
        self.period_value = ns
        with open(os.path.join(self.filepath, 'period'), 'w') as f:
            f.write(str(ns))

    def period_us(self, us: int):
        self.period(us * 1000)

    def enable(self, t: bool):
        with open(os.path.join(self.filepath, 'enable'), 'w') as f:
            f.write(f"{int(t)}")

    def write(self, duty: float):
        assert self.period_value, "The Period is not set."
        with open(os.path.join(self.filepath, 'duty_cycle'), 'w') as f:
            f.write(f"{int(self.period_value * duty)}")


class Gpio:

    def tr(self):
        while True:
            self.line.set_value(1)
            time.sleep(self.value[0])
            self.line.set_value(0)
            time.sleep(self.value[1])

    def __init__(self, period_s):
        self.line = gpiod.Chip(os.environ['FAN_CHIP']).get_line(int(os.environ['FAN_LINE']))
        self.line.request(consumer='fan', type=gpiod.LINE_REQ_DIR_OUT)
        self.value = [period_s / 2, period_s / 2]
        self.period_s = period_s
        self.thread = threading.Thread(target=self.tr, daemon=True)
        self.thread.start()

    def write(self, duty):
        self.value[1] = duty * self.period_s
        self.value[0] = self.period_s - self.value[1]


def read_cpu_temp():
    with open('/sys/class/thermal/thermal_zone0/temp') as f:
        t = int(f.read().strip()) / 1000.0
    logging.debug("CPU temperature: %.1f°C", t)
    return t

def read_temp():
    #source = 'drives'
    source = conf['temperature']['source']
    cpu_temp = read_cpu_temp()
    drive_temps = read_drive_temps()
    if source == 'cpu':
        temp = cpu_temp
    elif source == 'drives':
        if drive_temps:
            temp = max(drive_temps)
        else:
            temp = cpu_temp  # Fallback if no drive temperatures are available
            logging.warning("No drive temperatures available; using CPU temperature")
    elif source == 'both':
        temps = [cpu_temp] + drive_temps if drive_temps else [cpu_temp]
        temp = max(temps)
    else:
        temp = cpu_temp  # Default to CPU temperature

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
    """
    Minimal wrapper around the kernel’s pwm-fan driver.
    write(duty) expects 0.0 … 1.0 (same as before) and maps it to 0…255.
    If your hardware uses the inverse duty sense, replace     value = …255
    with                                                     value = …(255 - …)
    """
    def __init__(self):
        self.pwm_file = _find_hwmon_pwm()           # e.g. /sys/class/hwmon/hwmon0/pwm1
        # Ensure automatic mode is off so we can write our own values:
        auto = self.pwm_file.with_name("pwm1_enable")
        if auto.exists():
            auto.write_text("1")                    # 1 = manual

    def write(self, duty: float):
        # If *duty 0 = full speed* keep the inversion below,
        # otherwise use  int(duty*255)
        value = max(0, min(255, int((1.0 - duty) * 255)))
        self.pwm_file.write_text(f"{value}")


def running():
    global pin

    try:
        # If the kernel pwm-fan driver is present we use HwmonFan
        pin = HwmonFan()
        logging.info("Using kernel pwm-fan driver (hwmon interface)")
    except FileNotFoundError:
        # Fallbacks: hardware PWM (own export) → software GPIO PWM
        if os.getenv("HARDWARE_PWM") == "1":
            chip = os.getenv("PWMCHIP", "0")
            pin = Pwm(chip)          #     << your original Pwm class >>
            pin.period_us(40)
            pin.enable(True)
            logging.info("Using raw PWM sysfs on %s", chip)
        else:
            pin = Gpio(0.025)
            logging.info("Using software GPIO PWM")

    while True:
        change_dc(get_dc())
        time.sleep(1)




if __name__ == '__main__':
    running()
