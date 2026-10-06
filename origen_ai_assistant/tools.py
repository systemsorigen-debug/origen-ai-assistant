"""The fixed, read-only tool set exposed to the model. This is the whole safety guarantee: there is no
tool here, and nowhere else registered, that can insert/update/delete/submit/cancel a document or run
arbitrary SQL — the model cannot reach a capability that was never built, regardless of how a prompt is
phrased. Every tool re-checks the real caller's permissions on every call; nothing here trusts a result
cached from an earlier turn.
"""

import frappe

from origen_ai_assistant import aggregation, permissions
from origen_ai_assistant.redact import redact_before_send

MAX_RECORDS = 100


def _log(user, session, tool_name, doctype, filters, result_summary):
	try:
		frappe.get_doc(
			{
				"doctype": "AI Assistant Audit Log",
				"user": user,
				"session": session,
				"tool_name": tool_name,
				"doctype_name": doctype or "",
				"filters_applied": frappe.as_json(redact_before_send(filters or {})),
				"result_summary": frappe.as_json(redact_before_send(result_summary)),
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="AI Assistant: audit log insert failed")


def list_doctypes(user: str, session: str):
	rows = permissions.list_readable_doctypes(user)
	result = [{"doctype": r.name, "module": r.module} for r in rows]
	_log(user, session, "list_doctypes", None, {}, {"count": len(result)})
	return result


def describe_doctype(user: str, session: str, doctype: str):
	permissions.check_doctype_readable(doctype, user)
	fields = permissions.get_readable_fields(doctype, user)
	_log(user, session, "describe_doctype", doctype, {}, {"field_count": len(fields)})
	return {"doctype": doctype, "fields": fields}


def get_records(user: str, session: str, doctype: str, filters: dict = None, fields: list = None, limit: int = 20):
	permissions.check_doctype_readable(doctype, user)
	safe_fields = permissions.validate_fields(doctype, fields, user)
	safe_filters = permissions.validate_filters(doctype, filters, user)
	safe_limit = max(1, min(int(limit or 20), MAX_RECORDS))

	try:
		rows = frappe.get_list(doctype, filters=safe_filters, fields=safe_fields, limit_page_length=safe_limit, user=user)
	except frappe.PermissionError:
		_log(user, session, "get_records", doctype, safe_filters, {"error": "not permitted"})
		return {"error": "Not permitted."}

	result = [redact_before_send(dict(r)) for r in rows]
	_log(user, session, "get_records", doctype, safe_filters, {"row_count": len(result)})
	return {"doctype": doctype, "filters_applied": safe_filters, "rows": result, "row_count": len(result)}


def get_count(user: str, session: str, doctype: str, filters: dict = None):
	permissions.check_doctype_readable(doctype, user)
	safe_filters = permissions.validate_filters(doctype, filters, user)
	try:
		total = aggregation.scalar_count(doctype, safe_filters, user)
	except frappe.PermissionError:
		_log(user, session, "get_count", doctype, safe_filters, {"error": "not permitted"})
		return {"error": "Not permitted."}
	_log(user, session, "get_count", doctype, safe_filters, {"total": total})
	return {"doctype": doctype, "filters_applied": safe_filters, "total": total}


def aggregate(user: str, session: str, doctype: str, group_by: str, metric: str, field: str = None, filters: dict = None):
	permissions.check_doctype_readable(doctype, user)
	safe_group_by = permissions.validate_sort_or_group_field(doctype, group_by, user)
	safe_field = permissions.validate_sort_or_group_field(doctype, field, user) if field else None
	safe_filters = permissions.validate_filters(doctype, filters, user)
	if not safe_group_by:
		return {"error": "group_by field is not permitted or does not exist."}
	if (metric or "count").lower() in ("sum", "avg") and (not field or not safe_field):
		return {"error": "field is required (and must be a permitted field) for sum/avg."}

	try:
		result = aggregation.grouped_aggregate(doctype, safe_group_by, metric, safe_field, safe_filters, user)
	except frappe.PermissionError:
		_log(user, session, "aggregate", doctype, safe_filters, {"error": "not permitted"})
		return {"error": "Not permitted."}

	result = redact_before_send(result)
	_log(user, session, "aggregate", doctype, safe_filters, {k: v for k, v in result.items() if k != "groups"})
	return result


def time_trend(
	user: str,
	session: str,
	doctype: str,
	date_field: str,
	interval: str,
	metric: str,
	field: str = None,
	filters: dict = None,
):
	permissions.check_doctype_readable(doctype, user)
	safe_date_field = permissions.validate_sort_or_group_field(doctype, date_field, user)
	safe_field = permissions.validate_sort_or_group_field(doctype, field, user) if field else None
	safe_filters = permissions.validate_filters(doctype, filters, user)
	if not safe_date_field:
		return {"error": "date_field is not permitted or does not exist."}

	if safe_date_field not in ("creation", "modified"):
		meta = frappe.get_meta(doctype)
		df = meta.get_field(safe_date_field)
		if not df or df.fieldtype not in ("Date", "Datetime"):
			return {"error": "date_field must be a Date or Datetime field."}
	if (metric or "count").lower() in ("sum", "avg") and (not field or not safe_field):
		return {"error": "field is required (and must be a permitted field) for sum/avg."}

	if interval not in ("day", "week", "month"):
		interval = "day"

	try:
		result = aggregation.time_trend(doctype, safe_date_field, interval, metric, safe_field, safe_filters, user)
	except frappe.PermissionError:
		_log(user, session, "time_trend", doctype, safe_filters, {"error": "not permitted"})
		return {"error": "Not permitted."}

	result = redact_before_send(result)
	_log(user, session, "time_trend", doctype, safe_filters, {k: v for k, v in result.items() if k != "buckets"})
	return result


# Provider-facing tool definitions (name, description, JSON-schema input) — shared across every
# provider adapter so the chat controller stays provider-agnostic. See providers/base.py.
TOOL_DEFINITIONS = [
	{
		"name": "list_doctypes",
		"description": "List the DocTypes the current user is permitted to read. DocTypes the user cannot read are omitted entirely, not flagged as restricted.",
		"input_schema": {"type": "object", "properties": {}},
	},
	{
		"name": "describe_doctype",
		"description": "Get the field list for one DocType, filtered to what the current user may see (sensitive/password/excluded/permission-restricted fields are never included).",
		"input_schema": {
			"type": "object",
			"properties": {"doctype": {"type": "string"}},
			"required": ["doctype"],
		},
	},
	{
		"name": "get_records",
		"description": "Fetch up to 100 records from one DocType, filtered and permission-checked as the current user. Use aggregate/get_count for totals, not this.",
		"input_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"filters": {"type": "object"},
				"fields": {"type": "array", "items": {"type": "string"}},
				"limit": {"type": "integer"},
			},
			"required": ["doctype"],
		},
	},
	{
		"name": "get_count",
		"description": "Get an exact, permission-scoped record count for a DocType and filter set. This is a real database count, never partial.",
		"input_schema": {
			"type": "object",
			"properties": {"doctype": {"type": "string"}, "filters": {"type": "object"}},
			"required": ["doctype"],
		},
	},
	{
		"name": "aggregate",
		"description": "Group-by aggregation (count/sum/avg) for analytics questions, e.g. 'leads by source'. Returns the true grand_total always; if the number of groups is capped, truncated=true and groups_omitted is set — never present a capped result as the whole picture.",
		"input_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"group_by": {"type": "string"},
				"metric": {"type": "string", "enum": ["count", "sum", "avg"]},
				"field": {"type": "string", "description": "required for sum/avg"},
				"filters": {"type": "object"},
			},
			"required": ["doctype", "group_by", "metric"],
		},
	},
	{
		"name": "time_trend",
		"description": "Time-bucketed aggregation (day/week/month) for trend questions, e.g. 'applications per week this quarter'. Returns the true grand_total always; if sample_only is true, the per-bucket breakdown was computed from a bounded sample (too many matching rows) and may not be exact — narrow the filters/date range for an exact trend.",
		"input_schema": {
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"date_field": {"type": "string"},
				"interval": {"type": "string", "enum": ["day", "week", "month"]},
				"metric": {"type": "string", "enum": ["count", "sum", "avg"]},
				"field": {"type": "string"},
				"filters": {"type": "object"},
			},
			"required": ["doctype", "date_field", "interval", "metric"],
		},
	},
]

_DISPATCH = {
	"list_doctypes": list_doctypes,
	"describe_doctype": describe_doctype,
	"get_records": get_records,
	"get_count": get_count,
	"aggregate": aggregate,
	"time_trend": time_trend,
}


def dispatch_tool(name: str, user: str, session: str, tool_input: dict):
	fn = _DISPATCH.get(name)
	if not fn:
		return {"error": f"Unknown tool: {name}"}
	tool_input = tool_input or {}
	try:
		return fn(user=user, session=session, **tool_input)
	except frappe.PermissionError:
		return {"error": "Not permitted."}
	except TypeError:
		return {"error": "Invalid arguments for this tool."}
