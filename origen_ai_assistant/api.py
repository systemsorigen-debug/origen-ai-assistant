"""The chat controller. Every whitelisted method here re-checks access server-side regardless of
what the client's boot flag says — that flag is a UI convenience, this is the real boundary. No
method here ever calls anything with ignore_permissions=True except the handful of controlled writes
explicitly called out below (chat-message/audit-log inserts) — every data *lookup* a tool performs
goes through permissions.py as the real caller, never Administrator.
"""

import time

import frappe

from origen_ai_assistant import knowledge, tools
from origen_ai_assistant.providers import get_provider

ALLOWED_ROLES = {"AI Assistant User", "System Manager"}
KNOWLEDGE_MANAGER_ROLES = {"AI Assistant Knowledge Manager", "System Manager"}
AUDITOR_ROLES = {"AI Assistant Auditor", "System Manager"}


def _check_access(settings):
	if not settings.enabled:
		frappe.throw(frappe._("The AI Assistant is currently disabled."), frappe.PermissionError)
	if not ALLOWED_ROLES & set(frappe.get_roles()):
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)


def _check_session_ownership(session: str):
	existing = frappe.get_all("AI Assistant Chat Message", filters={"session": session}, fields=["user"], limit=1, order_by="creation asc")
	if existing and existing[0].user != frappe.session.user:
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)


def _check_rate_limit(settings):
	cache = frappe.cache()
	cache_key = f"ai_assistant_last_send:{frappe.session.user}"
	last = cache.get_value(cache_key)
	if last and (time.time() - float(last)) < 3:
		frappe.throw(frappe._("You're sending messages too quickly. Please wait a moment."))
	cache.set_value(cache_key, str(time.time()), expires_in_sec=60)

	budget_key = f"ai_assistant_daily_count:{frappe.utils.nowdate()}"
	count = cache.get_value(budget_key) or 0
	if int(count) >= (settings.daily_request_budget or 200):
		frappe.throw(frappe._("The site's daily AI Assistant request budget has been reached. Try again tomorrow."))
	cache.set_value(budget_key, int(count) + 1, expires_in_sec=86400)


def _compute_authorization_fingerprint(user: str) -> str:
	roles = sorted(frappe.get_roles(user))
	user_perms = frappe.get_all("User Permission", filters={"user": user}, fields=["allow", "for_value"])
	user_perms_key = sorted(f"{p.allow}:{p.for_value}" for p in user_perms)
	settings = frappe.get_cached_doc("AI Assistant Settings")
	return frappe.as_json({"roles": roles, "user_perms": user_perms_key, "knowledge_version": settings.knowledge_version_active})


def _load_history_for_replay(session: str, current_fingerprint: str) -> list:
	"""The simpler, safer mechanism the project's review called for: the moment any authorization
	mismatch is found anywhere in this session's history, replay stops entirely and the model starts
	fresh for this turn. The user's own view of their chat history in the UI is unaffected — this
	only controls what the *model* sees again.
	"""
	rows = frappe.get_all(
		"AI Assistant Chat Message",
		filters={"session": session, "user": frappe.session.user},
		fields=["role", "content", "authorization_fingerprint"],
		order_by="creation asc",
	)
	for row in rows:
		if row.role == "assistant" and row.authorization_fingerprint and row.authorization_fingerprint != current_fingerprint:
			return []
	return [{"role": r.role, "content": r.content} for r in rows]


