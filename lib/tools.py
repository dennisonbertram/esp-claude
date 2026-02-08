# tools.py — Tool registry for ESP-Claude
#
# Tools are functions the AI can call. Each tool has:
#   - name: unique identifier
#   - description: what the AI sees (be descriptive!)
#   - parameters: JSON Schema for the input
#   - execute: function(params) -> string result


class ToolError(Exception):
    """Raised by tool functions to signal an error to the LLM."""
    pass


class ToolRegistry:
    """Registry of tools available to the agent."""

    def __init__(self):
        self._tools = {}
        self._api_cache = None

    def register(self, name, description, parameters, execute):
        """Register a tool.

        Args:
            name: Tool name (lowercase, underscores)
            description: What this tool does (shown to the LLM)
            parameters: JSON Schema dict for parameters
            execute: Function that takes params dict, returns result string
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "execute": execute,
        }
        self._api_cache = None  # Invalidate cache

    def get(self, name):
        """Get a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def execute(self, name, params):
        """Execute a tool by name with given params.

        Returns (result_string, is_error) tuple.
        """
        tool = self._tools.get(name)
        if not tool:
            return f"Unknown tool: {name}", True

        try:
            result = tool["execute"](params)
            if result is None:
                result = "OK"
            return str(result), False
        except ToolError as e:
            return str(e), True
        except Exception as e:
            return f"Error executing {name}: {e}", True

    def to_api_format(self):
        """Convert all tools to Anthropic API format. Cached after first build."""
        if self._api_cache is not None:
            return self._api_cache
        tools = []
        for name, tool in self._tools.items():
            tools.append({
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["parameters"],
            })
        self._api_cache = tools
        return tools

    def list_names(self):
        """List all registered tool names."""
        return list(self._tools.keys())


# --- Helper for building JSON Schema parameter definitions ---

def make_params(properties, required=None):
    """Build a JSON Schema object for tool parameters.

    Usage:
        make_params({
            "pin": {"type": "integer", "description": "GPIO pin number"},
            "state": {"type": "boolean", "description": "On or off"},
        }, required=["pin", "state"])
    """
    schema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def no_params():
    """Schema for a tool that takes no parameters."""
    return {"type": "object", "properties": {}}


# --- Built-in tools that work on any ESP32 ---

def register_system_tools(registry):
    """Register tools that work on any ESP32 (no external hardware)."""

    import gc
    import os

    def tool_get_free_memory(params):
        gc.collect()
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        total = free + alloc
        return f"Free: {free} bytes, Used: {alloc} bytes, Total: {total} bytes ({free * 100 // total}% free)"

    registry.register(
        "get_free_memory",
        "Get the current free memory on the ESP32. Use this to monitor memory usage.",
        no_params(),
        tool_get_free_memory,
    )

    def tool_get_system_info(params):
        import sys
        freq = machine_freq_mhz()
        platform = sys.platform
        version = sys.version
        fs_stat = os.statvfs("/")
        fs_free = fs_stat[0] * fs_stat[3]  # block size * free blocks
        fs_total = fs_stat[0] * fs_stat[2]  # block size * total blocks
        return (
            f"Platform: {platform}, MicroPython: {version}, "
            f"CPU: {freq}MHz, "
            f"Flash free: {fs_free // 1024}KB / {fs_total // 1024}KB"
        )

    registry.register(
        "get_system_info",
        "Get ESP32 system information (platform, CPU frequency, flash storage).",
        no_params(),
        tool_get_system_info,
    )

    def tool_set_cpu_freq(params):
        import machine
        freq_mhz = params.get("mhz", 160)
        if not isinstance(freq_mhz, int):
            raise ToolError("mhz must be an integer")
        if freq_mhz not in [80, 160, 240]:
            raise ToolError("frequency must be 80, 160, or 240 MHz")
        machine.freq(freq_mhz * 1_000_000)
        return f"CPU frequency set to {freq_mhz}MHz"

    registry.register(
        "set_cpu_frequency",
        "Set the ESP32 CPU frequency. Lower = less power. Options: 80, 160, 240 MHz.",
        make_params({
            "mhz": {"type": "integer", "description": "Frequency in MHz: 80, 160, or 240"},
        }, required=["mhz"]),
        tool_set_cpu_freq,
    )

    def tool_sleep_ms(params):
        import time
        ms = params.get("milliseconds", 1000)
        if not isinstance(ms, int) or ms < 0:
            raise ToolError("milliseconds must be a non-negative integer")
        time.sleep_ms(ms)
        return f"Slept for {ms}ms"

    registry.register(
        "delay",
        "Wait for a specified number of milliseconds.",
        make_params({
            "milliseconds": {"type": "integer", "description": "Milliseconds to wait"},
        }, required=["milliseconds"]),
        tool_sleep_ms,
    )


