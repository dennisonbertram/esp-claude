# agent.py — Core AI agent for ESP32
#
# Implements the agent loop:
#   1. Send messages to Claude API
#   2. If tool calls in response → execute tools
#   3. Append results, repeat
#   4. No tool calls → done
#
# Designed for ESP32 memory constraints:
#   - No streaming (avoids SSE buffering)
#   - Message pruning (keeps last N messages)
#   - Explicit garbage collection
#   - Compact JSON building

import gc
import ujson
import time


class Agent:
    """Minimal AI agent that runs on ESP32.

    Usage:
        from lib.agent import Agent
        from lib.tools import ToolRegistry

        tools = ToolRegistry()
        # ... register tools ...

        agent = Agent(
            api_key="sk-ant-...",
            model="claude-haiku-3-5-20241022",
            system_prompt="You are a helpful assistant.",
            tools=tools,
        )

        response = agent.prompt("What is the temperature?")
        print(response)
    """

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    def __init__(self, api_key, model, system_prompt, tools=None,
                 max_tokens=1024, max_messages=12, api_timeout=30,
                 debug=False):
        """
        Args:
            api_key: Anthropic API key
            model: Model ID (e.g. "claude-haiku-3-5-20241022")
            system_prompt: System instructions for the agent
            tools: ToolRegistry instance (or None for no tools)
            max_tokens: Max response tokens per API call
            max_messages: Max conversation history length (older pruned)
            api_timeout: API request timeout in seconds (default 30)
            debug: Print debug info
        """
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools
        self.max_tokens = max_tokens
        self.max_messages = max_messages
        self.api_timeout = api_timeout
        self.debug = debug
        self.messages = []

        # Stats
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_api_calls = 0

    def prompt(self, text, max_turns=10):
        """Send a prompt and run the full agent loop.

        Args:
            text: User message text
            max_turns: Safety limit on tool-call loops

        Returns:
            Final text response from the agent, or None on error
        """
        # Add user message
        self._add_message("user", [{"type": "text", "text": text}])

        for turn in range(max_turns):
            if self.debug:
                print(f"\n[agent] Turn {turn + 1}/{max_turns}")

            # Call the API
            response = self._call_api()
            if response is None:
                return None

            # Extract content blocks
            content = response.get("content", [])
            stop_reason = response.get("stop_reason", "end_turn")

            # Track usage
            usage = response.get("usage", {})
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            self.total_api_calls += 1

            # Free response dict early
            response = None
            gc.collect()

            # Add assistant message
            self._add_message("assistant", content)

            # Extract text and tool calls
            text_parts = []
            tool_calls = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append(block)

            # Print assistant text
            if self.debug and text_parts:
                print(f"[agent] Response: {' '.join(text_parts)[:200]}")

            # No tool calls or no tools → done
            if not tool_calls or self.tools is None:
                return "\n".join(text_parts) if text_parts else ""

            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                tool_name = tc["name"]
                tool_input = tc.get("input", {})
                tool_id = tc["id"]

                if self.debug:
                    print(f"[agent] Tool call: {tool_name}({ujson.dumps(tool_input)[:100]})")

                result_text, is_error = self.tools.execute(tool_name, tool_input)

                if self.debug:
                    status = "ERROR" if is_error else "OK"
                    print(f"[agent] Tool result ({status}): {result_text[:200]}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_text,
                    "is_error": is_error,
                })

            # Add tool results as a user message (Anthropic API format)
            self._add_message("user", tool_results)

            # Free tool data
            tool_calls = None
            tool_results = None
            gc.collect()

            # If stop reason wasn't tool_use, we're done even with tool calls
            if stop_reason != "tool_use":
                return "\n".join(text_parts) if text_parts else ""

        if self.debug:
            print(f"[agent] Hit max turns ({max_turns})")
        return None

    def reset(self):
        """Clear conversation history."""
        self.messages = []
        gc.collect()

    def get_stats(self):
        """Get usage statistics."""
        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "api_calls": self.total_api_calls,
            "messages": len(self.messages),
        }

    # --- Internal ---

    def _add_message(self, role, content):
        """Add a message, pruning old ones if needed."""
        self.messages.append({"role": role, "content": content})

        # Prune old messages (keep system context fresh)
        if len(self.messages) > self.max_messages:
            # Always keep the first user message for context
            # Remove the oldest messages after that
            remove_count = len(self.messages) - self.max_messages
            cut = 1 + remove_count  # tentative start index for kept messages

            # Adjust cut point to avoid splitting tool_use/tool_result pairs.
            # Don't start kept messages with a tool_result (its tool_use would be pruned).
            # Don't end pruned messages with an assistant tool_use (its tool_result would be missing).
            while cut < len(self.messages):
                msg = self.messages[cut]
                # If the first kept message is a user tool_result, move cut forward
                if msg["role"] == "user" and isinstance(msg["content"], list) and msg["content"] and isinstance(msg["content"][0], dict) and msg["content"][0].get("type") == "tool_result":
                    cut += 1
                    continue
                # If the message just before cut is an assistant with tool_use, move cut forward
                prev = self.messages[cut - 1]
                if prev["role"] == "assistant" and isinstance(prev["content"], list):
                    has_tool_use = False
                    for block in prev["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            has_tool_use = True
                            break
                    if has_tool_use:
                        cut += 1
                        continue
                break

            self.messages = self.messages[:1] + self.messages[cut:]
            actual_removed = cut - 1
            if self.debug:
                print(f"[agent] Pruned {actual_removed} old messages, {len(self.messages)} remaining")
            gc.collect()

    def _call_api(self):
        """Make a single API call to Claude. Returns parsed response or None."""
        import urequests

        # Try to set default socket timeout for all new sockets
        # This ensures TLS/DNS/read won't hang indefinitely
        # Note: some MicroPython builds lack usocket.setdefaulttimeout
        _has_sock_timeout = False
        old_timeout = None
        try:
            import usocket
            old_timeout = usocket.getdefaulttimeout()
            usocket.setdefaulttimeout(self.api_timeout)
            _has_sock_timeout = True
        except Exception:
            try:
                import socket
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(self.api_timeout)
                _has_sock_timeout = True
            except Exception:
                if self.debug:
                    print("[agent] Warning: cannot set socket timeout")

        # Build request body
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system_prompt,
            "messages": self.messages,
        }

        # Add tools if registered
        if self.tools and self.tools.list_names():
            body["tools"] = self.tools.to_api_format()

        # Serialize
        json_body = ujson.dumps(body)
        body = None  # Free the dict
        gc.collect()

        if self.debug:
            print(f"[agent] API request: {len(json_body)} bytes, timeout={self.api_timeout}s")

        # Make request
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "content-type": "application/json",
        }

        try:
            start = time.ticks_ms()
            r = urequests.post(self.API_URL, data=json_body, headers=headers)
            elapsed = time.ticks_diff(time.ticks_ms(), start)

            # Free request body
            json_body = None
            gc.collect()

            status = r.status_code
            if self.debug:
                print(f"[agent] API response: HTTP {status} in {elapsed}ms")

            if status != 200:
                error_text = r.text[:300]
                r.close()
                print(f"[agent] API error {status}: {error_text}")
                return None

            # Parse response
            result = r.json()
            r.close()
            gc.collect()

            return result

        except Exception as e:
            print(f"[agent] API call failed: {e}")
            # Free any lingering references
            json_body = None
            gc.collect()
            return None

        finally:
            # Restore previous socket timeout
            if _has_sock_timeout:
                try:
                    import usocket
                    usocket.setdefaulttimeout(old_timeout)
                except Exception:
                    try:
                        import socket
                        socket.setdefaulttimeout(old_timeout)
                    except Exception:
                        pass

    def _estimate_message_tokens(self):
        """Rough token estimate for current messages (4 chars ≈ 1 token)."""
        total = len(self.system_prompt) // 4
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text", "") or block.get("content", "")
                        total += len(str(text)) // 4
        return total


