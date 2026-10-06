"""The hard safety boundary. Every read/aggregate tool in tools.py routes through here before touching
the database, and every check is made against the real acting user — never Administrator, never with
ignore_permissions — so a DocType or field a user cannot read in Desk is one the assistant cannot see
either. This is the one place that boundary is defined; nothing else in the app should re-implement it.
"""

import frappe

# Refused outright regardless of role — defense in depth beyond whatever the ordinary permission
# system alone would allow, because these DocTypes hold secrets or internal app state, not business
# data an analyst question would ever legitimately need.
HARDCODED_BLOCKLIST = {
	"User",
	"Email Account",
	"Email Queue",
	"Integration Request",
	"Error Log",
	"AI Assistant Settings",
	"AI Assistant Chat Message",
	"AI Knowledge Version",
	"AI Knowledge Note",
	"AI Knowledge Suggestion",
	"AI Assistant Audit Log",
}

SAFE_FILTER_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is"}


def _settings_excluded_doctypes():
	try:
		settings = frappe.get_cached_doc("AI Assistant Settings")
	except Exception:
		return set()
	return {row.doctype_name for row in (settings.excluded_doctypes or [])}


def _settings_excluded_fields(doctype):
	try:
		settings = frappe.get_cached_doc("AI Assistant Settings")
	except Exception:
		return set()
	return {row.fieldname for row in (settings.excluded_fields or []) if row.doctype_name == doctype}


def is_doctype_blocked(doctype: str) -> bool:
	if doctype in HARDCODED_BLOCKLIST:
		return True
	if doctype in _settings_excluded_doctypes():
		return True
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return True
	# Child tables are never directly queryable — only reachable as part of a parent record the
	# caller already had permission to fetch.
	return bool(meta.istable)


def get_allowed_permlevels(doctype: str, user: str) -> set:
	"""Read permlevel grants straight from the DocType's own DocPerm rows, the same real mechanism
	Frappe itself uses — not re-derived or guessed. A field is visible to a user only if its own
	permlevel is in this set.
	"""
	roles = set(frappe.get_roles(user))
	meta = frappe.get_meta(doctype)
	levels = {0}
	for perm in meta.permissions:
		if perm.role in roles and perm.read:
			levels.add(perm.permlevel or 0)
	return levels


def check_doctype_readable(doctype: str, user: str) -> None:
	"""Raises frappe.PermissionError if this doctype is not something the given user may see at
	all — either blocked outright or simply not readable under their normal Frappe permissions.
	"""
	if is_doctype_blocked(doctype):
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)
	if not frappe.has_permission(doctype, "read", user=user):
		frappe.throw(frappe._("Not permitted."), frappe.PermissionError)


def list_readable_doctypes(user: str) -> list:
	"""The literal mechanism behind 'if a user is not allowed for a specific doctype it should not
	get insights of that using AI' — everything else is invisible, not just denied.
	"""
	out = []
	for row in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, fields=["name", "module"]):
		if is_doctype_blocked(row.name):
			continue
		if not frappe.has_permission(row.name, "read", user=user):
			continue
		out.append(row)
	return out


def get_readable_fields(doctype: str, user: str) -> list:
	"""Field list filtered by: Password fieldtype (always stripped), Settings' excluded-fields list,
	and real DocPerm permlevel grants for this specific user — all three, every time, not just one.
	"""
	meta = frappe.get_meta(doctype)
	excluded = _settings_excluded_fields(doctype)
	allowed_levels = get_allowed_permlevels(doctype, user)
	fields = []
	for df in meta.fields:
		if df.fieldtype in ("Password",):
			continue
		if df.fieldname in excluded:
			continue
		if (df.permlevel or 0) not in allowed_levels:
			continue
		fields.append(
			{
				"fieldname": df.fieldname,
				"label": df.label,
				"fieldtype": df.fieldtype,
				"options": df.options,
				"reqd": bool(df.reqd),
			}
		)
	return fields


def validate_fields(doctype: str, requested_fields: list, user: str) -> list:
	"""Only real, plain fieldnames on this doctype — no expressions, no aliases, no SQL functions
	smuggled through a 'field' the model asked for by name. Returns the subset that is actually safe
	to select; never raises on an invalid one, just silently drops it (the model gets back what it
	can have, not a traceback revealing why one field vanished).
	"""
	readable = {f["fieldname"] for f in get_readable_fields(doctype, user)}
	# Frappe's own always-present fields are safe to include if the caller asked for them.
	readable |= {"name", "owner", "creation", "modified", "modified_by"}
	safe = []
	for f in requested_fields or []:
		if not isinstance(f, str):
			continue
		if f in readable:
			safe.append(f)
	return safe or ["name"]


def validate_filters(doctype: str, filters, user: str) -> dict:
	"""filters must be a plain {fieldname: value} or {fieldname: [operator, value]} mapping, every
	fieldname validated against the doctype's real, readable fields (same exclusion list as output
	fields — a restricted field must not be usable in a filter just because it's omitted from the
	result), every operator drawn from a fixed safe set.
	"""
	if not filters:
		return {}
	if not isinstance(filters, dict):
		frappe.throw(frappe._("Invalid filters."), frappe.ValidationError)

	readable = {f["fieldname"] for f in get_readable_fields(doctype, user)}
	readable |= {"name", "owner", "creation", "modified", "modified_by"}
	safe = {}
	for fieldname, condition in filters.items():
		if fieldname not in readable:
			continue
		if isinstance(condition, (list, tuple)) and len(condition) == 2:
			operator, value = condition
			if str(operator).lower() not in SAFE_FILTER_OPERATORS:
				continue
			safe[fieldname] = [operator, value]
		else:
			safe[fieldname] = condition
	return safe


def validate_sort_or_group_field(doctype: str, fieldname: str, user: str) -> str | None:
	"""Same exclusion rules as validate_fields, for the one-field case used by order_by/group_by —
	a restricted field must not be reachable through grouping or ordering either."""
	if not fieldname:
		return None
	readable = {f["fieldname"] for f in get_readable_fields(doctype, user)}
	readable |= {"name", "owner", "creation", "modified", "modified_by"}
	return fieldname if fieldname in readable else None
