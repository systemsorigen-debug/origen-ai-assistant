"""Provider-agnostic interface. The chat controller (api.py) talks only to this shape — it never
imports an SDK directly or branches on which provider is active. Adding a fourth provider later means
writing one more module that implements AIProvider; nothing in api.py changes.
"""

from dataclasses import dataclass, field


@dataclass
class ToolCall:
	id: str
	name: str
	input: dict
	raw: object = None  # provider-specific metadata that must round-trip unchanged on replay
	# (e.g. Gemini 3's thought_signature) — opaque to the chat controller, used only by the
	# provider's own assistant_tool_use_message/tool_result_message implementations.


@dataclass
class ProviderResponse:
	text: str
	tool_calls: list[ToolCall] = field(default_factory=list)
	stop_reason: str = "end_turn"  # "end_turn" | "tool_use"


class AIProvider:
	"""Implementations: providers/claude.py, providers/gemini.py, providers/openai.py."""

	def __init__(self, settings):
		self.settings = settings

	def complete(self, system: str, messages: list, tools: list, max_tokens: int) -> ProviderResponse:
		"""messages: list of {"role": "user"|"assistant", "content": str | list-of-blocks}.
		tools: the shared TOOL_DEFINITIONS shape from tools.py (name/description/input_schema).
		Must raise on a real provider/network error — api.py is responsible for turning that into a
		clean user-facing message, this layer should not swallow it silently.
		"""
		raise NotImplementedError

	def tool_result_message(self, tool_call: ToolCall, result) -> dict:
		"""Build the provider-specific message representing a tool's result, to append before the
		next complete() call in the tool-use loop."""
		raise NotImplementedError

	def assistant_tool_use_message(self, response: ProviderResponse) -> dict:
		"""Build the provider-specific assistant-turn message (the one containing the tool_use
		request(s)) to append to history before the tool_result_message(s)."""
		raise NotImplementedError