def machine_freq_mhz():
    """Get CPU frequency in MHz."""
    try:
        import machine
        return machine.freq() // 1_000_000
    except:
        return 0


# --- GPIO tools ---

def register_gpio_tools(registry, allowed_pins=None):
    """Register tools for direct GPIO control.

    Args:
        registry: ToolRegistry instance
        allowed_pins: List of allowed GPIO pin numbers (None = all allowed)
    """
    import machine

    # Track configured pins and hardware peripherals
    _pins = {}
    _adc_cache = {}
    _pwm_cache = {}

    def _check_pin(pin_num):
        if not isinstance(pin_num, int):
            raise ToolError("pin must be an integer")
        if allowed_pins and pin_num not in allowed_pins:
            raise ToolError(f"Pin {pin_num} not in allowed pins: {allowed_pins}")
        return pin_num

    def tool_digital_write(params):
        pin_num = _check_pin(params["pin"])
        value = 1 if params["value"] else 0
        if pin_num not in _pins:
            _pins[pin_num] = machine.Pin(pin_num, machine.Pin.OUT)
        _pins[pin_num].value(value)
        return f"Pin {pin_num} set to {'HIGH' if value else 'LOW'}"

    registry.register(
        "digital_write",
        "Set a GPIO pin HIGH (true) or LOW (false). Pin is configured as output automatically.",
        make_params({
            "pin": {"type": "integer", "description": "GPIO pin number"},
            "value": {"type": "boolean", "description": "true for HIGH, false for LOW"},
        }, required=["pin", "value"]),
        tool_digital_write,
    )

    def tool_digital_read(params):
        pin_num = _check_pin(params["pin"])
        if pin_num not in _pins or not isinstance(_pins[pin_num], machine.Pin):
            _pins[pin_num] = machine.Pin(pin_num, machine.Pin.IN, machine.Pin.PULL_UP)
        val = _pins[pin_num].value()
        return f"Pin {pin_num} reads {'HIGH' if val else 'LOW'}"

    registry.register(
        "digital_read",
        "Read the digital value of a GPIO pin (HIGH or LOW). Pin is configured as input with pull-up.",
        make_params({
            "pin": {"type": "integer", "description": "GPIO pin number"},
        }, required=["pin"]),
        tool_digital_read,
    )

    def tool_analog_read(params):
        from machine import ADC
        pin_num = _check_pin(params["pin"])
        if pin_num not in _adc_cache:
            _adc_cache[pin_num] = ADC(machine.Pin(pin_num))
            _adc_cache[pin_num].atten(ADC.ATTN_11DB)  # Full range 0-3.3V
        adc = _adc_cache[pin_num]
        raw = adc.read()
        voltage = raw / 4095 * 3.3
        return f"Pin {pin_num} analog: raw={raw}/4095, voltage={voltage:.2f}V"

    registry.register(
        "analog_read",
        "Read an analog value from a GPIO pin (ADC). Returns raw 12-bit value (0-4095) and voltage (0-3.3V).",
        make_params({
            "pin": {"type": "integer", "description": "GPIO pin number (must be ADC-capable: 32-39)"},
        }, required=["pin"]),
        tool_analog_read,
    )

    def tool_pwm_write(params):
        from machine import PWM
        pin_num = _check_pin(params["pin"])
        duty = params["duty"]  # 0-100 percent
        if not isinstance(duty, (int, float)) or duty < 0 or duty > 100:
            raise ToolError("duty must be a number between 0 and 100")
        freq = params.get("frequency", 1000)
        if not isinstance(freq, int) or freq <= 0:
            raise ToolError("frequency must be a positive integer")
        if pin_num in _pwm_cache:
            pwm = _pwm_cache[pin_num]
            pwm.freq(freq)
            pwm.duty(int(duty * 1023 / 100))
        else:
            pwm = PWM(machine.Pin(pin_num), freq=freq, duty=int(duty * 1023 / 100))
            _pwm_cache[pin_num] = pwm
        _pins[pin_num] = pwm
        return f"Pin {pin_num} PWM: duty={duty}%, freq={freq}Hz"

    registry.register(
        "pwm_write",
        "Set PWM output on a GPIO pin. Useful for dimming LEDs, controlling motor speed, etc.",
        make_params({
            "pin": {"type": "integer", "description": "GPIO pin number"},
            "duty": {"type": "number", "description": "Duty cycle 0-100 (percent)"},
            "frequency": {"type": "integer", "description": "PWM frequency in Hz (default 1000)"},
        }, required=["pin", "duty"]),
        tool_pwm_write,
    )


