# security.py — AI security monitor
#
# Uses a PIR motion sensor to detect movement. When motion is detected,
# the AI decides what to do: sound an alarm, send a webhook notification,
# or ignore it (based on time, frequency, etc.)
#
# This is an EVENT-DRIVEN example — the agent only wakes up when something
# happens, rather than polling on a schedule.
#
# Wiring:
#   - PIR sensor OUT → GPIO 14
#   - Buzzer → GPIO 27
#   - Optional: LED → GPIO 2 (onboard)

import machine
import time
from lib.agent import EventDrivenAgent
from lib.tools import (
    ToolRegistry, make_params, no_params,
    register_system_tools, register_webhook_tools,
)
import config


# --- Hardware ---
PIR_PIN = 14
BUZZER_PIN = 27
LED_PIN = 2

pir = machine.Pin(PIR_PIN, machine.Pin.IN)
buzzer = machine.Pin(BUZZER_PIN, machine.Pin.OUT, value=0)
led = machine.Pin(LED_PIN, machine.Pin.OUT, value=0)

# State
alarm_active = False
motion_log = []  # List of timestamps
MAX_LOG = 20


# --- Tools ---
def setup_tools():
    tools = ToolRegistry()
    register_system_tools(tools)
    register_webhook_tools(tools)

    def tool_sound_alarm(params):
        global alarm_active
        duration = params.get("seconds", 3)
        if duration > 10:
            duration = 10
        alarm_active = True
        # Beep pattern
        for i in range(duration * 2):
            buzzer.value(1)
            led.value(1)
            time.sleep_ms(250)
            buzzer.value(0)
            led.value(0)
            time.sleep_ms(250)
        alarm_active = False
        return f"Alarm sounded for {duration} seconds (beeping pattern)"

    tools.register(
        "sound_alarm",
        "Sound the buzzer alarm with a beeping pattern. Duration 1-10 seconds.",
        make_params({
            "seconds": {"type": "integer", "description": "Alarm duration in seconds (1-10)"},
        }, required=["seconds"]),
        tool_sound_alarm,
    )

    def tool_flash_led(params):
        times = min(params.get("times", 3), 20)
        for i in range(times):
            led.value(1)
            time.sleep_ms(100)
            led.value(0)
            time.sleep_ms(100)
        return f"LED flashed {times} times"

    tools.register(
        "flash_led",
        "Flash the onboard LED. Use for silent/subtle alerts.",
        make_params({
            "times": {"type": "integer", "description": "Number of flashes (1-20)"},
        }, required=["times"]),
        tool_flash_led,
    )

    def tool_get_motion_log(params):
        global motion_log
        if not motion_log:
            return "No motion events recorded."
        now = time.time()
        entries = []
        for ts in motion_log[-10:]:
            ago = int(now - ts)
            if ago < 60:
                entries.append(f"{ago}s ago")
            else:
                entries.append(f"{ago // 60}m ago")
        recent_count = sum(1 for ts in motion_log if now - ts < 300)
        return (
            f"Last {len(entries)} events: {', '.join(entries)}. "
            f"Events in last 5 min: {recent_count}. "
            f"Total recorded: {len(motion_log)}."
        )

    tools.register(
        "get_motion_log",
        "Get recent motion detection history. Shows timestamps and frequency.",
        no_params(),
        tool_get_motion_log,
    )

    def tool_get_pir_status(params):
        return f"PIR sensor currently reads: {'MOTION' if pir.value() else 'CLEAR'}"

    tools.register(
        "get_pir_status",
        "Read the current PIR sensor state (motion detected or clear).",
        no_params(),
        tool_get_pir_status,
    )

    return tools


# --- Main ---
def run():
    global motion_log

    tools = setup_tools()

    agent = EventDrivenAgent(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.MODEL,
        system_prompt="""You are an AI security monitor running on an ESP32 microcontroller.

When motion is detected, you must decide what to do:

Available actions:
- sound_alarm: Loud buzzer beeping (for real threats)
- flash_led: Silent visual alert (for minor/expected events)
- http_post: Send webhook notification to an external service
- get_motion_log: Check recent motion history for patterns

Decision guidelines:
- Check the motion log first to understand the pattern
- Single isolated motion event → flash LED, maybe it's a pet or wind
- Multiple events in quick succession (3+ in 5 min) → sound alarm, likely an intruder
- Always send a webhook notification for sustained activity
- If it's frequent but regular (every few minutes) → probably normal activity, just log

Webhook URL for notifications: http://your-server.com/security-webhook
(Change this in the system prompt for your setup)

Keep responses brief: what happened, what you decided, and why.""",
        tools=tools,
        max_tokens=config.MAX_TOKENS,
        max_messages=config.MAX_MESSAGES,
        debug=config.DEBUG,
        reset_after_event=True,
    )

    print("\n=== ESP-Claude Security Monitor ===")
    print(f"PIR sensor on GPIO {PIR_PIN}")
    print(f"Model: {config.MODEL}")
    print("Waiting for motion...\n")

    last_trigger = 0
    COOLDOWN = 10  # Minimum seconds between triggers

    while True:
        try:
            if pir.value() == 1:
                now = time.time()

                # Cooldown to avoid rapid-fire triggers
                if now - last_trigger < COOLDOWN:
                    time.sleep_ms(100)
                    continue

                last_trigger = now
                motion_log.append(now)

                # Trim log
                if len(motion_log) > MAX_LOG:
                    motion_log = motion_log[-MAX_LOG:]

                # Count recent events
                recent = sum(1 for ts in motion_log if now - ts < 300)

                print(f"\n[!] Motion detected! (event #{len(motion_log)}, {recent} in last 5min)")

                # Wake the AI
                response = agent.handle_event(
                    "motion_detected",
                    f"Motion detected by PIR sensor. "
                    f"This is event #{len(motion_log)}. "
                    f"There have been {recent} events in the last 5 minutes."
                )

                if response:
                    print(f"[AI] {response}")

            time.sleep_ms(100)

        except KeyboardInterrupt:
            print("\nShutting down...")
            buzzer.value(0)
            led.value(0)
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    run()
