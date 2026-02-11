"""
ILI9342C display driver for ESP32-S3-BOX-3.

Drives the 320x240 2.4" ILI9342C LCD via SPI. Renders text directly
to the display controller's GRAM -- no full-screen framebuffer needed.

The ESP32-S3-BOX-3 uses an ILI9342C controller (not ST7789).  The
vendor-specific initialisation sequence below is taken from the
official Espressif BSP (esp-bsp/bsp/esp-box-3).

Hardware pins (from esp-bsp esp-box-3.h):
    MOSI  = GPIO 6   (BSP_LCD_DATA0)
    SCLK  = GPIO 7   (BSP_LCD_PCLK)
    CS    = GPIO 5   (BSP_LCD_CS)
    DC    = GPIO 4   (BSP_LCD_DC)
    RST   = GPIO 48  (BSP_LCD_RST)
    BL    = GPIO 47  (BSP_LCD_BACKLIGHT)

Usage:
    from lib.display import init, show_status, deinit
    init()
    show_status("READY", "ESP-Claude Voice Assistant")
"""

import time
from machine import Pin, SPI
import struct

# --- Pin assignments (ESP32-S3-BOX-3) ---
_MOSI_PIN = 6
_SCLK_PIN = 7
_CS_PIN   = 5
_DC_PIN   = 4
_RST_PIN  = 48
_BL_PIN   = 47

# --- Display geometry ---
WIDTH  = 320
HEIGHT = 240

# --- ILI9342C / ILI9341 commands ---
_SWRESET = 0x01
_SLPOUT  = 0x11
_NORON   = 0x13
_INVON   = 0x21
_DISPON  = 0x29
_CASET   = 0x2A
_RASET   = 0x2B
_RAMWR   = 0x2C
_MADCTL  = 0x36
_COLMOD  = 0x3A

# MADCTL flags
_MADCTL_MX  = 0x40
_MADCTL_MY  = 0x80
_MADCTL_MV  = 0x20
_MADCTL_RGB = 0x00
_MADCTL_BGR = 0x08

# --- Module-level state ---
_spi = None
_dc  = None
_cs  = None
_rst = None
_bl  = None

