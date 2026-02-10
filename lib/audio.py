# audio.py -- I2S record/play for ESP32-S3-BOX-3
# Requires codec init (ES7210 + ES8311 via I2C) before use.
# Hardware: ES7210 ADC (mic), ES8311 DAC (speaker), shared I2S bus.

import struct
import gc
from machine import I2S, Pin

# Pin mapping (from ESP-BSP esp-box-3.h)
_BCLK = 17   # I2S_SCLK shared by both codecs
_WS   = 45   # I2S_LRCK shared by both codecs
_DIN  = 16   # I2S_DIN  from ES7210 (mic data)
_DOUT = 15   # I2S_DOUT to ES8311 (speaker data)


def record(max_seconds=10, silence_threshold=500, silence_duration_ms=1500, buf=None, channel="left"):
    """Record audio from microphone with silence detection.

    ES7210 outputs stereo I2S frames (mic1=left, mic2=right).
    We record in stereo and downmix to mono PCM.

    Args:
        buf: Optional pre-allocated bytearray for mono output.
             Must be at least sample_rate * 2 * max_seconds bytes.
             If None, a new buffer is allocated.
        channel: Which channel(s) to keep: "left", "right", or "mix".
                 Default "left" matches the ESP32-S3-BOX-3 primary mic
                 (ES7210 CH1 mapped to I2S left slot).

    Returns raw 16-bit 16kHz mono PCM bytes, trimmed to actual length.
    """
    sample_rate = 16000
    mono_bytes_per_sec = sample_rate * 2   # 16-bit mono output
    max_mono_bytes = mono_bytes_per_sec * max_seconds
    # Stereo chunk: 2048 bytes = 512 stereo frames (each 4 bytes: L16+R16)
    stereo_chunk_size = 2048
    min_mono_bytes = mono_bytes_per_sec // 2  # 0.5s minimum before silence detection

    # How many consecutive silent chunks to trigger stop
    # Each stereo chunk yields stereo_chunk_size/4 mono samples
    mono_samples_per_chunk = stereo_chunk_size // 4
    chunk_duration_ms = mono_samples_per_chunk * 1000 // sample_rate
    silence_chunks_needed = silence_duration_ms // chunk_duration_ms

    if buf is None or len(buf) < max_mono_bytes:
        buf = bytearray(max_mono_bytes)
    stereo_chunk = bytearray(stereo_chunk_size)
    offset = 0
    silence_count = 0

    # NOTE: ES7210 runs in I2S master mode — it generates BCLK and WS from
    # MCLK because MicroPython I2S RX on ESP32-S3 does not reliably drive
    # these clock outputs. The ESP32 I2S peripheral is also configured as
    # master at the same rate. Both sides share BCLK (pin 17) and WS (pin 45),
    # so there is bus contention on those two lines. In practice this works
    # because both derive from the same 12.288 MHz MCLK and target identical
    # clock frequencies, keeping the signals in phase. The ESP32 I2S still
    # needs sck/ws assigned to sample DIN data at the correct timing.
    i2s = I2S(
        0,
        sck=Pin(_BCLK),
        ws=Pin(_WS),
        sd=Pin(_DIN),
        mode=I2S.RX,
        bits=16,
        format=I2S.STEREO,
        rate=sample_rate,
        ibuf=32000,
    )

    try:
        while offset < max_mono_bytes:
            n = i2s.readinto(stereo_chunk)
            if n == 0:
                continue
            # Downmix stereo to mono using selected channel strategy
            # Stereo layout: [L0_lo, L0_hi, R0_lo, R0_hi, L1_lo, L1_hi, ...]
            mono_bytes = _stereo_to_mono(stereo_chunk, n, channel)
            end = min(offset + len(mono_bytes), max_mono_bytes)
            actual = end - offset
            buf[offset:end] = mono_bytes[:actual]
            offset = end

            # Silence detection on the mono data (skip first 0.5s)
            if offset >= min_mono_bytes:
                rms = _rms(buf, offset - actual, actual)
                if rms < silence_threshold:
                    silence_count += 1
                else:
                    silence_count = 0
                if silence_count >= silence_chunks_needed:
                    break
    finally:
        i2s.deinit()

    gc.collect()
    return bytes(buf[:offset])


