"""extend_bootinfo — one key, UI convenience only. Wrapped in try/except because this hook runs on
every single Desk page load for every user on the site, including people with no interest in this
app; a provider/settings failure here must never break anyone's login or Desk boot. The server-side
re-check on every whitelisted method (api.py) is the real boundary, not this flag.
"""

import frappe


def extend_bootinfo(bootinfo):
	try:
		settings = frappe.get_cached_doc("AI Assistant Settings")
		roles = set(frappe.get_roles())
		bootinfo["ai_assistant_access"] = bool(settings.enabled) and bool({"AI Assistant User", "System Manager"} & roles)
	except Exception:
		bootinfo["ai_assistant_access"] = False
