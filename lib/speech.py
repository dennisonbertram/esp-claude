# OpenAI Whisper (STT) and TTS API client for MicroPython / ESP32
# Uses urequests with manually constructed multipart/form-data for Whisper
# and JSON POST for TTS.

try:
    import ujson as json
except ImportError:
    import json

import gc
import urequests

_DEFAULT_TIMEOUT = 30


def _ascii_safe(text):
    """Strip non-ASCII chars (emoji etc) that break ujson on MicroPython."""
    return ''.join(c for c in text if ord(c) < 128)

# Multipart boundary - long and unique to avoid collisions with WAV data
_BOUNDARY = b"----ESPClaudeBoundary1234567890"

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
TTS_URL = "https://api.openai.com/v1/audio/speech"


def transcribe(wav_data, api_key, debug=False, timeout=_DEFAULT_TIMEOUT):
    """Transcribe audio using OpenAI Whisper API.

    Args:
        wav_data: bytes - Complete WAV file data (header + PCM samples).
        api_key: str - OpenAI API key.
        debug: bool - Print debug info.
        timeout: int - Socket timeout in seconds.

    Returns:
        str - Transcribed text, or empty string on error.
    """
    # Build multipart body with a single concatenation to avoid
    # intermediate copies of wav_data through bytearray appending.
    header = (
        b"--" + _BOUNDARY + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        b"Content-Type: audio/wav\r\n"
        b"\r\n"
    )
    footer = (
        b"\r\n"
        b"--" + _BOUNDARY + b"\r\n"
        b'Content-Disposition: form-data; name="model"\r\n'
        b"\r\n"
        b"whisper-1\r\n"
        b"--" + _BOUNDARY + b"--\r\n"
    )
    body = header + wav_data + footer
    del header, footer

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "multipart/form-data; boundary=" + _BOUNDARY.decode(),
    }

    if debug:
        print("[speech] transcribe: sending", len(body), "bytes to Whisper API")

    resp = None
    try:
        resp = urequests.post(WHISPER_URL, data=body, headers=headers)

        if debug:
            print("[speech] transcribe: status", resp.status_code)

        if resp.status_code != 200:
            err_text = resp.text
            print("[speech] transcribe error:", resp.status_code, err_text)
            return ""

        result = resp.json()
        text = result.get("text", "")
        if debug:
            print("[speech] transcribe result:", text[:120])
        return text

    except Exception as e:
        print("[speech] transcribe exception:", e)
        return ""
    finally:
        if resp is not None:
            resp.close()
        body = None
        gc.collect()


def synthesize(text, api_key, voice="alloy", debug=False, timeout=_DEFAULT_TIMEOUT):
    """Synthesize speech using OpenAI TTS API.

    Args:
        text: str - Text to convert to speech.
        api_key: str - OpenAI API key.
        voice: str - Voice name (alloy, echo, fable, onyx, nova, shimmer).
        debug: bool - Print debug info.
        timeout: int - Socket timeout in seconds.

    Returns:
        bytes - Raw 24kHz 16-bit mono little-endian PCM audio data,
                or empty bytes on error.
    """
    text = _ascii_safe(text)
    payload = json.dumps({
        "model": "tts-1",
        "input": text,
        "voice": voice,
        "response_format": "pcm",
    })

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }

    if debug:
        print("[speech] synthesize: requesting TTS, voice=%s, text=%d chars" % (voice, len(text)))

    resp = None
    try:
        resp = urequests.post(TTS_URL, data=payload, headers=headers)

        if debug:
            print("[speech] synthesize: status", resp.status_code)

        if resp.status_code != 200:
            err_text = resp.text
            print("[speech] synthesize error:", resp.status_code, err_text)
            return b""

        pcm_data = resp.content
        if debug:
            print("[speech] synthesize: received", len(pcm_data), "bytes of PCM audio")
        return pcm_data

    except Exception as e:
        print("[speech] synthesize exception:", e)
        return b""
    finally:
        if resp is not None:
            resp.close()
        gc.collect()
