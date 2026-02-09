"""
Mock-based unit tests for ESP-Claude lib/agent.py and lib/tools.py.

These tests run on standard CPython by mocking MicroPython-specific modules
(machine, urequests, ujson, gc, etc.) before importing the project code.
"""

import sys
import json
import time as stdlib_time
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Mock MicroPython modules BEFORE importing any project code
# ---------------------------------------------------------------------------

# machine module mock
mock_machine = MagicMock()
mock_machine.Pin.OUT = 1
mock_machine.Pin.IN = 0
mock_machine.Pin.PULL_UP = 1
mock_machine.freq.return_value = 160_000_000

# ADC mock class that tracks instantiation
class MockADC:
    """Track ADC construction calls for caching tests."""
    call_count = 0
    ATTN_11DB = 3

    def __init__(self, pin):
        MockADC.call_count += 1
        self._pin = pin

    def atten(self, val):
        pass

    def read(self):
        return 2048  # mid-range

mock_machine.ADC = MockADC

# PWM mock
class MockPWM:
    def __init__(self, pin, freq=1000, duty=0):
        self._pin = pin
        self._freq = freq
        self._duty = duty

    def freq(self, f=None):
        if f is not None:
            self._freq = f
        return self._freq

    def duty(self, d=None):
        if d is not None:
            self._duty = d
        return self._duty

mock_machine.PWM = MockPWM

sys.modules["machine"] = mock_machine

# urequests mock
mock_urequests = MagicMock()
sys.modules["urequests"] = mock_urequests

# ujson -> map to stdlib json
sys.modules["ujson"] = json

# gc mock — expose standard gc but add MicroPython-specific methods
import gc as real_gc
mock_gc = MagicMock()
mock_gc.collect = real_gc.collect
mock_gc.mem_free = MagicMock(return_value=100000)
mock_gc.mem_alloc = MagicMock(return_value=50000)
sys.modules["gc"] = mock_gc

# network, dht, neopixel, usocket, utime mocks
sys.modules["network"] = MagicMock()
sys.modules["dht"] = MagicMock()
sys.modules["neopixel"] = MagicMock()

mock_usocket = MagicMock()
sys.modules["usocket"] = mock_usocket

# utime -> map to stdlib time, but add MicroPython-specific tick functions
mock_utime = MagicMock()
mock_utime.sleep = stdlib_time.sleep
mock_utime.sleep_ms = MagicMock()
mock_utime.ticks_ms = MagicMock(return_value=0)
mock_utime.ticks_diff = MagicMock(side_effect=lambda a, b: a - b)
sys.modules["utime"] = mock_utime

# Patch stdlib time with ticks_ms/ticks_diff/sleep_ms for agent.py which uses `import time`
# On MicroPython, `time` IS `utime`. On CPython we need to add the missing attrs.
if not hasattr(stdlib_time, "ticks_ms"):
    stdlib_time.ticks_ms = lambda: int(stdlib_time.time() * 1000)
if not hasattr(stdlib_time, "ticks_diff"):
    stdlib_time.ticks_diff = lambda a, b: a - b
if not hasattr(stdlib_time, "sleep_ms"):
    stdlib_time.sleep_ms = lambda ms: stdlib_time.sleep(ms / 1000)

# ---------------------------------------------------------------------------
# Now import project modules
# ---------------------------------------------------------------------------
from lib.tools import ToolRegistry, ToolError, make_params, no_params, register_gpio_tools
from lib.agent import Agent, ScheduledAgent


