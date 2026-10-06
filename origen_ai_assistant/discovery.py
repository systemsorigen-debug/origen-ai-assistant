"""Site discovery: inventories the host site's own schema and automation purely from Frappe's meta
APIs — no DocType/field/role name is ever hardcoded here, which is what lets this app install on any
site with zero assumption about what that site contains. Produces a draft AI Knowledge Version;
knowledge.py is responsible for validating and activating it.
"""

import frappe

from origen_ai_assistant.permissions import HARDCODED_BLOCKLIST

LARGE_DOCTYPE_FIELD_THRESHOLD = 30
LARGE_DOCTYPE_SUMMARY_FIELD_COUNT = 15


def _doctype_automation(doctype: str) -> dict:
	server_scripts = frappe.get_all(
		"Server Script",
		filters={"reference_doctype": doctype},
		fields=["name", "script_type", "doctype_event", "disabled"],
	)
	client_scripts = frappe.get_all(
		"Client Script", filters={"dt": doctype}, fields=["name", "view", "enabled"]
	)
	workflow = frappe.get_all("Workflow", filters={"document_type": doctype}, fields=["name", "is_active"])
	return {
		"server_scripts": server_scripts,
		"client_scripts": client_scripts,
		"workflows": workflow,
	}


def _permission_matrix(meta) -> list:
	return [
		{
			"role": p.role,
			"read": bool(p.read),
			"write": bool(p.write),
			"create": bool(p.create),
			"delete": bool(p.delete),
			"permlevel": p.permlevel or 0,
		}
		for p in meta.permissions
	]


def _field_entry(df) -> dict:
	return {
		"fieldname": df.fieldname,
		"label": df.label,
		"fieldtype": df.fieldtype,
		"options": df.options,
		"reqd": bool(df.reqd),
		"permlevel": df.permlevel or 0,
		"in_list_view": bool(df.in_list_view),
	}


def discover_doctype(doctype_name: str) -> dict:
	meta = frappe.get_meta(doctype_name)
	fields = [_field_entry(df) for df in meta.fields if df.fieldtype != "Password"]
	entry = {
		"doctype": doctype_name,
		"module": meta.module,
		"is_custom": bool(meta.custom),
		"autoname": meta.autoname,
		"field_count": len(fields),
		"permissions": _permission_matrix(meta),
		"automation": _doctype_automation(doctype_name),
	}
	if len(fields) > LARGE_DOCTYPE_FIELD_THRESHOLD:
		key_fields = [f for f in fields if f["fieldname"] in (meta.title_field, meta.image_field) or f.get("in_list_view")]
		entry["fields_summary"] = (key_fields or fields)[:LARGE_DOCTYPE_SUMMARY_FIELD_COUNT]
		entry["fields_truncated_in_summary"] = True
	else:
		entry["fields"] = fields
	return entry


def run_discovery_job():
	"""Enqueued, not called synchronously from a request — 300+ DocTypes is real work."""
	settings = frappe.get_single("AI Assistant Settings")
	settings.db_set("discovery_status", "Running", update_modified=False)
	frappe.db.commit()

	try:
		installed_apps = frappe.get_installed_apps()
		doctypes = frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, fields=["name"])
		content = {
			"installed_apps": installed_apps,
			"doctypes": {},
		}
		for row in doctypes:
			if row.name in HARDCODED_BLOCKLIST:
				continue
			try:
				content["doctypes"][row.name] = discover_doctype(row.name)
			except Exception:
				frappe.log_error(title=f"AI Assistant: discovery failed for {row.name}")

		version = frappe.get_doc(
			{
				"doctype": "AI Knowledge Version",
				"version_no": _next_version_no(),
				"generated_on": frappe.utils.now_datetime(),
				"source": "Discovery",
				"status": "Draft",
				"content": frappe.as_json(content),
			}
		).insert(ignore_permissions=True)

		from origen_ai_assistant.knowledge import activate_version

		activate_version(version.name)

		settings.reload()
		settings.db_set("discovery_status", "Complete", update_modified=False)
		settings.db_set("discovery_last_error", "", update_modified=False)
	except Exception:
		frappe.log_error(title="AI Assistant: discovery job failed")
		settings.reload()
		settings.db_set("discovery_status", "Failed", update_modified=False)
		settings.db_set("discovery_last_error", frappe.get_traceback(), update_modified=False)
	finally:
		frappe.db.commit()


def _next_version_no() -> int:
	last = frappe.get_all("AI Knowledge Version", fields=["version_no"], order_by="version_no desc", limit_page_length=1)
	return (last[0].version_no + 1) if last else 1
