# blinky.py — Simplest example: AI controls the onboard LED
#
# No external hardware needed! The AI decides when and how to blink the LED.
# Great for testing your setup.
#
# Wiring: None (uses onboard LED, usually GPIO 2)

from lib.agent import Agent
from lib.tools import ToolRegistry, register_gpio_tools, register_system_tools
import config


def run():
    # Set up tools
    tools = ToolRegistry()
    register_system_tools(tools)
    register_gpio_tools(tools, allowed_pins=[2])  # Onboard LED only

    # Create agent
    agent = Agent(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.MODEL,
        system_prompt="""You are an AI controlling an ESP32 microcontroller's onboard LED on GPIO pin 2.

When the user asks you to do something with the LED, use the digital_write tool:
- Pin 2, value true = LED ON
- Pin 2, value false = LED OFF

You can also use the delay tool to create blinking patterns.
Be creative and have fun! Report what you did.""",
        tools=tools,
        max_tokens=config.MAX_TOKENS,
        max_messages=config.MAX_MESSAGES,
        debug=config.DEBUG,
    )

    # Interactive prompts
    print("\n=== ESP-Claude Blinky ===")
    print("Type a command (or 'quit' to exit)")
    print("Try: 'blink 3 times', 'SOS in morse code', 'heartbeat pattern'\n")

    while True:
        try:
            user_input = input("> ")
            if user_input.strip().lower() in ("quit", "exit", "q"):
                break
            if not user_input.strip():
                continue

            response = agent.prompt(user_input)
            if response:
                print(f"\n{response}\n")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

    print("Goodbye!")


if __name__ == "__main__":
    run()
