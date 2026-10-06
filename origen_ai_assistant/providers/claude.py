"""Anthropic/Claude adapter — the only provider live-tested in this build. Implements the manual
agentic-loop shape (see Anthropic's own docs): one complete() call is one request to
POST /v1/messages; api.py owns the loop across multiple complete() calls, so it can audit-log and
permission-check each tool call itself rather than letting an SDK-side runner execute tools for it.
"""

import anthropic

from origen_ai_assistant.providers.base import AIProvider, ProviderResponse, ToolCall

KNOWN_MODELS = {"claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"}


class ClaudeProvider(AIProvider):
	def __init__(self, settings):
		super().__init__(settings)
		api_key = settings.get_password("claude_api_key")
		self.client = anthropic.Anthropic(api_key=api_key)
		self.model = settings.claude_model or "claude-opus-5"

	def complete(self, system: str, messages: list, tools: list, max_tokens: int) -> ProviderResponse:
		response = self.client.messages.create(
			model=self.model,
			max_tokens=max_tokens,
			system=system,
			messages=messages,
			tools=[{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools],
		)

		text_parts = [b.text for b in response.content if b.type == "text"]
		tool_calls = [ToolCall(id=b.id, name=b.name, input=b.input) for b in response.content if b.type == "tool_use"]

		return ProviderResponse(
			text="\n".join(text_parts).strip(),
			tool_calls=tool_calls,
			stop_reason="tool_use" if response.stop_reason == "tool_use" else "end_turn",
		)

	def assistant_tool_use_message(self, response: ProviderResponse) -> dict:
		# Reconstructed from our own ProviderResponse rather than the raw SDK object, so this stays
		# provider-agnostic at the api.py boundary — every provider's history ends up in the same
		# plain-dict shape, which is also what api.py uses for the authorization-fingerprint check.
		content = []
		if response.text:
			content.append({"type": "text", "text": response.text})
		for call in response.tool_calls:
			content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
		return {"role": "assistant", "content": content}

	def tool_result_message(self, tool_call: ToolCall, result) -> dict:
		import frappe

		return {"type": "tool_result", "tool_use_id": tool_call.id, "content": frappe.as_json(result)}