class ScheduledAgent(Agent):
    """Agent that runs on a schedule with a recurring prompt.

    Usage:
        agent = ScheduledAgent(
            api_key="...",
            model="...",
            system_prompt="You are a thermostat...",
            tools=tools,
            interval_seconds=300,
            recurring_prompt="Check temperature and adjust heater.",
        )
        agent.run_forever()  # Blocks, runs every 5 minutes
    """

    def __init__(self, interval_seconds=300, recurring_prompt="Check status.",
                 on_response=None, on_error=None, **kwargs):
        """
        Args:
            interval_seconds: Seconds between each agent cycle
            recurring_prompt: The prompt to send each cycle
            on_response: Callback(response_text) after each cycle
            on_error: Callback(exception) on errors
            **kwargs: Passed to Agent.__init__
        """
        super().__init__(**kwargs)
        self.interval_seconds = interval_seconds
        self.recurring_prompt = recurring_prompt
        self.on_response = on_response
        self.on_error = on_error
        self.cycle_count = 0

    def run_forever(self):
        """Run the agent loop forever. Blocks."""
        if self.debug:
            print(f"[agent] Starting scheduled loop, interval={self.interval_seconds}s")
            print(f"[agent] Prompt: {self.recurring_prompt[:100]}")
            if self.tools:
                print(f"[agent] Tools: {', '.join(self.tools.list_names())}")

        while True:
            cycle_start = time.ticks_ms()
            self.cycle_count += 1
            if self.debug:
                print(f"\n{'='*40}")
                print(f"[agent] Cycle {self.cycle_count}")
                gc.collect()
                print(f"[agent] Free memory: {gc.mem_free()} bytes")

            try:
                response = self.prompt(self.recurring_prompt)

                if response and self.on_response:
                    self.on_response(response)
                elif self.debug and response:
                    print(f"[agent] Final: {response[:300]}")

            except Exception as e:
                print(f"[agent] Cycle error: {e}")
                if self.on_error:
                    self.on_error(e)

            # Reset conversation each cycle to save memory
            # (each cycle is independent)
            self.reset()
            gc.collect()

            # Subtract elapsed time to prevent timing drift
            elapsed_ms = time.ticks_diff(time.ticks_ms(), cycle_start)
            sleep_ms = (self.interval_seconds * 1000) - elapsed_ms
            if sleep_ms > 0:
                if self.debug:
                    print(f"[agent] Cycle took {elapsed_ms}ms, sleeping {sleep_ms}ms...")
                time.sleep_ms(sleep_ms)
            else:
                if self.debug:
                    print(f"[agent] Cycle took {elapsed_ms}ms (over budget, no sleep)")

    def run_once(self):
        """Run a single cycle. Returns response text."""
        self.cycle_count += 1
        try:
            response = self.prompt(self.recurring_prompt)
            self.reset()
            gc.collect()
            return response
        except Exception as e:
            print(f"[agent] Error: {e}")
            self.reset()
            gc.collect()
            return None


