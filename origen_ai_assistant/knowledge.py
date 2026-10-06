"""Versioned knowledge activation, the derived private knowledge file, and per-caller permission
filtering of context before it ever reaches a model provider. A failed discovery/refresh never
reaches activate_version at all (discovery.py only calls it after a successful run), so the active
version is only ever replaced by another successful one — rollback is just reactivating an older row.
"""

import frappe

from origen_ai_assistant import permissions


def activate_version(version_name: str) -> None:
	previous_active = frappe.get_all("AI Knowledge Version", filters={"status": "Active"}, pluck="name")
	for name in previous_active:
		frappe.db.set_value("AI Knowledge Version", name, "status", "Superseded")

	version = frappe.get_doc("AI Knowledge Version", version_name)
	frappe.db.set_value("AI Knowledge Version", version_name, "status", "Active")
	frappe.db.set_value("AI Assistant Settings", None, "knowledge_version_active", version_name)
	frappe.db.set_value("AI Assistant Settings", None, "discovery_last_run", version.generated_on)
	frappe.db.commit()

	_prune_old_versions()
	write_knowledge_file()


def _prune_old_versions():
	settings = frappe.get_cached_doc("AI Assistant Settings")
	keep = settings.knowledge_version_retention_count or 10
	superseded = frappe.get_all(
		"AI Knowledge Version", filters={"status": "Superseded"}, order_by="version_no desc", pluck="name"
	)
	for name in superseded[keep:]:
		frappe.delete_doc("AI Knowledge Version", name, ignore_permissions=True, force=True)


def write_knowledge_file() -> None:
	"""The literal private per-site knowledge file — derived from the active Version plus every
	approved Business Note, never the source of truth itself (that's the DB), so it's safe to
	regenerate from scratch on every call rather than edited in place.
	"""
	settings = frappe.get_cached_doc("AI Assistant Settings")
	if not settings.knowledge_version_active:
		return

	version = frappe.get_doc("AI Knowledge Version", settings.knowledge_version_active)
	notes = frappe.get_all(
		"AI Knowledge Note",
		filters={"approved": 1},
		fields=["doctype_name", "fieldname", "note", "author"],
	)

	lines = [
		f"# AI Assistant Knowledge File — version {version.version_no} ({version.generated_on})",
		"",
		"## Approved business knowledge",
	]
	for n in notes:
		scope = f"{n.doctype_name}.{n.fieldname}" if n.fieldname else n.doctype_name
		lines.append(f"- **{scope}** (by {n.author}): {n.note}")
	lines.append("")
	lines.append("## Discovered schema (generated, not business-authored)")
	lines.append(version.content)

	from frappe.utils.file_manager import save_file

	# save_file always creates a new File record rather than overwriting, and Frappe stores the
	# deduped/randomized filename (e.g. "ai_assistant_knowledgeb1ee14.md") as file_name, not the
	# literal name passed in — confirmed live that filtering on the exact passed-in name matched
	# nothing and every call (one per Note save, per Suggestion approval, per Discovery) left a new
	# orphaned copy. This Settings record only ever has this one attachment from this app, so a plain
	# attached-doctype/name match for cleanup is safe and simple.
	for row in frappe.get_all(
		"File",
		filters={"attached_to_doctype": "AI Assistant Settings", "attached_to_name": settings.name},
		pluck="name",
	):
		frappe.delete_doc("File", row, ignore_permissions=True, force=True, delete_permanently=True)

	save_file(
		"ai_assistant_knowledge.md",
		"\n".join(lines),
		"AI Assistant Settings",
		settings.name,
		is_private=1,
	)


def scheduled_refresh():
	"""Wrapped so a provider/discovery failure here can never fail the scheduler cycle for any
	other app's jobs — the isolation guarantee applies to this hook too, not just extend_bootinfo.
	"""
	try:
		settings = frappe.get_single("AI Assistant Settings")
		if not settings.enabled or settings.knowledge_refresh_frequency != "Daily":
			return
		from origen_ai_assistant.discovery import run_discovery_job

		frappe.enqueue(run_discovery_job, queue="long", job_name="ai_assistant_scheduled_discovery")
	except Exception:
		frappe.log_error(title="AI Assistant: scheduled_refresh failed")


def get_context_for_user(user: str) -> dict:
	"""Recomputed fresh on every call — never cached — so a permission change is reflected on the
	very next turn, not whenever some cache happens to expire.
	"""
	settings = frappe.get_cached_doc("AI Assistant Settings")
	if not settings.knowledge_version_active:
		return {"doctypes": {}, "notes": [], "incomplete": True}

	version = frappe.get_doc("AI Knowledge Version", settings.knowledge_version_active)
	full = frappe.parse_json(version.content)

	filtered_doctypes = {}
	for doctype_name, entry in (full.get("doctypes") or {}).items():
		if permissions.is_doctype_blocked(doctype_name):
			continue
		if not frappe.has_permission(doctype_name, "read", user=user):
			continue
		filtered_doctypes[doctype_name] = entry

	notes = []
	for n in frappe.get_all(
		"AI Knowledge Note", filters={"approved": 1}, fields=["doctype_name", "fieldname", "note"]
	):
		if n.doctype_name not in filtered_doctypes:
			continue
		if n.fieldname:
			allowed_levels = permissions.get_allowed_permlevels(n.doctype_name, user)
			meta = frappe.get_meta(n.doctype_name)
			df = meta.get_field(n.fieldname)
			if not df or (df.permlevel or 0) not in allowed_levels:
				continue
		notes.append(n)

	return {"doctypes": filtered_doctypes, "notes": notes, "incomplete": False}