# --- Minimal 5x8 font for ASCII 0x20-0x7E ---
# Each character is 5 bytes; each byte is a column (LSB = top row).
# This is the classic Adafruit/GLCD 5x7 font, public domain.
_FONT = (
    b'\x00\x00\x00\x00\x00'  # 0x20 space
    b'\x00\x00\x5F\x00\x00'  # !
    b'\x00\x07\x00\x07\x00'  # "
    b'\x14\x7F\x14\x7F\x14'  # #
    b'\x24\x2A\x7F\x2A\x12'  # $
    b'\x23\x13\x08\x64\x62'  # %
    b'\x36\x49\x55\x22\x50'  # &
    b'\x00\x05\x03\x00\x00'  # '
    b'\x00\x1C\x22\x41\x00'  # (
    b'\x00\x41\x22\x1C\x00'  # )
    b'\x08\x2A\x1C\x2A\x08'  # *
    b'\x08\x08\x3E\x08\x08'  # +
    b'\x00\x50\x30\x00\x00'  # ,
    b'\x08\x08\x08\x08\x08'  # -
    b'\x00\x60\x60\x00\x00'  # .
    b'\x20\x10\x08\x04\x02'  # /
    b'\x3E\x51\x49\x45\x3E'  # 0
    b'\x00\x42\x7F\x40\x00'  # 1
    b'\x42\x61\x51\x49\x46'  # 2
    b'\x21\x41\x45\x4B\x31'  # 3
    b'\x18\x14\x12\x7F\x10'  # 4
    b'\x27\x45\x45\x45\x39'  # 5
    b'\x3C\x4A\x49\x49\x30'  # 6
    b'\x01\x71\x09\x05\x03'  # 7
    b'\x36\x49\x49\x49\x36'  # 8
    b'\x06\x49\x49\x29\x1E'  # 9
    b'\x00\x36\x36\x00\x00'  # :
    b'\x00\x56\x36\x00\x00'  # ;
    b'\x00\x08\x14\x22\x41'  # <
    b'\x14\x14\x14\x14\x14'  # =
    b'\x41\x22\x14\x08\x00'  # >
    b'\x02\x01\x51\x09\x06'  # ?
    b'\x32\x49\x79\x41\x3E'  # @
    b'\x7E\x11\x11\x11\x7E'  # A
    b'\x7F\x49\x49\x49\x36'  # B
    b'\x3E\x41\x41\x41\x22'  # C
    b'\x7F\x41\x41\x22\x1C'  # D
    b'\x7F\x49\x49\x49\x41'  # E
    b'\x7F\x09\x09\x01\x01'  # F
    b'\x3E\x41\x41\x51\x32'  # G
    b'\x7F\x08\x08\x08\x7F'  # H
    b'\x00\x41\x7F\x41\x00'  # I
    b'\x20\x40\x41\x3F\x01'  # J
    b'\x7F\x08\x14\x22\x41'  # K
    b'\x7F\x40\x40\x40\x40'  # L
    b'\x7F\x02\x04\x02\x7F'  # M
    b'\x7F\x04\x08\x10\x7F'  # N
    b'\x3E\x41\x41\x41\x3E'  # O
    b'\x7F\x09\x09\x09\x06'  # P
    b'\x3E\x41\x51\x21\x5E'  # Q
    b'\x7F\x09\x19\x29\x46'  # R
    b'\x46\x49\x49\x49\x31'  # S
    b'\x01\x01\x7F\x01\x01'  # T
    b'\x3F\x40\x40\x40\x3F'  # U
    b'\x1F\x20\x40\x20\x1F'  # V
    b'\x7F\x20\x18\x20\x7F'  # W
    b'\x63\x14\x08\x14\x63'  # X
    b'\x03\x04\x78\x04\x03'  # Y
    b'\x61\x51\x49\x45\x43'  # Z
    b'\x00\x00\x7F\x41\x41'  # [
    b'\x02\x04\x08\x10\x20'  # backslash
    b'\x41\x41\x7F\x00\x00'  # ]
    b'\x04\x02\x01\x02\x04'  # ^
    b'\x40\x40\x40\x40\x40'  # _
    b'\x00\x01\x02\x04\x00'  # `
    b'\x20\x54\x54\x54\x78'  # a
    b'\x7F\x48\x44\x44\x38'  # b
    b'\x38\x44\x44\x44\x20'  # c
    b'\x38\x44\x44\x48\x7F'  # d
    b'\x38\x54\x54\x54\x18'  # e
    b'\x08\x7E\x09\x01\x02'  # f
    b'\x08\x14\x54\x54\x3C'  # g
    b'\x7F\x08\x04\x04\x78'  # h
    b'\x00\x44\x7D\x40\x00'  # i
    b'\x20\x40\x44\x3D\x00'  # j
    b'\x00\x7F\x10\x28\x44'  # k
    b'\x00\x41\x7F\x40\x00'  # l
    b'\x7C\x04\x18\x04\x78'  # m
    b'\x7C\x08\x04\x04\x78'  # n
    b'\x38\x44\x44\x44\x38'  # o
    b'\x7C\x14\x14\x14\x08'  # p
    b'\x08\x14\x14\x18\x7C'  # q
    b'\x7C\x08\x04\x04\x08'  # r
    b'\x48\x54\x54\x54\x20'  # s
    b'\x04\x3F\x44\x40\x20'  # t
    b'\x3C\x40\x40\x20\x7C'  # u
    b'\x1C\x20\x40\x20\x1C'  # v
    b'\x3C\x40\x30\x40\x3C'  # w
    b'\x44\x28\x10\x28\x44'  # x
    b'\x0C\x50\x50\x50\x3C'  # y
    b'\x44\x64\x54\x4C\x44'  # z
    b'\x00\x08\x36\x41\x00'  # {
    b'\x00\x00\x7F\x00\x00'  # |
    b'\x00\x41\x36\x08\x00'  # }
    b'\x08\x08\x2A\x1C\x08'  # ~
)

# --- State-related colors (RGB565 big-endian for ILI9342C) ---
# RGB565: RRRRRGGGGGGBBBBB
# Helper: rgb565(r, g, b) -> 16-bit value
def _rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

# Background colors for each state
_STATE_COLORS = {
    "LISTENING": _rgb565(0, 0, 100),       # dark blue
    "THINKING":  _rgb565(120, 80, 0),       # dark yellow/orange
    "SPEAKING":  _rgb565(0, 80, 0),         # dark green
    "READY":     _rgb565(60, 0, 80),        # dark purple
    "ERROR":     _rgb565(120, 0, 0),        # dark red
}
_DEFAULT_BG = _rgb565(0, 0, 0)             # black
_WHITE = _rgb565(255, 255, 255)


def _cmd(command, data=None):
    """Send a command byte, optionally followed by data bytes."""
    _dc.value(0)
    _cs.value(0)
    _spi.write(bytes([command]))
    if data is not None:
        _dc.value(1)
        _spi.write(data)
    _cs.value(1)


def _set_window(x0, y0, x1, y1):
    """Set the drawing window (column/row address)."""
    _cmd(_CASET, struct.pack('>HH', x0, x1))
    _cmd(_RASET, struct.pack('>HH', y0, y1))


