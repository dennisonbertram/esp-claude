# main.py — ESP-Claude entry point
#
# Uncomment the example you want to run.
# Boot.py handles WiFi connection before this runs.

import gc
gc.collect()

print(f"\n[main] Free memory: {gc.mem_free()} bytes")
print("[main] ESP-Claude starting...\n")

# --- Pick one example to run: ---

# Simplest demo — AI controls the onboard LED, no extra hardware
from examples.blinky import run

# Smart thermostat — DHT22 + relay
# from examples.thermostat import run

# Garden monitor — soil moisture + water pump
# from examples.garden import run

# Security monitor — PIR motion sensor + buzzer
# from examples.security import run

# Voice assistant — mic + speaker on ESP32-S3-BOX-3
# from examples.voice import run

# --- Or write your own: ---
# from my_app import run

run()
