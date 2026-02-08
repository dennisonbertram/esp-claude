# garden.py — AI garden monitor
#
# Reads soil moisture and ambient light, decides when to water plants.
# The AI reasons about soil conditions, time of day, and watering history.
#
# Wiring:
#   - Soil moisture sensor analog → GPIO 34 (ADC)
#   - LDR (light sensor) → GPIO 35 (ADC) with voltage divider
#   - Water pump relay IN → GPIO 16
#   - Optional: DHT22 data → GPIO 4

import machine
import time
from lib.agent import ScheduledAgent
from lib.tools import ToolRegistry, make_params, no_params, register_system_tools
import config


# --- Hardware ---
MOISTURE_PIN = 34    # Analog — soil moisture sensor
LIGHT_PIN = 35       # Analog — LDR light sensor
PUMP_PIN = 16        # Digital — water pump relay

moisture_adc = machine.ADC(machine.Pin(MOISTURE_PIN))
moisture_adc.atten(machine.ADC.ATTN_11DB)

light_adc = machine.ADC(machine.Pin(LIGHT_PIN))
light_adc.atten(machine.ADC.ATTN_11DB)

pump = machine.Pin(PUMP_PIN, machine.Pin.OUT, value=0)
pump_on = False
last_watered = None
total_water_seconds = 0


# --- Tools ---
def setup_tools():
    tools = ToolRegistry()
    register_system_tools(tools)

    def tool_read_soil_moisture(params):
        raw = moisture_adc.read()
        # Typical capacitive sensor: ~1500 (wet) to ~3500 (dry)
        # Normalize to 0-100% where 100% = wet
        pct = max(0, min(100, (3500 - raw) * 100 // 2000))
        return f"Soil moisture: {pct}% (raw ADC: {raw}/4095). Below 30% means dry, above 70% means wet."

    tools.register(
        "read_soil_moisture",
        "Read soil moisture level. Returns percentage (0%=bone dry, 100%=saturated) and raw ADC value.",
        no_params(),
        tool_read_soil_moisture,
    )

    def tool_read_light_level(params):
        raw = light_adc.read()
        # Normalize: higher raw = more light (depends on circuit)
        pct = raw * 100 // 4095
        if pct > 70:
            desc = "bright (direct sunlight)"
        elif pct > 40:
            desc = "moderate (indirect light)"
        elif pct > 15:
            desc = "dim (shade/cloudy)"
        else:
            desc = "dark (night/indoor)"
        return f"Light level: {pct}% ({desc}, raw ADC: {raw}/4095)"

    tools.register(
        "read_light_level",
        "Read ambient light level. Returns percentage and description.",
        no_params(),
        tool_read_light_level,
    )

    def tool_water_plants(params):
        global pump_on, last_watered, total_water_seconds
        seconds = params.get("seconds", 5)
        if seconds > 30:
            return "Error: max 30 seconds per watering to prevent flooding"
        if seconds < 1:
            return "Error: minimum 1 second"

        pump.value(1)
        pump_on = True
        time.sleep(seconds)
        pump.value(0)
        pump_on = False

        last_watered = time.time()
        total_water_seconds += seconds
        return f"Watered for {seconds} seconds. Total watering this session: {total_water_seconds}s"

    tools.register(
        "water_plants",
        "Turn on the water pump for a specified number of seconds (1-30). Use short bursts.",
        make_params({
            "seconds": {"type": "integer", "description": "Seconds to run pump (1-30)"},
        }, required=["seconds"]),
        tool_water_plants,
    )

    def tool_watering_history(params):
        global last_watered, total_water_seconds
        if last_watered is None:
            return "No watering recorded yet this session."
        elapsed = time.time() - last_watered
        mins = elapsed // 60
        return (
            f"Last watered: {int(mins)} minutes ago. "
            f"Total watering this session: {total_water_seconds} seconds."
        )

    tools.register(
        "get_watering_history",
        "Check when plants were last watered and total watering time.",
        no_params(),
        tool_watering_history,
    )

    return tools


# --- Main ---
def run():
    tools = setup_tools()

    agent = ScheduledAgent(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.MODEL,
        system_prompt="""You are an AI garden monitor running on an ESP32 microcontroller.

Your job:
1. Check soil moisture level
2. Check ambient light level
3. Check watering history
4. Decide if plants need water

Watering guidelines:
- Water when soil moisture drops below 30%
- Don't water if moisture is above 50% (already fine)
- Prefer watering during low-light periods (less evaporation)
- Use short bursts (3-10 seconds) — you can always water more next cycle
- Don't water if you watered recently (within 30 minutes) unless soil is very dry (<15%)
- Never exceed 30 seconds in a single watering

Keep responses brief: what you measured, what you decided, and why.""",
        tools=tools,
        max_tokens=config.MAX_TOKENS,
        max_messages=config.MAX_MESSAGES,
        debug=config.DEBUG,
        interval_seconds=config.AGENT_LOOP_SECONDS,
        recurring_prompt="Check the garden conditions and water if needed.",
        on_response=lambda r: print(f"[garden] {r}"),
    )

    print("\n=== ESP-Claude Garden Monitor ===")
    print(f"Checking every {config.AGENT_LOOP_SECONDS}s")
    print(f"Model: {config.MODEL}\n")

    agent.run_forever()


if __name__ == "__main__":
    run()
