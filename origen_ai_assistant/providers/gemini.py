"""Google Gemini adapter. Implemented against the public google-genai function-calling shape and
built to the same AIProvider interface as providers/claude.py — but, per this build's explicit scope,
this adapter has only been exercised against a mocked AIProvider response, never a real Gemini call.
Treat it as implemented, not verified, until a real API key is supplied and Phase 3's live check runs.
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
					parts.append(types.Part(function_call=types.FunctionCall(name=block["name"], args=block["input"])))
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
				tool_calls.append(ToolCall(id=fc.name, name=fc.name, input=dict(fc.args or {})))

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
			content.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.input})
		return {"role": "assistant", "content": content}

	def tool_result_message(self, tool_call: ToolCall, result) -> dict:
		import frappe

		return {"type": "tool_result", "tool_use_id": tool_call.id, "name": tool_call.name, "content": frappe.as_json(result)}
