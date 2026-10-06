"""Install/uninstall. Additive-only on install (creates only this app's own Roles/Page/Settings
singleton — never edits anything that already existed); uninstall removes only what this app itself
created, enumerated explicitly rather than guessed, per the project's isolation requirement.
"""

import frappe

ROLES = ["AI Assistant User", "AI Assistant Knowledge Manager", "AI Assistant Auditor"]


def after_install():
	for role_name in ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(ignore_permissions=True)

	# The "ai-assistant" Page itself ships as a standard fixture
	# (page/ai_assistant/ai_assistant.json, including its role restriction) and is
	# created/updated by `bench migrate` the same way a DocType is — no code needed here.

	if not frappe.db.exists("AI Assistant Settings", "AI Assistant Settings"):
		frappe.get_doc(
			{
				"doctype": "AI Assistant Settings",
				"enabled": 0,
				"provider": "Claude",
				"discovery_status": "Not Started",
				"knowledge_refresh_frequency": "Daily",
				"max_tool_iterations": 8,
				"max_rows_per_query": 100,
				"max_response_tokens": 2000,
				"turn_timeout_seconds": 30,
				"max_concurrent_requests_per_user": 1,
				"max_concurrent_requests_site": 5,
				"daily_request_budget": 200,
				"chat_retention_days": 90,
				"audit_retention_days": 180,
				"knowledge_version_retention_count": 10,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()


def before_uninstall():
	"""Removes only the Role-assignment rows and Roles this app created — any Role/assignment that
	already existed before install is left exactly as it was.
	"""
	for role_name in ROLES:
		for row in frappe.get_all("Has Role", filters={"role": role_name}, fields=["name"]):
			frappe.delete_doc("Has Role", row.name, ignore_permissions=True, force=True)
		if frappe.db.exists("Role", role_name):
			frappe.delete_doc("Role", role_name, ignore_permissions=True, force=True)

	if frappe.db.exists("Page", "ai-assistant"):
		frappe.delete_doc("Page", "ai-assistant", ignore_permissions=True, force=True)

	frappe.db.commit()