# ===================================================================
# Test 1: Message pruning preserves tool_use/tool_result pairs
# ===================================================================
class TestMessagePruning(unittest.TestCase):
    """Verify that _add_message pruning never leaves orphaned tool_results."""

    def _make_agent(self, max_messages=6):
        return Agent(
            api_key="test-key",
            model="test-model",
            system_prompt="test",
            max_messages=max_messages,
        )

    def test_pruning_preserves_tool_pairs(self):
        """Fill history past limit with tool_use/tool_result pairs.
        After pruning, the first kept message must NOT be an orphaned tool_result."""
        agent = self._make_agent(max_messages=6)

        # Message 0: initial user message (always kept)
        agent._add_message("user", [{"type": "text", "text": "hello"}])

        # Build several tool_use/tool_result pairs
        for i in range(5):
            # Assistant message with a tool_use block
            agent._add_message("assistant", [
                {"type": "text", "text": f"thinking {i}"},
                {"type": "tool_use", "id": f"tool_{i}", "name": "test",
                 "input": {}},
            ])
            # User message with tool_result
            agent._add_message("user", [
                {"type": "tool_result", "tool_use_id": f"tool_{i}",
                 "content": f"result {i}", "is_error": False},
            ])

        # We should have pruned — check no orphaned tool_result at start
        # The first message is always messages[0] (the original user msg)
        # The second message (messages[1]) should not be an orphaned tool_result
        self.assertTrue(len(agent.messages) <= 6,
                        f"Expected <= 6 messages, got {len(agent.messages)}")

        # Check messages[1] (first message after the always-kept first user msg)
        if len(agent.messages) > 1:
            second_msg = agent.messages[1]
            if second_msg["role"] == "user" and isinstance(second_msg["content"], list):
                first_block = second_msg["content"][0]
                if isinstance(first_block, dict):
                    self.assertNotEqual(
                        first_block.get("type"), "tool_result",
                        "First kept message after initial is an orphaned tool_result!"
                    )

    def test_pruning_no_orphaned_assistant_tool_use(self):
        """The message just before pruning boundary should not be an assistant
        with tool_use (which would leave it without a corresponding result)."""
        agent = self._make_agent(max_messages=4)

        agent._add_message("user", [{"type": "text", "text": "start"}])

        for i in range(4):
            agent._add_message("assistant", [
                {"type": "tool_use", "id": f"t{i}", "name": "test", "input": {}},
            ])
            agent._add_message("user", [
                {"type": "tool_result", "tool_use_id": f"t{i}",
                 "content": "ok", "is_error": False},
            ])

        # Verify: no kept message is an orphaned tool_result without its tool_use
        for idx in range(1, len(agent.messages)):
            msg = agent.messages[idx]
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for block in msg["content"]:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        # Find the corresponding tool_use in a preceding assistant message
                        tool_use_id = block["tool_use_id"]
                        found = False
                        for prev_idx in range(idx):
                            prev = agent.messages[prev_idx]
                            if prev["role"] == "assistant" and isinstance(prev["content"], list):
                                for pblock in prev["content"]:
                                    if isinstance(pblock, dict) and pblock.get("type") == "tool_use" and pblock.get("id") == tool_use_id:
                                        found = True
                                        break
                            if found:
                                break
                        self.assertTrue(found,
                                        f"Orphaned tool_result {tool_use_id} at index {idx}")


# ===================================================================
# Test 2: ToolError propagation
# ===================================================================
class TestToolError(unittest.TestCase):
    """Verify ToolError is caught and returned as (message, True)."""

    def test_tool_error_propagation(self):
        registry = ToolRegistry()

        def failing_tool(params):
            raise ToolError("sensor disconnected")

        registry.register(
            "fail_tool",
            "A tool that always fails",
            no_params(),
            failing_tool,
        )

        result_text, is_error = registry.execute("fail_tool", {})
        self.assertTrue(is_error, "Expected is_error=True for ToolError")
        self.assertEqual(result_text, "sensor disconnected")

    def test_generic_exception_propagation(self):
        registry = ToolRegistry()

        def buggy_tool(params):
            raise ValueError("unexpected bug")

        registry.register("buggy", "Buggy tool", no_params(), buggy_tool)
        result_text, is_error = registry.execute("buggy", {})
        self.assertTrue(is_error)
        self.assertIn("Error executing buggy", result_text)
        self.assertIn("unexpected bug", result_text)

    def test_unknown_tool(self):
        registry = ToolRegistry()
        result_text, is_error = registry.execute("nonexistent", {})
        self.assertTrue(is_error)
        self.assertIn("Unknown tool", result_text)


# ===================================================================
# Test 3: PWM/ADC caching (analog_read)
# ===================================================================
class TestADCCaching(unittest.TestCase):
    """Verify ADC objects are cached per pin — constructor called only once."""

    def test_adc_cached_on_same_pin(self):
        # Reset the call counter
        MockADC.call_count = 0

        registry = ToolRegistry()
        register_gpio_tools(registry)

        # First read on pin 34
        result1, err1 = registry.execute("analog_read", {"pin": 34})
        self.assertFalse(err1, f"First analog_read failed: {result1}")
        self.assertEqual(MockADC.call_count, 1,
                         "ADC should be constructed once on first read")

        # Second read on same pin 34
        result2, err2 = registry.execute("analog_read", {"pin": 34})
        self.assertFalse(err2, f"Second analog_read failed: {result2}")
        self.assertEqual(MockADC.call_count, 1,
                         "ADC should be cached — constructor not called again")

    def test_adc_different_pins_create_separate_instances(self):
        MockADC.call_count = 0

        registry = ToolRegistry()
        register_gpio_tools(registry)

        registry.execute("analog_read", {"pin": 32})
        self.assertEqual(MockADC.call_count, 1)

        registry.execute("analog_read", {"pin": 33})
        self.assertEqual(MockADC.call_count, 2,
                         "Different pin should create a new ADC instance")