def _stereo_to_mono(buf, length, channel="left"):
    """Extract a single channel or mix from interleaved 16-bit stereo PCM.

    Args:
        buf: Stereo PCM buffer (L0_lo L0_hi R0_lo R0_hi L1_lo L1_hi ...).
        length: Number of valid bytes in buf.
        channel: "left" - take left channel only.
                 "right" - take right channel only.
                 "mix" - average L and R (with saturation).

    Returns:
        bytearray of mono 16-bit PCM.
    """
    frames = length // 4  # each stereo frame is 4 bytes
    out = bytearray(frames * 2)
    si = 0
    di = 0
    if channel == "left":
        for _ in range(frames):
            out[di] = buf[si]
            out[di + 1] = buf[si + 1]
            si += 4
            di += 2
    elif channel == "right":
        for _ in range(frames):
            out[di] = buf[si + 2]
            out[di + 1] = buf[si + 3]
            si += 4
            di += 2
    else:  # "mix" -- average L+R with saturation
        for _ in range(frames):
            # Decode left sample (signed 16-bit little-endian)
            l = buf[si] | (buf[si + 1] << 8)
            if l >= 0x8000:
                l -= 0x10000
            # Decode right sample
            r = buf[si + 2] | (buf[si + 3] << 8)
            if r >= 0x8000:
                r -= 0x10000
            # Average with rounding
            m = (l + r + 1) >> 1
            # Saturate to int16 range
            if m > 32767:
                m = 32767
            elif m < -32768:
                m = -32768
            # Encode as unsigned 16-bit little-endian
            if m < 0:
                m += 0x10000
            out[di] = m & 0xFF
            out[di + 1] = (m >> 8) & 0xFF
            si += 4
            di += 2
    return out


def _mono_to_stereo(mono_data):
    """Duplicate mono 16-bit PCM to stereo (L=R) for I2S output.

    Input:  [S0_lo, S0_hi, S1_lo, S1_hi, ...]
    Output: [S0_lo, S0_hi, S0_lo, S0_hi, S1_lo, S1_hi, S1_lo, S1_hi, ...]
    """
    n = len(mono_data)
    out = bytearray(n * 2)
    si = 0
    di = 0
    while si < n - 1:
        lo = mono_data[si]
        hi = mono_data[si + 1]
        out[di] = lo
        out[di + 1] = hi
        out[di + 2] = lo
        out[di + 3] = hi
        si += 2
        di += 4
    return out


def play(pcm_data, sample_rate=24000):
    """Play raw 16-bit mono PCM data through the speaker.

    Converts mono to stereo (duplicating L=R) before writing to I2S
    to ensure correct framing with the ES8311 DAC regardless of which
    I2S slot it listens on.
    """
    chunk_size = 1024  # mono bytes per iteration (stereo output is 2x)

    i2s = I2S(
        0,
        sck=Pin(_BCLK),
        ws=Pin(_WS),
        sd=Pin(_DOUT),
        mode=I2S.TX,
        bits=16,
        format=I2S.STEREO,
        rate=sample_rate,
        ibuf=32000,
    )

    # Pad to 16-bit sample alignment if needed
    if len(pcm_data) & 1:
        pcm_data = pcm_data + b'\x00'
    mv = memoryview(pcm_data)
    try:
        for i in range(0, len(pcm_data), chunk_size):
            end = min(i + chunk_size, len(pcm_data))
            stereo = _mono_to_stereo(mv[i:end])
            i2s.write(stereo)
    finally:
        i2s.deinit()
    gc.collect()


def pcm_to_wav(pcm_data, sample_rate=16000, bits=16, channels=1):
    """Wrap raw PCM data in a WAV/RIFF container. Returns bytes."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8

    header = struct.pack(
        '<4sI4s'       # RIFF, ChunkSize, WAVE
        '4sIHHIIHH'    # fmt , SubChunk1Size, AudioFormat, NumChannels,
                       #       SampleRate, ByteRate, BlockAlign, BitsPerSample
        '4sI',         # data, SubChunk2Size
        b'RIFF',
        data_size + 36,
        b'WAVE',
        b'fmt ',
        16,            # PCM fmt chunk size
        1,             # AudioFormat = PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b'data',
        data_size,
    )

    return header + pcm_data


def _apply_gain(buf, length, gain=4):
    """Apply software gain to 16-bit PCM in-place with saturation.

    Args:
        buf: bytearray of 16-bit signed PCM.
        length: Number of valid bytes.
        gain: Integer multiplier (default 4 = ~12dB boost).
    """
    for i in range(0, length - 1, 2):
        lo = buf[i]
        hi = buf[i + 1]
        sample = lo | (hi << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        sample *= gain
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        if sample < 0:
            sample += 0x10000
        buf[i] = sample & 0xFF
        buf[i + 1] = (sample >> 8) & 0xFF


def _rms(buf, start, length):
    """RMS of 16-bit signed PCM in buf[start:start+length]. Integer math, no floats."""
    n = length >> 1  # number of 16-bit samples
    if n == 0:
        return 0
    sum_sq = 0
    for i in range(n):
        off = start + i * 2
        lo = buf[off]
        hi = buf[off + 1]
        sample = lo | (hi << 8)
        if sample >= 0x8000:
            sample -= 0x10000
        sum_sq += sample * sample
    mean = sum_sq // n
    # Integer square root (Newton's method)
    if mean == 0:
        return 0
    x = mean
    y = (x + 1) >> 1
    while y < x:
        x = y
        y = (x + mean // x) >> 1
    return x
