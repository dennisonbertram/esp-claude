# voice.py -- Voice assistant for ESP32-S3-BOX-3
#
# Full pipeline: Record -> Whisper STT -> Claude Agent -> OpenAI TTS -> Play
#
# Hardware: ES7210 mic codec, ES8311 speaker codec, NS4150B PA amplifier
# Requires WiFi connection (handled by boot.py)

import gc
import time
from machine import I2C, Pin

from lib.codec import ES7210, ES8311, start_mclk, stop_mclk
from lib import audio
from lib import speech
from lib.agent import Agent
from lib.tools import ToolRegistry, register_system_tools, register_webhook_tools
import config

# Hardware pins (ESP32-S3-BOX-3)
_SDA_PIN = 8
_SCL_PIN = 18
_MCLK_PIN = 2
_PA_PIN = 46

# Single fixed MCLK -- both codecs derive their sample rates internally
_MCLK_FREQ = 12288000  # 256 * 48000

_SYSTEM_PROMPT = (
    "You are a helpful voice assistant running on an ESP32 microcontroller. "
    "Keep ALL responses to 1-2 sentences maximum. "
    "Never use emoji or special characters. "
    "Be concise and conversational."
)


def run():
    """Main entry point for the voice agent."""

    mclk_pwm = None
    mic = None
    spk = None
    pa = None

    try:
        # -- 1. Initialize hardware --
        print("[voice] Initializing hardware...")

        # I2C bus shared by both codecs
        i2c = I2C(0, sda=Pin(_SDA_PIN), scl=Pin(_SCL_PIN), freq=400000)
        if config.DEBUG:
            devices = i2c.scan()
            print("[voice] I2C devices:", [hex(d) for d in devices])

        # Start single fixed MCLK (12.288 MHz) -- never changes at runtime
        mclk_pwm = start_mclk(pin=_MCLK_PIN, freq=_MCLK_FREQ)
        time.sleep_ms(500)

        # Initialize speaker codec first -- ES7210 init can disturb I2C bus
        spk = ES8311(debug=config.DEBUG)
        spk.init(i2c, sample_rate=24000, mclk=_MCLK_FREQ)
        spk.set_volume(80)

        # Initialize mic codec (ES7210) at 16kHz with 12.288 MHz MCLK
        mic = ES7210(debug=config.DEBUG)
        mic.init(i2c, sample_rate=16000, mclk=_MCLK_FREQ)

        # Configure PA amplifier pin (kept off until playback)
        pa = Pin(_PA_PIN, Pin.OUT)
        pa.value(0)

        gc.collect()
        print("[voice] Hardware initialized")

        # -- 2. Create agent --
        tools = ToolRegistry()
        register_system_tools(tools)
        register_webhook_tools(tools)

        agent = Agent(
            api_key=config.ANTHROPIC_API_KEY,
            model=config.MODEL,
            system_prompt=_SYSTEM_PROMPT,
            tools=tools,
            max_tokens=config.MAX_TOKENS,
            max_messages=config.MAX_MESSAGES,
            api_timeout=config.API_TIMEOUT,
            debug=config.DEBUG,
        )

        gc.collect()
        if config.DEBUG:
            print("[voice] Free memory: {} bytes".format(gc.mem_free()))

        # Pre-allocate recording buffer to avoid per-cycle heap fragmentation
        rec_buf = bytearray(16000 * 2 * 10)  # 10s at 16kHz 16-bit mono

        print("\n=== ESP-Claude Voice Assistant ===")
        print("Speak into the microphone. Press Ctrl+C to stop.\n")

        # -- 3. Main loop --
        while True:
            try:
                _voice_cycle(agent, pa, rec_buf)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print("[voice] Cycle error: {}".format(e))
                try:
                    pa.value(0)
                except Exception:
                    pass
                gc.collect()
                time.sleep_ms(500)

    except KeyboardInterrupt:
        print("\n[voice] Interrupted by user")

    finally:
        # -- 4. Cleanup --
        print("[voice] Shutting down...")
        if pa is not None:
            pa.value(0)
        if spk is not None:
            spk.deinit()
        if mic is not None:
            mic.deinit()
        if mclk_pwm is not None:
            stop_mclk(mclk_pwm)
        gc.collect()
        print("[voice] Goodbye!")