# ===================================================================
# Test 4: Tool API format caching
# ===================================================================
class TestAPIFormatCaching(unittest.TestCase):
    """Verify to_api_format() caching and invalidation."""

    def test_api_format_cached(self):
        registry = ToolRegistry()
        registry.register("tool_a", "Tool A", no_params(), lambda p: "a")

        fmt1 = registry.to_api_format()
        fmt2 = registry.to_api_format()
        self.assertIs(fmt1, fmt2,
                      "Second call should return same cached object")

    def test_api_format_invalidated_on_register(self):
        registry = ToolRegistry()
        registry.register("tool_a", "Tool A", no_params(), lambda p: "a")

        fmt1 = registry.to_api_format()

        # Register a new tool — should invalidate cache
        registry.register("tool_b", "Tool B", no_params(), lambda p: "b")

        fmt2 = registry.to_api_format()
        self.assertIsNot(fmt1, fmt2,
                         "Cache should be invalidated after registering a new tool")
        self.assertEqual(len(fmt2), 2, "Should have 2 tools in API format")

    def test_api_format_structure(self):
        registry = ToolRegistry()
        params = make_params({"x": {"type": "integer"}}, required=["x"])
        registry.register("my_tool", "Does stuff", params, lambda p: "ok")

        fmt = registry.to_api_format()
        self.assertEqual(len(fmt), 1)
        self.assertEqual(fmt[0]["name"], "my_tool")
        self.assertEqual(fmt[0]["description"], "Does stuff")
        self.assertEqual(fmt[0]["input_schema"], params)


# ===================================================================
# Test 5: Input validation for GPIO tools
# ===================================================================
class TestInputValidation(unittest.TestCase):
    """Verify GPIO tools reject invalid inputs."""

    def setUp(self):
        MockADC.call_count = 0
        self.registry = ToolRegistry()
        register_gpio_tools(self.registry)

    def test_analog_read_non_integer_pin(self):
        result, is_error = self.registry.execute("analog_read", {"pin": "abc"})
        self.assertTrue(is_error, "Should reject non-integer pin")
        self.assertIn("pin must be an integer", result)

    def test_digital_write_non_integer_pin(self):
        result, is_error = self.registry.execute("digital_write",
                                                  {"pin": "bad", "value": True})
        self.assertTrue(is_error)
        self.assertIn("pin must be an integer", result)

    def test_pwm_duty_out_of_range(self):
        result, is_error = self.registry.execute("pwm_write",
                                                  {"pin": 5, "duty": 150})
        self.assertTrue(is_error)
        self.assertIn("duty must be a number between 0 and 100", result)

    def test_pwm_negative_duty(self):
        result, is_error = self.registry.execute("pwm_write",
                                                  {"pin": 5, "duty": -10})
        self.assertTrue(is_error)
        self.assertIn("duty must be a number between 0 and 100", result)

    def test_pwm_invalid_frequency(self):
        result, is_error = self.registry.execute("pwm_write",
                                                  {"pin": 5, "duty": 50,
                                                   "frequency": -1})
        self.assertTrue(is_error)
        self.assertIn("frequency must be a positive integer", result)

    def test_allowed_pins_restriction(self):
        registry = ToolRegistry()
        register_gpio_tools(registry, allowed_pins=[2, 4])

        result, is_error = registry.execute("digital_write",
                                            {"pin": 99, "value": True})
        self.assertTrue(is_error)
        self.assertIn("not in allowed pins", result)

    def test_allowed_pin_succeeds(self):
        registry = ToolRegistry()
        register_gpio_tools(registry, allowed_pins=[2, 4])

        result, is_error = registry.execute("digital_write",
                                            {"pin": 2, "value": True})
        self.assertFalse(is_error, f"Expected success for allowed pin, got: {result}")