def _fill_rect(x, y, w, h, color):
    """Fill a rectangle with a solid color (RGB565)."""
    if w <= 0 or h <= 0:
        return
    _set_window(x, y, x + w - 1, y + h - 1)
    # Send color data in chunks to limit RAM usage
    hi = (color >> 8) & 0xFF
    lo = color & 0xFF
    chunk_pixels = min(w * h, 2048)
    chunk = bytes([hi, lo]) * chunk_pixels
    _dc.value(0)
    _cs.value(0)
    _spi.write(bytes([_RAMWR]))
    _dc.value(1)
    total = w * h
    sent = 0
    while sent < total:
        n = min(chunk_pixels, total - sent)
        if n == chunk_pixels:
            _spi.write(chunk)
        else:
            _spi.write(bytes([hi, lo]) * n)
        sent += n
    _cs.value(1)


def _draw_char(ch, x, y, color, bg, scale):
    """Draw a single character at (x, y) with given scale.

    Renders directly to display GRAM -- no framebuffer.
    Character cell is (5*scale + scale) x (8*scale) pixels.
    The extra +scale is inter-character spacing.
    """
    idx = ord(ch) - 0x20
    if idx < 0 or idx >= 95:
        idx = 0  # fallback to space

    # Total pixel size of one character cell (including 1-col gap)
    cw = 6 * scale  # 5 data columns + 1 gap column, each scaled
    ch_h = 8 * scale

    # Set window for the entire character cell
    if x + cw > WIDTH:
        cw = WIDTH - x
    if y + ch_h > HEIGHT:
        ch_h = HEIGHT - y
    if cw <= 0 or ch_h <= 0:
        return

    _set_window(x, y, x + cw - 1, y + ch_h - 1)

    fg_hi = (color >> 8) & 0xFF
    fg_lo = color & 0xFF
    bg_hi = (bg >> 8) & 0xFF
    bg_lo = bg & 0xFF

    # Build one row at a time (cw * 2 bytes per row) and send
    font_offset = idx * 5
    row_buf = bytearray(cw * 2)

    _dc.value(0)
    _cs.value(0)
    _spi.write(bytes([_RAMWR]))
    _dc.value(1)

    for bit_row in range(8):
        # Build pixel data for this font row
        px = 0
        for col in range(5):
            font_byte = _FONT[font_offset + col]
            is_set = (font_byte >> bit_row) & 1
            hi = fg_hi if is_set else bg_hi
            lo = fg_lo if is_set else bg_lo
            for _ in range(scale):
                row_buf[px] = hi
                row_buf[px + 1] = lo
                px += 2
        # Gap column (background)
        for _ in range(scale):
            if px + 1 < len(row_buf):
                row_buf[px] = bg_hi
                row_buf[px + 1] = bg_lo
                px += 2

        # Write this row 'scale' times (vertical scaling)
        row_data = bytes(row_buf[:px])
        for _ in range(scale):
            _spi.write(row_data)

    _cs.value(1)