# --- HTTP/Webhook tools ---

def register_webhook_tools(registry):
    """Register tools for making HTTP requests (webhooks, notifications)."""

    def tool_http_get(params):
        import urequests
        url = params["url"]
        if not isinstance(url, str) or not url.startswith("http"):
            raise ToolError("url must be a string starting with http:// or https://")
        try:
            r = urequests.get(url, timeout=10)
            body = r.text[:512]  # Limit response size
            status = r.status_code
            r.close()
            return f"HTTP {status}: {body}"
        except Exception as e:
            raise ToolError(f"HTTP GET failed: {e}")

    registry.register(
        "http_get",
        "Make an HTTP GET request to a URL. Response is truncated to 512 bytes.",
        make_params({
            "url": {"type": "string", "description": "URL to request"},
        }, required=["url"]),
        tool_http_get,
    )

    def tool_http_post(params):
        import urequests
        url = params["url"]
        if not isinstance(url, str) or not url.startswith("http"):
            raise ToolError("url must be a string starting with http:// or https://")
        body = params.get("body", "")
        content_type = params.get("content_type", "application/json")
        try:
            r = urequests.post(
                url,
                data=body,
                headers={"Content-Type": content_type},
                timeout=10,
            )
            resp = r.text[:512]
            status = r.status_code
            r.close()
            return f"HTTP {status}: {resp}"
        except Exception as e:
            raise ToolError(f"HTTP POST failed: {e}")

    registry.register(
        "http_post",
        "Make an HTTP POST request. Use this to send webhooks or notifications.",
        make_params({
            "url": {"type": "string", "description": "URL to POST to"},
            "body": {"type": "string", "description": "Request body (usually JSON string)"},
            "content_type": {"type": "string", "description": "Content-Type header (default: application/json)"},
        }, required=["url"]),
        tool_http_post,
    )


# --- NeoPixel tools ---

def register_neopixel_tools(registry, pin, num_leds):
    """Register tools for controlling NeoPixel/WS2812 LED strips.

    Args:
        registry: ToolRegistry instance
        pin: GPIO pin number connected to NeoPixel data
        num_leds: Number of LEDs in the strip
    """
    import machine
    import neopixel

    np = neopixel.NeoPixel(machine.Pin(pin), num_leds)

    def _clamp_color(val, name):
        if not isinstance(val, int):
            raise ToolError(f"{name} must be an integer")
        if val < 0 or val > 255:
            raise ToolError(f"{name} must be 0-255, got {val}")
        return val

    def tool_set_led(params):
        index = params["index"]
        if not isinstance(index, int) or index < 0 or index >= num_leds:
            raise ToolError(f"index must be an integer 0-{num_leds - 1}")
        r = _clamp_color(params["red"], "red")
        g = _clamp_color(params["green"], "green")
        b = _clamp_color(params["blue"], "blue")
        np[index] = (r, g, b)
        np.write()
        return f"LED {index} set to RGB({r},{g},{b})"

    registry.register(
        "set_led_color",
        f"Set a NeoPixel LED color. There are {num_leds} LEDs (index 0-{num_leds - 1}).",
        make_params({
            "index": {"type": "integer", "description": f"LED index (0-{num_leds - 1})"},
            "red": {"type": "integer", "description": "Red 0-255"},
            "green": {"type": "integer", "description": "Green 0-255"},
            "blue": {"type": "integer", "description": "Blue 0-255"},
        }, required=["index", "red", "green", "blue"]),
        tool_set_led,
    )

    def tool_set_all_leds(params):
        r = _clamp_color(params["red"], "red")
        g = _clamp_color(params["green"], "green")
        b = _clamp_color(params["blue"], "blue")
        for i in range(num_leds):
            np[i] = (r, g, b)
        np.write()
        return f"All {num_leds} LEDs set to RGB({r},{g},{b})"

    registry.register(
        "set_all_leds",
        f"Set all {num_leds} NeoPixel LEDs to the same color.",
        make_params({
            "red": {"type": "integer", "description": "Red 0-255"},
            "green": {"type": "integer", "description": "Green 0-255"},
            "blue": {"type": "integer", "description": "Blue 0-255"},
        }, required=["red", "green", "blue"]),
        tool_set_all_leds,
    )

    def tool_clear_leds(params):
        for i in range(num_leds):
            np[i] = (0, 0, 0)
        np.write()
        return f"All {num_leds} LEDs turned off"

    registry.register(
        "clear_leds",
        "Turn off all NeoPixel LEDs.",
        no_params(),
        tool_clear_leds,
    )