# ===================================================================
# Test 6: Timing drift correction in ScheduledAgent
# ===================================================================
class TestScheduledAgentTiming(unittest.TestCase):
    """Verify ScheduledAgent accounts for processing time in sleep interval."""

    def test_drift_correction_subtracts_elapsed(self):
        """Simulate a cycle that takes 500ms; sleep should be interval - 500ms."""
        # We'll track the sleep_ms call
        sleep_calls = []
        original_sleep_ms = stdlib_time.sleep_ms

        def mock_sleep_ms(ms):
            sleep_calls.append(ms)
            # Raise to break out of run_forever loop after first sleep
            raise KeyboardInterrupt("stop after first cycle")

        stdlib_time.sleep_ms = mock_sleep_ms

        # Control ticks_ms to simulate 500ms elapsed
        tick_values = iter([1000, 1500])  # start=1000, end=1500 -> elapsed=500ms
        original_ticks_ms = stdlib_time.ticks_ms
        stdlib_time.ticks_ms = lambda: next(tick_values)

        try:
            agent = ScheduledAgent(
                api_key="test",
                model="test",
                system_prompt="test",
                interval_seconds=10,
                recurring_prompt="check",
            )

            # Mock prompt to return immediately
            agent.prompt = MagicMock(return_value="ok")
            agent.reset = MagicMock()

            try:
                agent.run_forever()
            except (KeyboardInterrupt, StopIteration):
                pass

            self.assertEqual(len(sleep_calls), 1,
                             f"Expected 1 sleep call, got {len(sleep_calls)}")
            # interval=10s=10000ms, elapsed=500ms, sleep should be 9500ms
            self.assertEqual(sleep_calls[0], 9500,
                             f"Expected sleep_ms(9500), got sleep_ms({sleep_calls[0]})")

        finally:
            stdlib_time.sleep_ms = original_sleep_ms
            stdlib_time.ticks_ms = original_ticks_ms

    def test_no_sleep_when_over_budget(self):
        """If cycle takes longer than interval, no sleep should happen."""
        sleep_calls = []
        original_sleep_ms = stdlib_time.sleep_ms

        def mock_sleep_ms(ms):
            sleep_calls.append(ms)
            raise KeyboardInterrupt("stop")

        stdlib_time.sleep_ms = mock_sleep_ms

        # elapsed = 15000ms > interval 10000ms
        tick_values = iter([0, 15000])
        original_ticks_ms = stdlib_time.ticks_ms
        stdlib_time.ticks_ms = lambda: next(tick_values)

        try:
            agent = ScheduledAgent(
                api_key="test",
                model="test",
                system_prompt="test",
                interval_seconds=10,
                recurring_prompt="check",
            )
            agent.prompt = MagicMock(return_value="ok")
            agent.reset = MagicMock()

            # run_forever should NOT call sleep_ms since elapsed > interval
            # It should loop again, but our ticks_ms iterator is exhausted, so StopIteration
            try:
                agent.run_forever()
            except (KeyboardInterrupt, StopIteration):
                pass

            # sleep_ms should not have been called (or if it was, verify no positive sleep)
            for call in sleep_calls:
                self.fail(f"sleep_ms should not be called when over budget, got {call}")

        finally:
            stdlib_time.sleep_ms = original_sleep_ms
            stdlib_time.ticks_ms = original_ticks_ms


# ===================================================================
# Additional test: Agent._add_message basic behavior
# ===================================================================
class TestAgentBasics(unittest.TestCase):
    """Basic Agent functionality tests."""

    def test_reset_clears_messages(self):
        agent = Agent(api_key="k", model="m", system_prompt="s")
        agent._add_message("user", [{"type": "text", "text": "hi"}])
        self.assertEqual(len(agent.messages), 1)
        agent.reset()
        self.assertEqual(len(agent.messages), 0)

    def test_get_stats(self):
        agent = Agent(api_key="k", model="m", system_prompt="s")
        stats = agent.get_stats()
        self.assertEqual(stats["input_tokens"], 0)
        self.assertEqual(stats["output_tokens"], 0)
        self.assertEqual(stats["api_calls"], 0)
        self.assertEqual(stats["messages"], 0)

    def test_token_estimation(self):
        agent = Agent(api_key="k", model="m", system_prompt="a" * 400)
        agent._add_message("user", [{"type": "text", "text": "b" * 800}])
        estimate = agent._estimate_message_tokens()
        # system: 400/4=100, message text: 800/4=200, total ~300
        self.assertGreaterEqual(estimate, 200)
        self.assertLessEqual(estimate, 400)

    def test_tool_result_none_becomes_ok(self):
        """If a tool function returns None, result should be 'OK'."""
        registry = ToolRegistry()
        registry.register("noop", "Does nothing", no_params(), lambda p: None)
        result, is_error = registry.execute("noop", {})
        self.assertFalse(is_error)
        self.assertEqual(result, "OK")


if __name__ == "__main__":
    unittest.main()