def init():
    """Initialize the ILI9342C display and turn on the backlight."""
    global _spi, _dc, _cs, _rst, _bl

    # Configure pins
    _dc  = Pin(_DC_PIN, Pin.OUT)
    _cs  = Pin(_CS_PIN, Pin.OUT)
    _rst = Pin(_RST_PIN, Pin.OUT)
    _bl  = Pin(_BL_PIN, Pin.OUT)

    _cs.value(1)
    _dc.value(0)

    # SPI bus -- use SPI(1) which maps to SPI2_HOST on ESP32-S3.
    # The BSP uses SPI3_HOST (MicroPython SPI(2)), but SPI(2) crashes
    # on some MicroPython builds with user-assigned pins.  SPI(1) works
    # fine with arbitrary pin routing on ESP32-S3.
    # ILI9342C supports up to 10MHz write clock per datasheet, but the
    # Espressif BSP runs at 40MHz successfully; we do the same.
    _spi = SPI(1, baudrate=40000000, polarity=0, phase=0,
               sck=Pin(_SCLK_PIN), mosi=Pin(_MOSI_PIN))

    # Hardware reset
    _rst.value(1)
    time.sleep_ms(10)
    _rst.value(0)
    time.sleep_ms(10)
    _rst.value(1)
    time.sleep_ms(120)

    # ----- ILI9342C vendor-specific initialisation -----
    # Sequence taken from Espressif BSP (esp-bsp/bsp/esp-box-3/esp-box-3.c).
    # These commands configure power, gamma, and timing registers that the
    # ILI9342C requires but a plain ST7789 does not.

    _cmd(0xC8, b'\xFF\x93\x42')          # Enable extended command set
    _cmd(0xC0, b'\x0E\x0E')              # Power Control 1
    _cmd(0xC5, b'\xD0')                  # VCOM Control
    _cmd(0xC1, b'\x02')                  # Power Control 2
    _cmd(0xB4, b'\x02')                  # Display Inversion Control
    # Positive Gamma Correction
    _cmd(0xE0, b'\x00\x03\x08\x06\x13\x09\x39\x39'
               b'\x48\x02\x0A\x08\x17\x17\x0F')
    # Negative Gamma Correction
    _cmd(0xE1, b'\x00\x28\x29\x01\x0D\x03\x3F\x33'
               b'\x52\x04\x0F\x0E\x37\x38\x0F')
    _cmd(0xB1, b'\x00\x1B')              # Frame Rate Control

    # Memory data access control -- landscape, BGR colour order.
    # The ILI9342C native orientation is 320x240 (landscape) so we
    # only need BGR; no MV/MX/MY rotation flags required.
    # The BSP applies mirror_x + mirror_y after panel init; we match
    # that by setting MX | MY | BGR = 0xC8.
    _cmd(_MADCTL, bytes([_MADCTL_MX | _MADCTL_MY | _MADCTL_BGR]))

    # Pixel format: 16-bit/pixel (RGB565)
    _cmd(_COLMOD, b'\x55')

    _cmd(0xB7, b'\x06')                  # Entry Mode Set

    # Sleep out
    _cmd(_SLPOUT)
    time.sleep_ms(120)

    # Display on
    _cmd(_DISPON)
    time.sleep_ms(120)

    # Backlight on
    _bl.value(1)

    # Clear to black
    clear()


def clear(color=None):
    """Fill the entire screen with a solid color.

    Args:
        color: RGB565 color value (default black).
    """
    if color is None:
        color = _DEFAULT_BG
    _fill_rect(0, 0, WIDTH, HEIGHT, color)


def text(message, x=0, y=0, color=None, bg=None, scale=2):
    """Draw a text string at position (x, y).

    Args:
        message: ASCII string to render.
        x: Left pixel coordinate.
        y: Top pixel coordinate.
        color: Foreground color (RGB565), default white.
        bg: Background color (RGB565), default black.
        scale: Integer scale factor (1=5x8, 2=10x16, 3=15x24, etc.)
    """
    if color is None:
        color = _WHITE
    if bg is None:
        bg = _DEFAULT_BG
    cx = x
    char_w = 6 * scale
    for ch in message:
        if ch == '\n':
            cx = x
            y += 8 * scale
            continue
        if cx + char_w > WIDTH:
            cx = x
            y += 8 * scale
        if y + 8 * scale > HEIGHT:
            break
        _draw_char(ch, cx, y, color, bg, scale)
        cx += char_w


def show_status(status, detail=""):
    """Show a status screen with large status text and smaller detail.

    Clears the screen with a state-appropriate background color, then
    draws the status text centered and detail text below.

    Args:
        status: Status string (e.g. "LISTENING", "THINKING", "SPEAKING").
        detail: Optional detail text shown below the status.
    """
    bg = _STATE_COLORS.get(status, _DEFAULT_BG)

    # Clear screen with state color
    clear(bg)

    # Draw status text centered, scale=4 (20x32 per char)
    status_scale = 4
    char_w = 6 * status_scale
    text_w = len(status) * char_w
    sx = max(0, (WIDTH - text_w) // 2)
    sy = 60 if detail else 100
    text(status, sx, sy, _WHITE, bg, status_scale)

    # Draw detail text, scale=2 (10x16 per char)
    if detail:
        detail_scale = 2
        d_char_w = 6 * detail_scale
        # Word-wrap detail to fit screen width
        max_chars = WIDTH // d_char_w
        lines = []
        while detail:
            if len(detail) <= max_chars:
                lines.append(detail)
                break
            # Find last space within max_chars
            cut = detail[:max_chars].rfind(' ')
            if cut <= 0:
                cut = max_chars
            lines.append(detail[:cut])
            detail = detail[cut:].lstrip()
        dy = sy + 8 * status_scale + 20
        for line in lines:
            lw = len(line) * d_char_w
            dx = max(0, (WIDTH - lw) // 2)
            text(line, dx, dy, _WHITE, bg, detail_scale)
            dy += 8 * detail_scale + 4
            if dy + 8 * detail_scale > HEIGHT:
                break


def deinit():
    """Turn off backlight and release SPI."""
    global _spi, _bl
    if _bl is not None:
        _bl.value(0)
        _bl = None
    if _spi is not None:
        _spi.deinit()
        _spi = None
