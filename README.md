# ESP-Claude: AI Agent on a Microcontroller

A minimal but complete AI agent that runs on an ESP32 microcontroller using MicroPython. It implements the core agent loop (prompt → LLM → tool calls → loop) in ~300 lines of code, turning a $5 board into an AI-powered device.

## How It Works

```
┌─────────────┐     HTTPS      ┌──────────────┐
│   ESP32     │ ──────────────► │ Anthropic    │
│             │ ◄────────────── │ Claude API   │
│  MicroPython│                 └──────────────┘
│  Agent Loop │
│             │ ◄── Sensors (temp, humidity, light, etc.)
│  Tool       │ ──► Actuators (relays, LEDs, motors, etc.)
│  Dispatch   │ ──► Notifications (HTTP webhooks)
│             │
└─────────────┘
```

The agent loop is simple:
1. Build messages array with system prompt
2. POST to Claude API with tool definitions
3. If response has tool calls → execute them (read sensor, toggle relay, etc.)
4. Append results, go to step 2
5. No more tool calls → done, sleep, repeat

## Project Structure

```
esp-claude/
├── README.md           # This file
├── config.py           # WiFi credentials, API key, model settings
├── lib/
│   ├── agent.py        # Core agent loop + API client
│   └── tools.py        # Tool registry and base tools
├── examples/
│   ├── thermostat.py   # Smart thermostat (DHT22 + relay)
│   ├── garden.py       # Garden monitor (soil moisture + pump)
│   ├── security.py     # Security monitor (PIR + buzzer + webhook)
│   └── blinky.py       # Simplest demo — AI controls an LED
├── main.py             # Entry point (edit to pick your example)
└── boot.py             # WiFi connection on startup
```

## Hardware

**Minimum:** Any ESP32 board ($5-10)

**Recommended:** ESP32-S3 with PSRAM (8MB extra RAM for larger conversations)

**For examples:**
- `blinky.py` — Just the ESP32, uses the onboard LED
- `thermostat.py` — DHT22 sensor + relay module
- `garden.py` — Soil moisture sensor + water pump relay
- `security.py` — PIR motion sensor + buzzer

## Setup

### 1. Flash MicroPython

Download MicroPython for your board from https://micropython.org/download/

```bash
# Install esptool
pip install esptool

# Erase flash
esptool.py --chip esp32 erase_flash

# Flash MicroPython
esptool.py --chip esp32 write_flash -z 0x1000 ESP32_GENERIC-20250415-v1.25.0.bin
```

### 2. Configure

Edit `config.py` with your settings:

```python
WIFI_SSID = "YourNetwork"
WIFI_PASSWORD = "YourPassword"
ANTHROPIC_API_KEY = "sk-ant-..."
```

### 3. Upload Files

Using [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```bash
pip install mpremote

# Upload everything
mpremote cp config.py :config.py
mpremote cp boot.py :boot.py
mpremote cp main.py :main.py
mpremote mkdir lib
mpremote cp lib/agent.py :lib/agent.py
mpremote cp lib/tools.py :lib/tools.py

# Upload an example
mpremote mkdir examples
mpremote cp examples/blinky.py :examples/blinky.py
```

Or use [Thonny IDE](https://thonny.org/) for a GUI experience.

### 4. Run

```bash
# Connect and see output
mpremote run main.py

# Or just reset the board — boot.py + main.py run automatically
mpremote reset
```

## Memory Management

ESP32 has ~200KB usable RAM. The agent manages this by:

- **Message pruning**: Keeps only the last N messages (configurable)
- **Response size limits**: `max_tokens` capped at 1024 by default
- **Garbage collection**: Explicit `gc.collect()` after each API call
- **No streaming**: Uses single-response API (avoids SSE buffering)
- **Compact tool results**: Keep tool outputs short and focused

With an ESP32-S3 + PSRAM, you get ~8MB extra and can keep much longer conversations.

## Cost

At ~1000 tokens per agent cycle every 5 minutes:
- ~288 cycles/day
- ~288K tokens/day
- **~$0.30-0.90/day** depending on model (Haiku is cheapest)

Use `claude-haiku-3-5` for cheap, frequent checks. Use `claude-sonnet-4-5` for complex reasoning tasks.

## Limitations

- No streaming (SSE is complex on MicroPython) — uses synchronous API calls
- Conversation history is limited by RAM (~10-20 turns)
- No extensions/plugins system — modify the code directly
- Single-threaded (MicroPython limitation)
- HTTPS adds ~1-2 second overhead per call (TLS handshake)

## Inspiration

Built in the spirit of [pi coding agent](https://github.com/badlogic/pi-mono) — the minimal, extensible terminal coding harness. This project asks: what's the absolute minimum you need to run an AI agent? Turns out, not much.
