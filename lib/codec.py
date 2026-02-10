"""
MicroPython I2C codec drivers for ESP32-S3-BOX-3 audio hardware.

ES7210: Quad-channel ADC (microphone input)
ES8311: Mono DAC/ADC codec (speaker output)

Ported from Espressif esp-bsp and esp-adf C drivers.
"""

import time
from machine import Pin, PWM


# ---------------------------------------------------------------------------
# MCLK generation via PWM
# ---------------------------------------------------------------------------

def start_mclk(pin=2, freq=12288000):
    """Generate MCLK square wave using PWM on the given pin.

    Args:
        pin: GPIO number for MCLK output (default 2 for BOX-3).
        freq: MCLK frequency in Hz. Fixed at 12.288 MHz -- both
              ES7210 and ES8311 have internal dividers to derive
              their target sample rates from this single clock.

    Returns:
        PWM object (keep a reference to prevent GC).

    NOTE -- Known limitation: The ideal approach is I2S APLL-derived MCLK
    which is phase-coherent with BCLK/LRCK. MicroPython does not expose
    the I2S APLL output on a GPIO, so we use the ESP32-S3 LEDC/PWM
    hardware timer instead. The LEDC peripheral uses hardware counters
    (not software timing) and should be accurate at 12.288 MHz, but:
      - Actual frequency depends on the APB clock and divider rounding.
      - Verify with an oscilloscope that the output is within codec
        tolerance (typically +/-50 ppm) and that codecs lock reliably.
      - duty_u16=32768 gives 50% duty cycle, critical for clock signals.
    """
    pwm = PWM(Pin(pin), freq=freq, duty_u16=32768)  # 50% duty
    return pwm


def stop_mclk(pwm):
    """Stop MCLK output and release the pin."""
    if pwm is not None:
        pwm.deinit()


# ---------------------------------------------------------------------------
# ES7210 — Quad-channel ADC for microphones
# ---------------------------------------------------------------------------

