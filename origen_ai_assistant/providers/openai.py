"""OpenAI adapter. Implemented against the public Chat Completions function-calling shape and built
to the same AIProvider interface as providers/claude.py — but, per this build's explicit scope, this
adapter has only been exercised against a mocked AIProvider response, never a real OpenAI call. Treat
it as implemented, not verified, until a real API key is supplied and Phase 3's live check runs.
"""

import json

import openai

from origen_ai_assistant.providers.base import AIProvider, ProviderResponse, ToolCall


class OpenAIProvider(AIProvider):
	def __init__(self, settings):
		super().__init__(settings)
		api_key = settings.get_password("openai_api_key")
		self.client = openai.OpenAI(api_key=api_key)
		self.model = settings.openai_model or "gpt-4.1"

	def _to_openai_tools(self, tools: list):
		return [
			{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
			for t in tools
		]

	def _to_openai_messages(self, system: str, messages: list):
		out = [{"role": "system", "content": system}]
		for m in messages:
			if isinstance(m["content"], str):
				out.append({"role": m["role"], "content": m["content"]})
				continue
			if m["role"] == "assistant":
				text = next((b["text"] for b in m["content"] if b.get("type") == "text"), None)
				tool_calls = [
					{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
					for b in m["content"]
					if b.get("type") == "tool_use"
				]
				entry = {"role": "assistant", "content": text}
				if tool_calls:
					entry["tool_calls"] = tool_calls
				out.append(entry)
			else:
				for b in m["content"]:
					if b.get("type") == "tool_result":
						out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
					elif b.get("type") == "text":
						out.append({"role": "user", "content": b["text"]})
		return out

	def complete(self, system: str, messages: list, tools: list, max_tokens: int) -> ProviderResponse:
		response = self.client.chat.completions.create(
			model=self.model,
			max_tokens=max_tokens,
			messages=self._to_openai_messages(system, messages),
			tools=self._to_openai_tools(tools),
		)
		choice = response.choices[0]
		msg = choice.message

		tool_calls = [
			ToolCall(id=tc.id, name=tc.function.name, input=json.loads(tc.function.arguments or "{}"))
			for tc in (msg.tool_calls or [])
		]

		return ProviderResponse(
			text=(msg.content or "").strip(),
			tool_calls=tool_calls,
			stop_reason="tool_use" if tool_calls else "end_turn",
		)

	def assistant_tool_use_message(self, response: ProviderResponse) -> dict:
		content = []
		if response.text:
			content.append({"type": "text", "text": response.text})
		for call in response.tool_calls:
			content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
		return {"role": "assistant", "content": content}

	def tool_result_message(self, tool_call: ToolCall, result) -> dict:
		import frappe

		return {"type": "tool_result", "tool_use_id": tool_call.id, "content": frappe.as_json(result)}
