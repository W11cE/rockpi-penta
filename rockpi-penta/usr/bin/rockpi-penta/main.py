#!/usr/bin/env python3
import queue
import threading
import traceback

import fan
import misc



q = queue.Queue()
lock = threading.Lock()

# main.py (after removing OLED support)
q = queue.Queue()
action = {
    'none':   lambda: None,
    'switch': lambda: misc.fan_switch(),
    'reboot': lambda: misc.check_call('reboot'),
    'poweroff': lambda: misc.check_call('poweroff'),
    # 'slider': removed – no OLED, so slider does nothing
}

# Thread to handle button press events
def receive_key(q):
    while True:
        func = misc.get_func(q.get())      # e.g. 'switch', 'reboot', etc.
        action.get(func, lambda: None)()   # ignore any unknown actions

# Start the key scan and fan control threads
key_thread = threading.Thread(target=misc.watch_key, args=(q,), daemon=True)
handler_thread = threading.Thread(target=receive_key, args=(q,), daemon=True)
fan_thread = threading.Thread(target=fan.running, daemon=False)
key_thread.start(); handler_thread.start(); fan_thread.start()
fan_thread.join()  # keep main thread alive by joining the fan thread

