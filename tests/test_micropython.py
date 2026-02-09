# test_micropython.py — Test non-hardware code paths under MicroPython unix port
#
# Stubs out hardware-only modules, then exercises ToolRegistry and Agent
# logic without making real API calls.

import sys
import os

# ---- Stub out hardware/network modules that don't exist on unix port ----

class _StubModule:
    """Minimal stub that returns itself for any attribute access."""
    def __getattr__(self, name):
        return _StubModule()
    def __call__(self, *args, **kwargs):
        return _StubModule()

for mod_name in ("machine", "dht", "neopixel", "urequests", "usocket", "network"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = _StubModule()

# ---- Helpers ----

_pass = 0
_fail = 0

def check(label, condition, detail=""):
    global _pass, _fail
    if condition:
        _pass += 1
        print("  PASS: " + label)
    else:
        _fail += 1
        msg = "  FAIL: " + label
        if detail:
            msg += " — " + str(detail)
        print(msg)

# ---- Adjust sys.path so `lib` package is importable ----

project_root = os.getcwd()
# If run from tests/ directory, go up one level
if project_root.endswith("/tests"):
    project_root = project_root.rsplit("/tests", 1)[0]
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# ===========================================================================
# TEST 1: Import lib.tools
# ===========================================================================
print("\n=== Test 1: Import lib.tools ===")
try:
    from lib.tools import ToolRegistry, ToolError, make_params, no_params
    check("import lib.tools", True)
except Exception as e:
    check("import lib.tools", False, str(e))
    # Can't continue if import fails
    sys.exit(1)

# ===========================================================================
# TEST 2: ToolRegistry basics
# ===========================================================================
print("\n=== Test 2: ToolRegistry basics ===")

registry = ToolRegistry()
check("create ToolRegistry", registry is not None)
check("list_names() empty", registry.list_names() == [])
check("to_api_format() empty", registry.to_api_format() == [])

# Register a simple tool
def my_tool(params):
    return "hello " + params.get("name", "world")

registry.register(
    "greet",
    "Say hello to someone",
    make_params({"name": {"type": "string", "description": "Name to greet"}}, required=["name"]),
    my_tool,
)

check("list_names() has greet", registry.list_names() == ["greet"])
check("get() returns tool", registry.get("greet") is not None)
check("get() unknown returns None", registry.get("nonexistent") is None)

# ===========================================================================
# TEST 3: to_api_format()
# ===========================================================================
print("\n=== Test 3: to_api_format() ===")

api_fmt = registry.to_api_format()
check("to_api_format() returns list", isinstance(api_fmt, list))
check("to_api_format() has 1 tool", len(api_fmt) == 1)

tool_def = api_fmt[0]
check("tool name correct", tool_def["name"] == "greet")
check("tool has description", tool_def["description"] == "Say hello to someone")
check("tool has input_schema", "input_schema" in tool_def)
check("input_schema type is object", tool_def["input_schema"]["type"] == "object")
check("input_schema has properties", "name" in tool_def["input_schema"]["properties"])
check("input_schema has required", tool_def["input_schema"]["required"] == ["name"])

# Verify caching: second call should return same object
api_fmt2 = registry.to_api_format()
check("to_api_format() caching works", api_fmt is api_fmt2)

# ===========================================================================
# TEST 4: Tool execution
# ===========================================================================
print("\n=== Test 4: Tool execution ===")

result, is_error = registry.execute("greet", {"name": "MicroPython"})
check("execute returns result", result == "hello MicroPython")
check("execute not an error", is_error == False)

result2, is_error2 = registry.execute("nonexistent", {})
check("unknown tool returns error", is_error2 == True)
check("unknown tool error message", "Unknown tool" in result2)

# ===========================================================================
# TEST 5: ToolError handling
# ===========================================================================
print("\n=== Test 5: ToolError handling ===")

def failing_tool(params):
    raise ToolError("sensor disconnected")

registry.register("broken", "A tool that fails", no_params(), failing_tool)

result3, is_error3 = registry.execute("broken", {})
check("ToolError caught", is_error3 == True)
check("ToolError message preserved", result3 == "sensor disconnected")

# General exception
def crashing_tool(params):
    raise ValueError("unexpected")

registry.register("crasher", "A tool that crashes", no_params(), crashing_tool)

result4, is_error4 = registry.execute("crasher", {})
check("general exception caught", is_error4 == True)
check("general exception message", "Error executing crasher" in result4)

# ===========================================================================
# TEST 6: no_params() and make_params() helpers
# ===========================================================================
print("\n=== Test 6: Schema helpers ===")

np = no_params()
check("no_params() type", np["type"] == "object")
check("no_params() empty properties", np["properties"] == {})

mp = make_params(
    {"x": {"type": "integer"}, "y": {"type": "integer"}},
    required=["x"],
)
check("make_params() type", mp["type"] == "object")
check("make_params() properties", "x" in mp["properties"] and "y" in mp["properties"])
check("make_params() required", mp["required"] == ["x"])

mp_no_req = make_params({"a": {"type": "string"}})
check("make_params() no required", "required" not in mp_no_req)

# ===========================================================================
# TEST 7: Import lib.agent
# ===========================================================================
print("\n=== Test 7: Import lib.agent ===")
try:
    from lib.agent import Agent, ScheduledAgent, EventDrivenAgent
    check("import lib.agent", True)
except Exception as e:
    check("import lib.agent", False, str(e))
    # Print summary and exit
    print("\n" + "=" * 50)
    print("Results: {} passed, {} failed".format(_pass, _fail))
    sys.exit(1 if _fail > 0 else 0)

# ===========================================================================
# TEST 8: Agent construction and message management
# ===========================================================================
print("\n=== Test 8: Agent construction and messages ===")

agent = Agent(
    api_key="sk-test-fake",
    model="claude-haiku-3-5-20241022",
    system_prompt="You are a test agent.",
    tools=registry,
    max_tokens=512,
    max_messages=6,
    debug=False,
)
check("Agent created", agent is not None)
check("messages starts empty", agent.messages == [])
check("stats zeroed", agent.get_stats() == {
    "input_tokens": 0, "output_tokens": 0, "api_calls": 0, "messages": 0,
})

# Test _add_message
agent._add_message("user", [{"type": "text", "text": "hello"}])
check("_add_message adds message", len(agent.messages) == 1)
check("message role correct", agent.messages[0]["role"] == "user")

agent._add_message("assistant", [{"type": "text", "text": "hi there"}])
check("two messages", len(agent.messages) == 2)

# Test reset
agent.reset()
check("reset clears messages", agent.messages == [])

# ===========================================================================
# TEST 9: Message pruning
# ===========================================================================
print("\n=== Test 9: Message pruning ===")

agent2 = Agent(
    api_key="sk-test-fake",
    model="test",
    system_prompt="test",
    max_messages=4,
    debug=False,
)

# Add messages to trigger pruning
for i in range(6):
    role = "user" if i % 2 == 0 else "assistant"
    agent2._add_message(role, [{"type": "text", "text": "msg {}".format(i)}])

check("pruning keeps max_messages", len(agent2.messages) <= 4 + 1)  # +1 for first message retention
check("first message preserved", agent2.messages[0]["content"][0]["text"] == "msg 0")

# ===========================================================================
# TEST 10: Token estimation
# ===========================================================================
print("\n=== Test 10: Token estimation ===")

agent3 = Agent(
    api_key="sk-test-fake",
    model="test",
    system_prompt="A" * 100,  # ~25 tokens
    debug=False,
)
agent3._add_message("user", [{"type": "text", "text": "B" * 200}])  # ~50 tokens

estimate = agent3._estimate_message_tokens()
check("token estimate > 0", estimate > 0)
check("token estimate reasonable", 50 <= estimate <= 100, "got {}".format(estimate))

# ===========================================================================
# TEST 11: ScheduledAgent construction
# ===========================================================================
print("\n=== Test 11: ScheduledAgent ===")

sched = ScheduledAgent(
    api_key="sk-test-fake",
    model="test",
    system_prompt="test",
    interval_seconds=60,
    recurring_prompt="Check sensors.",
    debug=False,
)
check("ScheduledAgent created", sched is not None)
check("interval_seconds set", sched.interval_seconds == 60)
check("recurring_prompt set", sched.recurring_prompt == "Check sensors.")
check("cycle_count starts at 0", sched.cycle_count == 0)

# ===========================================================================
# TEST 12: EventDrivenAgent construction
# ===========================================================================
print("\n=== Test 12: EventDrivenAgent ===")

event_agent = EventDrivenAgent(
    api_key="sk-test-fake",
    model="test",
    system_prompt="test",
    reset_after_event=True,
    debug=False,
)
check("EventDrivenAgent created", event_agent is not None)
check("reset_after_event set", event_agent.reset_after_event == True)
check("event_count starts at 0", event_agent.event_count == 0)

# ===========================================================================
# TEST 13: Multiple tools and cache invalidation
# ===========================================================================
print("\n=== Test 13: Multiple tools and cache invalidation ===")

reg2 = ToolRegistry()
reg2.register("a", "Tool A", no_params(), lambda p: "A")
api1 = reg2.to_api_format()
check("1 tool in api format", len(api1) == 1)

reg2.register("b", "Tool B", no_params(), lambda p: "B")
api2 = reg2.to_api_format()
check("cache invalidated on register", api1 is not api2)
check("2 tools in api format", len(api2) == 2)

# ===========================================================================
# TEST 14: Tool returning None becomes "OK"
# ===========================================================================
print("\n=== Test 14: Tool returning None ===")

reg2.register("silent", "Returns nothing", no_params(), lambda p: None)
result5, is_error5 = reg2.execute("silent", {})
check("None result becomes 'OK'", result5 == "OK")
check("None result not an error", is_error5 == False)

# ===========================================================================
# Summary
# ===========================================================================
print("\n" + "=" * 50)
print("Results: {} passed, {} failed".format(_pass, _fail))
if _fail > 0:
    sys.exit(1)
else:
    print("All tests passed!")
    sys.exit(0)
