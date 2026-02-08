# thermostat.py — Smart thermostat with AI reasoning
#
# The AI reads temperature/humidity and decides whether to turn the heater
# on or off. It reasons about trends, comfort, and efficiency.
#
# Wiring:
#   - DHT22 data pin → GPIO 4 (with 10K pull-up resistor to 3.3V)
#   - Relay module IN → GPIO 16
#   - Relay module VCC → 5V, GND → GND

import dht
import machine
from lib.agent import ScheduledAgent
from lib.tools import ToolRegistry, make_params, no_params, register_system_tools
import config


# --- Hardware setup ---
DHT_PIN = 4
RELAY_PIN = 16
TARGET_TEMP_LOW = 20.0   # Celsius
TARGET_TEMP_HIGH = 22.0  # Celsius

sensor = dht.DHT22(machine.Pin(DHT_PIN))
relay = machine.Pin(RELAY_PIN, machine.Pin.OUT, value=0)
heater_on = False


# --- Tool definitions ---
def setup_tools():
    tools = ToolRegistry()
    register_system_tools(tools)

    def tool_read_temperature(params):
        try:
            sensor.measure()
            temp = sensor.temperature()
            hum = sensor.humidity()
            return f"Temperature: {temp:.1f}°C, Humidity: {hum:.1f}%"
        except OSError as e:
            return f"Sensor read failed ({e}). This is transient — please retry."

    tools.register(
        "read_temperature",
        "Read the current temperature (°C) and humidity (%) from the DHT22 sensor.",
        no_params(),
        tool_read_temperature,
    )

    def tool_set_heater(params):
        global heater_on
        on = params["on"]
        relay.value(1 if on else 0)
        heater_on = on
        return f"Heater turned {'ON' if on else 'OFF'}"

    tools.register(
        "set_heater",
        "Turn the heater on or off via the relay.",
        make_params({
            "on": {"type": "boolean", "description": "true to turn heater ON, false for OFF"},
        }, required=["on"]),
        tool_set_heater,
    )

    def tool_get_heater_status(params):
        return f"Heater is currently {'ON' if heater_on else 'OFF'}"

    tools.register(
        "get_heater_status",
        "Check if the heater is currently on or off.",
        no_params(),
        tool_get_heater_status,
    )

    return tools


# --- Main ---
def run():
    tools = setup_tools()

    agent = ScheduledAgent(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.MODEL,
        system_prompt=f"""You are a smart thermostat controller running on an ESP32.

Your job:
1. Read the current temperature and humidity
2. Check if the heater is on or off
3. Decide whether to turn the heater on or off
4. Briefly explain your reasoning

Rules:
- Target range: {TARGET_TEMP_LOW}°C to {TARGET_TEMP_HIGH}°C
- Turn heater ON if temperature drops below {TARGET_TEMP_LOW}°C
- Turn heater OFF if temperature rises above {TARGET_TEMP_HIGH}°C
- If between {TARGET_TEMP_LOW}°C and {TARGET_TEMP_HIGH}°C, keep current state
- Consider humidity too — high humidity makes it feel warmer
- Be energy-efficient: don't toggle rapidly

Keep your responses brief (1-2 sentences about what you did and why).""",
        tools=tools,
        max_tokens=config.MAX_TOKENS,
        max_messages=config.MAX_MESSAGES,
        debug=config.DEBUG,
        interval_seconds=config.AGENT_LOOP_SECONDS,
        recurring_prompt="Check the temperature and adjust the heater if needed.",
        on_response=lambda r: print(f"[thermostat] {r}"),
    )

    print(f"\n=== ESP-Claude Smart Thermostat ===")
    print(f"Target: {TARGET_TEMP_LOW}-{TARGET_TEMP_HIGH}°C")
    print(f"Checking every {config.AGENT_LOOP_SECONDS}s")
    print(f"Model: {config.MODEL}\n")

    agent.run_forever()


if __name__ == "__main__":
    run()
