#!/bin/bash
# Upload all files to ESP32 via mpremote
# Usage: ./upload.sh

set -e

echo "=== Uploading ESP-Claude to ESP32 ==="

# Create directories
mpremote mkdir :lib 2>/dev/null || true
mpremote mkdir :examples 2>/dev/null || true

# Core files
echo "Uploading core files..."
mpremote cp config.py :config.py
mpremote cp boot.py :boot.py
mpremote cp main.py :main.py

# Library
echo "Uploading library..."
mpremote cp lib/agent.py :lib/agent.py
mpremote cp lib/tools.py :lib/tools.py

# Examples
echo "Uploading examples..."
mpremote cp examples/blinky.py :examples/blinky.py
mpremote cp examples/thermostat.py :examples/thermostat.py
mpremote cp examples/garden.py :examples/garden.py
mpremote cp examples/security.py :examples/security.py

echo ""
echo "=== Done! ==="
echo "Edit config.py on the board with your WiFi and API key:"
echo "  mpremote edit :config.py"
echo ""
echo "Or reset the board to start:"
echo "  mpremote reset"
