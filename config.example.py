# Copy this file to config.py and fill in your values

# WiFi
WIFI_SSID = "your-wifi-ssid"
WIFI_PASSWORD = "your-wifi-password"

# Anthropic API
ANTHROPIC_API_KEY = "sk-ant-your-key-here"

# OpenAI API (Whisper STT + TTS)
OPENAI_API_KEY = "sk-proj-your-openai-key-here"

# Model selection
# "claude-haiku-3-5-20241022"   — cheapest, fast, good for simple tasks
# "claude-sonnet-4-5-20250514"  — balanced, good reasoning
# "claude-sonnet-4-20250514"    — best reasoning
MODEL = "claude-haiku-3-5-20241022"

# Agent settings
MAX_TOKENS = 1024          # Max response tokens (keep low to save RAM)
MAX_MESSAGES = 12          # Max conversation history (older messages pruned)
AGENT_LOOP_SECONDS = 300   # How often to run the agent loop (5 min default)

API_TIMEOUT = 30           # API request timeout in seconds

# Debug
DEBUG = True               # Print debug info to REPL