class ES7210:
    """I2C driver for the ES7210 4-channel audio ADC.

    Default I2C address 0x40 (AD1=0, AD0=0).
    """

    ADDR = 0x40

    # Register addresses
    REG_RESET       = 0x00
    REG_CLK_OFF     = 0x01
    REG_MAINCLK     = 0x02
    REG_MASTERCLK   = 0x03
    REG_LRCK_DIVH   = 0x04
    REG_LRCK_DIVL   = 0x05
    REG_POWER_DOWN  = 0x06
    REG_OSR         = 0x07
    REG_MODE_CFG    = 0x08
    REG_TIME_CTL0   = 0x09
    REG_TIME_CTL1   = 0x0A
    REG_SDP1        = 0x11
    REG_SDP2        = 0x12
    REG_AUTOMUTE    = 0x13
    REG_ADC1_GAIN   = 0x1B
    REG_ADC2_GAIN   = 0x1C
    REG_ADC3_GAIN   = 0x1D
    REG_ADC4_GAIN   = 0x1E
    REG_HPF34_2     = 0x20
    REG_HPF34_1     = 0x21
    REG_HPF12_2     = 0x22
    REG_HPF12_1     = 0x23
    REG_ANALOG      = 0x40
    REG_MIC12_BIAS  = 0x41
    REG_MIC34_BIAS  = 0x42
    REG_MIC1_GAIN   = 0x43
    REG_MIC2_GAIN   = 0x44
    REG_MIC3_GAIN   = 0x45
    REG_MIC4_GAIN   = 0x46
    REG_MIC1_POWER  = 0x47
    REG_MIC2_POWER  = 0x48
    REG_MIC3_POWER  = 0x49
    REG_MIC4_POWER  = 0x4A
    REG_MIC12_PWR   = 0x4B
    REG_MIC34_PWR   = 0x4C

    def __init__(self, addr=None, debug=False):
        self.addr = addr or self.ADDR
        self.debug = debug
        self._i2c = None

    def _wr(self, reg, val):
        """Write a single byte to a register."""
        self._i2c.writeto_mem(self.addr, reg, bytes([val]))
        if self.debug:
            print("ES7210 W 0x{:02X} = 0x{:02X}".format(reg, val))
        time.sleep_ms(1)

    def _rd(self, reg):
        """Read a single byte from a register."""
        data = self._i2c.readfrom_mem(self.addr, reg, 1)
        return data[0]

    def init(self, i2c, sample_rate=16000, mclk=12288000):
        """Initialize the ES7210 for microphone input.

        Args:
            i2c: machine.I2C instance (already initialized).
            sample_rate: Target sample rate in Hz (default 16000).
            mclk: MCLK frequency in Hz (default 12288000).
        """
        self._i2c = i2c

        if self.debug:
            print("ES7210 init @ 0x{:02X}, rate={}, mclk={}".format(
                self.addr, sample_rate, mclk))

        # Clock coefficient lookup (from esp-bsp es7210.c es7210_coeff_div table).
        # Fields: mclk, lrck, ss_ds, adc_div, dll, doubler, osr, mclk_src, lrck_h, lrck_l
        _COEFF = {
            (12288000, 16000): (0x00, 0x03, 0x01, 0x01, 0x20, 0x00, 0x03, 0x00),
            (12288000,  8000): (0x00, 0x06, 0x01, 0x01, 0x20, 0x00, 0x06, 0x00),
            (12288000, 24000): (0x00, 0x02, 0x01, 0x01, 0x20, 0x00, 0x02, 0x00),
            (12288000, 32000): (0x00, 0x03, 0x01, 0x00, 0x20, 0x00, 0x01, 0x80),
            (12288000, 48000): (0x00, 0x01, 0x01, 0x01, 0x20, 0x00, 0x01, 0x00),
            ( 4096000, 16000): (0x00, 0x01, 0x01, 0x01, 0x20, 0x00, 0x01, 0x00),
        }
        coeff = _COEFF.get((mclk, sample_rate))

        # 1. Software reset
        self._wr(self.REG_RESET, 0xFF)
        time.sleep_ms(10)
        self._wr(self.REG_RESET, 0x32)
        time.sleep_ms(10)

        # 2. Disable all clocks during configuration
        self._wr(self.REG_CLK_OFF, 0x3F)

        # 3. Timing / power-up settling
        self._wr(self.REG_TIME_CTL0, 0x30)
        self._wr(self.REG_TIME_CTL1, 0x30)

        # 4. High-pass filter configuration (removes DC offset)
        self._wr(self.REG_HPF12_1, 0x2A)
        self._wr(self.REG_HPF12_2, 0x0A)
        self._wr(self.REG_HPF34_1, 0x2A)
        self._wr(self.REG_HPF34_2, 0x0A)

        # 5. I2S format: master mode, I2S standard, 16-bit
        # Master mode (bit0=0): ES7210 generates BCLK and WS from MCLK.
        # Required because MicroPython I2S RX on ESP32-S3 does not reliably
        # drive BCLK/WS output clocks — hardware testing confirmed that slave
        # mode (0x01) produces no audio, while master mode (0x00) works.
        self._wr(self.REG_MODE_CFG, 0x00)   # master mode
        self._wr(self.REG_SDP1, 0x60)       # I2S, 16-bit
        self._wr(self.REG_SDP2, 0x00)       # normal (not TDM)

        # 6. Analog circuitry power-up
        self._wr(self.REG_ANALOG, 0xC3)

        # 7. Microphone bias voltage (2.87V for MEMS mics)
        self._wr(self.REG_MIC12_BIAS, 0x70)
        self._wr(self.REG_MIC34_BIAS, 0x70)

        # 8. Microphone PGA gain (max ~37.5dB for better voice pickup)
        # ES7210 PGA gain register: bits[3:0] set analog gain in ~3dB steps.
        # 0x0C = ~24dB, 0x0F = ~30dB max analog PGA.
        # Bit 4 enables additional +7.5dB boost (total ~37.5dB).
        self._wr(self.REG_MIC1_GAIN, 0x15)
        self._wr(self.REG_MIC2_GAIN, 0x15)
        self._wr(self.REG_MIC3_GAIN, 0x15)
        self._wr(self.REG_MIC4_GAIN, 0x15)

        # 8b. ADC digital gain (adds on top of PGA analog gain)
        # Registers 0x1B-0x1E: 0x00=-95.5dB, 0xBF=0dB, 0xFF=+32dB.
        # Max digital gain compensates for I2S dual-master clock contention
        # that causes ~88% sample dropout on ESP32-S3-BOX-3.
        # Silence threshold in audio.record() is raised accordingly.
        self._wr(self.REG_ADC1_GAIN, 0xFF)
        self._wr(self.REG_ADC2_GAIN, 0xFF)
        self._wr(self.REG_ADC3_GAIN, 0xFF)
        self._wr(self.REG_ADC4_GAIN, 0xFF)

        # 9. Power on microphone channels 1+2 (BOX-3 has 2 mics)
        self._wr(self.REG_MIC1_POWER, 0x08)
        self._wr(self.REG_MIC2_POWER, 0x08)
        self._wr(self.REG_MIC3_POWER, 0x08)
        self._wr(self.REG_MIC4_POWER, 0x08)

        # 10. Clock configuration from BSP coefficient table
        # REG02 = ss_ds[3:0] | adc_div[5:4] | (doubler << 6) | (dll << 7)
        # REG03 = mclk_src (master clock source selection)
        if coeff:
            ss_ds, adc_div, dll, doubler, osr, mclk_src, lrck_h, lrck_l = coeff
            self._wr(self.REG_MAINCLK, ss_ds | (adc_div << 4) | (doubler << 6) | (dll << 7))
            self._wr(self.REG_MASTERCLK, mclk_src)
            self._wr(self.REG_LRCK_DIVH, lrck_h)
            self._wr(self.REG_LRCK_DIVL, lrck_l)
            self._wr(self.REG_OSR, osr)
        else:
            # Fallback: assume MCLK = 256 * sample_rate
            if self.debug:
                print("ES7210 no coeff match, using 256*fs defaults")
            self._wr(self.REG_MAINCLK, 0xC1)  # ss_ds=1, adc_div=0, doubler=1, dll=1
            self._wr(self.REG_MASTERCLK, 0x00)  # mclk_src=0 (MCLK from pin)
            self._wr(self.REG_LRCK_DIVH, 0x01)
            self._wr(self.REG_LRCK_DIVL, 0x00)
            self._wr(self.REG_OSR, 0x20)

        # 11. Power down DLL, use direct MCLK path
        self._wr(self.REG_POWER_DOWN, 0x04)

        # 12. Mic 1-2 power control: enable bias, ADC, PGA
        self._wr(self.REG_MIC12_PWR, 0x0F)
        self._wr(self.REG_MIC34_PWR, 0x0F)

        # 13. Enable ADC channels and enter normal operation
        self._wr(self.REG_RESET, 0x71)   # enable ADC 1-4
        time.sleep_ms(5)
        self._wr(self.REG_RESET, 0x41)   # normal operation

        # 14. Re-enable clocks
        self._wr(self.REG_CLK_OFF, 0x00)

        if self.debug:
            for reg, name in (
                (self.REG_RESET, "RESET/CTL"),
                (self.REG_CLK_OFF, "CLK_OFF"),
                (self.REG_SDP1, "SDP1"),
                (self.REG_ANALOG, "ANALOG"),
            ):
                val = self._rd(reg)
                print("ES7210 R 0x{:02X} ({}) = 0x{:02X}".format(reg, name, val))
            print("ES7210 init complete")

    def set_gain(self, gain_db=37):
        """Set microphone PGA gain for all channels.

        Args:
            gain_db: Gain in dB (0 to 37). Clamped to valid range.
                     Bits[3:0] provide ~3dB steps (max ~30dB at 0x0F).
                     Bit 4 adds ~7.5dB boost for values > 30dB.
        """
        # Map dB to register value: 0-30dB -> 0x00-0x0F, 30-37dB -> 0x10-0x15
        if gain_db <= 30:
            val = max(0, min(0x0F, gain_db * 0x0F // 30))
        else:
            val = min(0x15, 0x10 + (gain_db - 30) * 5 // 7)
        self._wr(self.REG_MIC1_GAIN, val)
        self._wr(self.REG_MIC2_GAIN, val)
        self._wr(self.REG_MIC3_GAIN, val)
        self._wr(self.REG_MIC4_GAIN, val)
        if self.debug:
            print("ES7210 gain = 0x{:02X}".format(val))

    def deinit(self):
        """Power down the ES7210."""
        if self._i2c is None:
            return
        try:
            # Disable all ADC channels
            self._wr(self.REG_RESET, 0x32)
            # Power down analog
            self._wr(self.REG_ANALOG, 0x00)
            # Gate clocks
            self._wr(self.REG_CLK_OFF, 0x3F)
        except OSError:
            pass
        if self.debug:
            print("ES7210 deinit")
        self._i2c = None


# ---------------------------------------------------------------------------
# ES8311 — Low-power mono audio codec (used as DAC for speaker)
# ---------------------------------------------------------------------------

class ES8311:
    """I2C driver for the ES8311 mono audio codec.

    Default I2C address 0x18 (CE pin low).
    Used as DAC on ESP32-S3-BOX-3 for speaker output.
    """

    ADDR = 0x18

    # Register addresses
    REG_RESET     = 0x00
    REG_CLK01     = 0x01
    REG_CLK02     = 0x02
    REG_CLK03     = 0x03
    REG_CLK04     = 0x04
    REG_CLK05     = 0x05
    REG_CLK06     = 0x06
    REG_CLK07     = 0x07
    REG_CLK08     = 0x08
    REG_SDP_IN    = 0x09
    REG_SDP_OUT   = 0x0A
    REG_SYS0B     = 0x0B
    REG_SYS0C     = 0x0C
    REG_SYS0D     = 0x0D
    REG_SYS0E     = 0x0E
    REG_SYS0F     = 0x0F
    REG_SYS10     = 0x10
    REG_SYS11     = 0x11
    REG_SYS12     = 0x12
    REG_SYS13     = 0x13
    REG_SYS14     = 0x14
    REG_ADC15     = 0x15
    REG_ADC16     = 0x16
    REG_ADC17     = 0x17
    REG_ADC1B     = 0x1B
    REG_ADC1C     = 0x1C
    REG_DAC31     = 0x31
    REG_DAC32     = 0x32
    REG_DAC37     = 0x37
    REG_GPIO44    = 0x44
    REG_GP45      = 0x45

    # Clock coefficient table from esp-adf es8311.c coeff_div[]:
    # (mclk, rate, pre_div, pre_multi, adc_div, dac_div,
    #  fs_mode, lrck_h, lrck_l, bclk_div, adc_osr, dac_osr)
    _COEFF_TABLE = (
        # mclk       rate   pdiv pmul adiv ddiv fsm  lh   ll   bdiv aosr dosr
        # 256*fs entries
        (2048000,    8000,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
        (4096000,   16000,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
        (6144000,   24000,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
        (8192000,   32000,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
        (11289600,  44100,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
        (12288000,  48000,  0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
        # Fixed 12.288 MHz MCLK entries (used with single-clock design)
        (12288000,   8000,  0x06, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
        (12288000,  16000,  0x03, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
        (12288000,  24000,  0x02, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
        (12288000,  32000,  0x03, 0x02, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
    )

    def __init__(self, addr=None, debug=False):
        self.addr = addr or self.ADDR
        self.debug = debug
        self._i2c = None

    def _wr(self, reg, val):
        """Write a single byte to a register."""
        self._i2c.writeto_mem(self.addr, reg, bytes([val]))
        if self.debug:
            print("ES8311 W 0x{:02X} = 0x{:02X}".format(reg, val))
        time.sleep_ms(1)

    def _rd(self, reg):
        """Read a single byte from a register."""
        data = self._i2c.readfrom_mem(self.addr, reg, 1)
        return data[0]

    def _find_coeff(self, mclk, rate):
        """Look up clock coefficients for given MCLK and sample rate."""
        for row in self._COEFF_TABLE:
            if row[0] == mclk and row[1] == rate:
                return row
        return None

    def init(self, i2c, sample_rate=24000, mclk=12288000):
        """Initialize the ES8311 for speaker output (DAC mode).

        Args:
            i2c: machine.I2C instance (already initialized).
            sample_rate: Target sample rate in Hz (default 24000 for TTS playback).
            mclk: MCLK frequency in Hz (default 12288000).
        """
        self._i2c = i2c

        if self.debug:
            print("ES8311 init @ 0x{:02X}, rate={}, mclk={}".format(
                self.addr, sample_rate, mclk))

        # 1. Soft reset
        self._wr(self.REG_RESET, 0x1F)
        time.sleep_ms(10)
        self._wr(self.REG_RESET, 0x00)
        time.sleep_ms(10)

        # 2. I2C noise immunity (write twice per datasheet recommendation)
        self._wr(self.REG_GPIO44, 0x08)
        self._wr(self.REG_GPIO44, 0x08)

        # 3. Clock configuration
        coeff = self._find_coeff(mclk, sample_rate)

        # REG01: clock manager
        # bit7: 0=MCLK from pin, 1=MCLK from SCLK
        # bit6-5: MCLK/SCLK inversion control
        # bit4-0: enable various clocks
        self._wr(self.REG_CLK01, 0x3F)  # MCLK from pin, enable all clocks

        if coeff:
            # Apply coefficients from table
            pre_div = coeff[2]
            pre_multi = coeff[3]
            adc_div = coeff[4]
            dac_div = coeff[5]
            fs_mode = coeff[6]
            lrck_h = coeff[7]
            lrck_l = coeff[8]
            bclk_div = coeff[9]
            adc_osr = coeff[10]
            dac_osr = coeff[11]

            # REG02: pre_div[4:0] | pre_multi[6:5]
            self._wr(self.REG_CLK02, (pre_multi << 5) | pre_div)
            # REG03: fs_mode[6] | adc_osr[5:0]
            self._wr(self.REG_CLK03, (fs_mode << 6) | adc_osr)
            # REG04: dac_osr
            self._wr(self.REG_CLK04, dac_osr)
            # REG05: adc_div[7:4] | dac_div[3:0]
            self._wr(self.REG_CLK05, (adc_div << 4) | dac_div)
            # REG06: bclk divider
            self._wr(self.REG_CLK06, bclk_div)
            # REG07-08: LRCK divider
            self._wr(self.REG_CLK07, lrck_h)
            self._wr(self.REG_CLK08, lrck_l)
        else:
            # Fallback: generic settings for 256*fs MCLK ratio
            if self.debug:
                print("ES8311 no coeff match, using defaults")
            self._wr(self.REG_CLK02, 0x21)  # pre_div=1, pre_multi=1
            self._wr(self.REG_CLK03, 0x10)  # single speed, osr=16
            self._wr(self.REG_CLK04, 0x10)  # dac osr=16
            self._wr(self.REG_CLK05, 0x11)  # adc_div=1, dac_div=1
            self._wr(self.REG_CLK06, 0x04)  # bclk divider
            self._wr(self.REG_CLK07, 0x00)  # lrck div high
            self._wr(self.REG_CLK08, 0xFF)  # lrck div low

        # 4. System power control (power down during config)
        self._wr(self.REG_SYS0B, 0x00)
        self._wr(self.REG_SYS0C, 0x00)

        # 5. Analog power
        self._wr(self.REG_SYS10, 0x1F)
        self._wr(self.REG_SYS11, 0x7F)

        # 6. I2S format: 16-bit, I2S standard
        # REG09 (SDP IN - data to DAC):
        #   bits[3:2] = word length: 00=24, 01=20, 10=18, 11=16
        #   bits[1:0] = format: 00=I2S
        self._wr(self.REG_SDP_IN, 0x0C)   # 16-bit I2S
        self._wr(self.REG_SDP_OUT, 0x0C)  # 16-bit I2S (ADC output format)

        # 7. Power up analog circuitry
        self._wr(self.REG_SYS0D, 0x01)   # power up analog block
        self._wr(self.REG_SYS0E, 0x02)   # enable analog PGA + ADC modulator
        self._wr(self.REG_SYS12, 0x00)   # power up DAC
        self._wr(self.REG_SYS13, 0x10)   # enable headphone / line driver
        self._wr(self.REG_SYS14, 0x1A)   # mic input config (analog)

        # 8. ADC configuration (even though we mainly use DAC)
        self._wr(self.REG_ADC16, 0x24)   # ADC mic gain
        self._wr(self.REG_ADC17, 0xC8)   # ADC gain setting
        self._wr(self.REG_ADC1B, 0x0A)   # ADC filter
        self._wr(self.REG_ADC1C, 0x6A)   # ADC EQ bypass, DC offset cancel

        # 9. DAC EQ bypass
        self._wr(self.REG_DAC37, 0x08)

        # 10. Unmute DAC
        self._wr(self.REG_DAC31, 0x00)

        # 11. Set volume to 0dB
        self._wr(self.REG_DAC32, 0xBF)   # 0xBF = 0dB

        # 12. Start state machine (power on)
        # REG00 bit7: CSM_ON=1 starts the codec state machine
        self._wr(self.REG_RESET, 0x80)

        if self.debug:
            for reg, name in (
                (self.REG_RESET, "RESET/CSM"),
                (self.REG_CLK01, "CLK01"),
                (self.REG_SDP_IN, "SDP_IN"),
                (self.REG_DAC32, "DAC_VOL"),
            ):
                val = self._rd(reg)
                print("ES8311 R 0x{:02X} ({}) = 0x{:02X}".format(reg, name, val))
            print("ES8311 init complete")

    def set_volume(self, level):
        """Set DAC output volume.

        Args:
            level: Volume 0-100 (0=mute, 100=0dB).
                   Maps linearly to register 0x00-0xBF.
                   Values above 0xBF add positive gain and risk clipping.
        """
        if self._i2c is None:
            return
        level = max(0, min(100, level))
        # ES8311 volume register: 0x00=-95.5dB, 0xBF=0dB, 0xFF=+32dB
        # Cap at 0xBF to avoid positive gain that can clip into PA
        reg_val = (level * 0xBF) // 100
        self._wr(self.REG_DAC32, reg_val)
        if self.debug:
            print("ES8311 volume = {} (reg 0x{:02X})".format(level, reg_val))

    def mute(self, enable=True):
        """Mute or unmute the DAC output.

        Args:
            enable: True to mute, False to unmute.
        """
        if self._i2c is None:
            return
        # REG31 bit6: DAC soft mute, bit5: DAC soft mute rate
        self._wr(self.REG_DAC31, 0x60 if enable else 0x00)

    def deinit(self):
        """Power down the ES8311."""
        if self._i2c is None:
            return
        try:
            # Mute first to avoid pop
            self._wr(self.REG_DAC31, 0x60)
            time.sleep_ms(10)
            # Power down DAC
            self._wr(self.REG_SYS12, 0x02)
            # Disable HP driver
            self._wr(self.REG_SYS13, 0x00)
            # Power down analog
            self._wr(self.REG_SYS0D, 0x00)
            # Stop state machine
            self._wr(self.REG_RESET, 0x00)
            # Gate clocks
            self._wr(self.REG_CLK01, 0x00)
        except OSError:
            pass
        if self.debug:
            print("ES8311 deinit")
        self._i2c = None