def _voice_cycle(agent, pa, rec_buf):
    """Run one listen -> think -> speak cycle."""

    t_start = time.ticks_ms() if config.DEBUG else 0

    # -- Record --
    # BOX-3 primary mic is ES7210 CH1 mapped to I2S left slot.
    # Change to channel="right" or channel="mix" if audio is silent/weak.
    print("Listening...")
    t0 = time.ticks_ms() if config.DEBUG else 0
    pcm = audio.record(max_seconds=10, buf=rec_buf, channel="left")
    if config.DEBUG:
        print("[voice] Record: {}ms, {} bytes".format(
            time.ticks_diff(time.ticks_ms(), t0), len(pcm)))

    if len(pcm) < 1600:  # < 0.05s of audio at 16kHz/16-bit
        if config.DEBUG:
            print("[voice] Too short, skipping")
        return

    # -- Convert to WAV --
    wav = audio.pcm_to_wav(pcm)
    del pcm
    gc.collect()

    # -- Transcribe --
    print("Transcribing...")
    t0 = time.ticks_ms() if config.DEBUG else 0
    text = speech.transcribe(wav, config.OPENAI_API_KEY, debug=config.DEBUG)
    del wav
    gc.collect()
    if config.DEBUG:
        print("[voice] Transcribe: {}ms".format(
            time.ticks_diff(time.ticks_ms(), t0)))

    if not text or not text.strip():
        print("No speech detected")
        return

    # Skip Whisper hallucinations
    _HALLUCINATIONS = {"beep", "beeping", "electronic beeping", "you", ".", "",
                       "the", "bye", "thank you", "(silence)",
                       "thanks for watching", "thank you for watching",
                       "beep beep", "beep beep beep", "beep.", "beep. beep.",
                       "beep. beep. beep."}
    stripped = text.strip()
    if len(stripped) < 3 or stripped.lower() in _HALLUCINATIONS or len(text) > 500:
        print("Skipping noise/hallucination: {}".format(repr(text)))
        return

    print("You said: {}".format(text))

    # -- Agent --
    print("Thinking...")
    t0 = time.ticks_ms() if config.DEBUG else 0
    response = agent.prompt(text)
    gc.collect()
    if config.DEBUG:
        print("[voice] Agent: {}ms".format(
            time.ticks_diff(time.ticks_ms(), t0)))

    if not response:
        print("No response from agent")
        return

    print("Claude: {}".format(response))

    # -- Synthesize --
    print("Speaking...")
    t0 = time.ticks_ms() if config.DEBUG else 0
    pcm_out = speech.synthesize(response, config.OPENAI_API_KEY, debug=config.DEBUG)
    gc.collect()
    if config.DEBUG:
        print("[voice] TTS: {}ms, {} bytes".format(
            time.ticks_diff(time.ticks_ms(), t0), len(pcm_out)))

    if pcm_out:
        # Enable PA only during playback to reduce idle hiss
        pa.value(1)
        t0 = time.ticks_ms() if config.DEBUG else 0
        audio.play(pcm_out, sample_rate=24000)
        pa.value(0)
        if config.DEBUG:
            print("[voice] Play: {}ms".format(
                time.ticks_diff(time.ticks_ms(), t0)))
        del pcm_out
        gc.collect()

    if config.DEBUG:
        total = time.ticks_diff(time.ticks_ms(), t_start)
        print("[voice] Total cycle: {}ms".format(total))
        print("[voice] Free memory: {} bytes".format(gc.mem_free()))

    print("---")