class EventDrivenAgent(Agent):
    """Agent that responds to events/triggers rather than a schedule.

    Usage:
        agent = EventDrivenAgent(
            api_key="...",
            model="...",
            system_prompt="You handle security alerts.",
            tools=tools,
        )

        # When something happens:
        response = agent.handle_event("motion_detected",
            "Motion sensor triggered on front door at 2:30 AM")
    """

    def __init__(self, reset_after_event=True, **kwargs):
        """
        Args:
            reset_after_event: Clear history after each event (saves memory)
            **kwargs: Passed to Agent.__init__
        """
        super().__init__(**kwargs)
        self.reset_after_event = reset_after_event
        self.event_count = 0

    def handle_event(self, event_type, event_data):
        """Handle an event by prompting the agent.

        Args:
            event_type: Type of event (e.g., "motion_detected", "temp_high")
            event_data: Description or data about the event

        Returns:
            Agent response text, or None on error
        """
        self.event_count += 1
        prompt = f"[EVENT: {event_type}] {event_data}"

        if self.debug:
            print(f"\n[agent] Event #{self.event_count}: {event_type}")

        try:
            response = self.prompt(prompt)

            if self.reset_after_event:
                self.reset()
                gc.collect()

            return response

        except Exception as e:
            print(f"[agent] Event handling error: {e}")
            if self.reset_after_event:
                self.reset()
            gc.collect()
            return None
