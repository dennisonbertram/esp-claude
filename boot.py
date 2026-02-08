# boot.py — Runs on every ESP32 startup
# Connects to WiFi before main.py executes

import network
import time

def connect_wifi():
    try:
        from config import WIFI_SSID, WIFI_PASSWORD, DEBUG
    except ImportError:
        print("ERROR: config.py not found. Copy config.py to the board and edit it.")
        return False

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        if DEBUG:
            print(f"[wifi] Already connected: {wlan.ifconfig()[0]}")
        return True

    if DEBUG:
        print(f"[wifi] Connecting to {WIFI_SSID}...")

    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    # Wait up to 15 seconds
    for i in range(30):
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            if DEBUG:
                print(f"[wifi] Connected! IP: {ip}")
            return True
        time.sleep(0.5)

    print(f"[wifi] Failed to connect to {WIFI_SSID}")
    return False


connected = connect_wifi()
