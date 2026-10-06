"""Google Gemini adapter, built to the same AIProvider interface as providers/claude.py.

Live-verified end-to-end on vedica3 with a real API key and real data (not just mocked): correct
exact counts, correct schema lookups via describe_doctype, correct grouped aggregation summing to
the true total. Two real, Gemini-specific issues were found and fixed during that verification, not
assumed away:
- Gemini 3 requires each function-call part's `thought_signature` to be echoed back unchanged on
  replay (a hard 400 otherwise, not just a quality note) — carried through `ToolCall.raw`.
- The model that was live-tested is on Google's free tier, whose per-minute token quota (250k) is
  easily exhausted by an oversized system prompt — see api.py's `_build_system_prompt` docstring for
  the actual fix (a lightweight catalog instead of a full per-doctype field dump), which mattered
  for every provider, not just this one.
"""

from google import genai
from google.genai import types

from origen_ai_assistant.providers.base import AIProvider, ProviderResponse, ToolCall


class GeminiProvider(AIProvider):
	def __init__(self, settings):
		super().__init__(settings)
		api_key = settings.get_password("gemini_api_key")
		self.client = genai.Client(api_key=api_key)
		self.model = settings.gemini_model or "gemini-2.0-flash"

	def _to_gemini_tools(self, tools: list):
		declarations = [
			types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["input_schema"])
			for t in tools
		]
		return [types.Tool(function_declarations=declarations)]

	def _to_gemini_contents(self, messages: list):
		contents = []
		for m in messages:
			role = "model" if m["role"] == "assistant" else "user"
			if isinstance(m["content"], str):
				contents.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
				continue
			parts = []
			for block in m["content"]:
				if block.get("type") == "text":
					parts.append(types.Part(text=block["text"]))
				elif block.get("type") == "tool_use":
					# Gemini 3 requires the original thought_signature to be echoed back unchanged
					# on replay (confirmed live: omitting it is a hard 400, not just a quality
					# degradation) — carried through ToolCall.raw since our canonical message dict
					# is otherwise provider-agnostic.
					parts.append(
						types.Part(
							function_call=types.FunctionCall(name=block["name"], args=block["input"]),
							thought_signature=block.get("thought_signature"),
						)
					)
				elif block.get("type") == "tool_result":
					parts.append(
						types.Part(
							function_response=types.FunctionResponse(name=block.get("name", ""), response={"result": block["content"]})
						)
					)
			contents.append(types.Content(role=role, parts=parts))
		return contents

	def complete(self, system: str, messages: list, tools: list, max_tokens: int) -> ProviderResponse:
		response = self.client.models.generate_content(
			model=self.model,
			contents=self._to_gemini_contents(messages),
			config=types.GenerateContentConfig(
				system_instruction=system,
				tools=self._to_gemini_tools(tools),
				max_output_tokens=max_tokens,
			),
		)

		text_parts = []
		tool_calls = []
		for part in response.candidates[0].content.parts:
			if getattr(part, "text", None):
				text_parts.append(part.text)
			if getattr(part, "function_call", None):
				fc = part.function_call
				tool_calls.append(
					ToolCall(id=fc.name, name=fc.name, input=dict(fc.args or {}), raw=getattr(part, "thought_signature", None))
				)

		return ProviderResponse(
			text="\n".join(text_parts).strip(),
			tool_calls=tool_calls,
			stop_reason="tool_use" if tool_calls else "end_turn",
		)

	def assistant_tool_use_message(self, response: ProviderResponse) -> dict:
		content = []
		if response.text:
			content.append({"type": "text", "text": response.text})
		for call in response.tool_calls:
			content.append(
				{"type": "tool_use", "id": call.id, "name": call.name, "input": call.input, "thought_signature": call.raw}
			)
		return {"role": "assistant", "content": content}

	def tool_result_message(self, tool_call: ToolCall, result) -> dict:
		import frappe

		return {"type": "tool_result", "tool_use_id": tool_call.id, "name": tool_call.name, "content": frappe.as_json(result)}
