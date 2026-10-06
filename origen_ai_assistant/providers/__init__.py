import frappe

from origen_ai_assistant.providers.base import AIProvider


def get_provider(settings) -> AIProvider:
	provider = (settings.provider or "Claude").lower()
	if provider == "claude":
		from origen_ai_assistant.providers.claude import ClaudeProvider

		return ClaudeProvider(settings)
	if provider == "gemini":
		from origen_ai_assistant.providers.gemini import GeminiProvider

		return GeminiProvider(settings)
	if provider == "openai":
		from origen_ai_assistant.providers.openai import OpenAIProvider

		return OpenAIProvider(settings)
	frappe.throw(frappe._("Unknown AI provider: {0}").format(settings.provider))