def _create_message(session: str, role: str, content: str, authorization_fingerprint: str = None):
	frappe.get_doc(
		{
			"doctype": "AI Assistant Chat Message",
			"session": session,
			"user": frappe.session.user,
			"role": role,
			"content": content,
			"authorization_fingerprint": authorization_fingerprint or "",
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()


def _build_system_prompt(user: str) -> str:
	context = knowledge.get_context_for_user(user)
	parts = [
		"You are a read-only analytics assistant inside Frappe Desk. You can only read data the "
		"current user is permitted to see — you have no tool that can create, update, delete, "
		"submit, or cancel any record, and no tool that runs arbitrary SQL. If asked to perform a "
		"write, explain that you cannot, and if useful, describe the exact steps or SQL a human with "
		"the right access could run themselves — never claim to have run it.",
		"When reporting a grouped/aggregate result, only state a total that came from the "
		"aggregate/time_trend/get_count tools. If a result is marked truncated, say so explicitly "
		"(e.g. 'top 50 of N groups, covering X of the true total Y') — never present a capped result "
		"as the complete picture. If a time_trend result has sample_only=true, say the per-period "
		"breakdown is from a bounded sample and may not be exact, even though grand_total is exact.",
		f"Known site schema (permission-filtered for this user): {frappe.as_json(context['doctypes'])}",
	]
	if context["notes"]:
		parts.append(f"Business knowledge notes relevant to what you can see: {frappe.as_json(context['notes'])}")
	if context.get("incomplete"):
		parts.append("Site knowledge discovery has not completed yet — say so plainly if asked an analytical question.")
	return "\n\n".join(parts)


def _run_tool_loop(provider, system: str, messages: list, user: str, session: str, settings) -> str:
	start = time.time()
	max_iterations = settings.max_tool_iterations or 8
	timeout = settings.turn_timeout_seconds or 30
	max_tokens = settings.max_response_tokens or 2000

	for _ in range(max_iterations):
		if time.time() - start > timeout:
			return "This request took too long and was stopped. Try a narrower question."

		response = provider.complete(system, messages, tools.TOOL_DEFINITIONS, max_tokens)

		if response.stop_reason != "tool_use" or not response.tool_calls:
			return response.text or "I don't have a response for that."

		messages.append(provider.assistant_tool_use_message(response))

		tool_result_blocks = []
		for call in response.tool_calls:
			result = tools.dispatch_tool(call.name, user, session, call.input)
			tool_result_blocks.append(provider.tool_result_message(call, result))
		messages.append({"role": "user", "content": tool_result_blocks})

	return "I made too many tool calls trying to answer this — try breaking the question into smaller parts."


@frappe.whitelist()
def send_message(session: str, message: str):
	settings = frappe.get_single("AI Assistant Settings")
	_check_access(settings)
	_check_rate_limit(settings)
	_check_session_ownership(session)

	context = knowledge.get_context_for_user(frappe.session.user)
	if context.get("incomplete"):
		return {"reply": "Site knowledge discovery hasn't completed yet, so I can't answer reliably. Ask an AI Assistant Knowledge Manager to run discovery first.", "incomplete": True}

	current_fp = _compute_authorization_fingerprint(frappe.session.user)
	history = _load_history_for_replay(session, current_fp)

	_create_message(session, "user", message)

	system = _build_system_prompt(frappe.session.user)
	provider = get_provider(settings)
	conversation = history + [{"role": "user", "content": message}]

	reply_text = _run_tool_loop(provider, system, conversation, frappe.session.user, session, settings)

	_create_message(session, "assistant", reply_text, authorization_fingerprint=current_fp)
	return {"reply": reply_text}


@frappe.whitelist()
def get_chat_history(session: str):
	if not (AUDITOR_ROLES & set(frappe.get_roles())):
		_check_session_ownership(session)
	return frappe.get_all(
		"AI Assistant Chat Message",
		filters={"session": session, "user": frappe.session.user} if not (AUDITOR_ROLES & set(frappe.get_roles())) else {"session": session},
		fields=["role", "content", "creation"],
		order_by="creation asc",
	)


@frappe.whitelist()
def trigger_discovery():
	if not (KNOWLEDGE_MANAGER_ROLES & set(frappe.get_roles())):
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)
	from origen_ai_assistant.discovery import run_discovery_job

	frappe.enqueue(run_discovery_job, queue="long", job_name="ai_assistant_manual_discovery")
	return {"queued": True}


@frappe.whitelist()
def refresh_knowledge_file():
	if not (KNOWLEDGE_MANAGER_ROLES & set(frappe.get_roles())):
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)
	knowledge.write_knowledge_file()
	return {"done": True}
