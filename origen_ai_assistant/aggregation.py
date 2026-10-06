"""Real SQL aggregates, run through frappe.get_list so the database — not the app — computes the
number, under the exact same permission-query-condition machinery a normal filtered list goes through.
This is deliberately NOT built on frappe.get_all (skips permission filtering) or raw frappe.db.count
(same gap), and deliberately NOT built by fetching matching rows into Python just to measure how many
there are — a COUNT(*)/SUM(*)/AVG(*) is one query regardless of table size, so a scalar result here is
never partial. Only a *grouped* aggregate can be capped (too many distinct groups to return), and that
cap is always disclosed, never silently presented as the whole picture.
"""

import frappe

MAX_GROUPS = 50
MAX_BUCKETS = 366

DATE_FORMATS = {
	"day": "%Y-%m-%d",
	"week": "%x-W%v",
	"month": "%Y-%m",
}


def _metric_expr(metric: str, field: str | None) -> str:
	metric = (metric or "count").lower()
	if metric == "count":
		return "count(name) as value"
	if metric in ("sum", "avg") and field:
		return f"{metric}(`{field}`) as value"
	frappe.throw(frappe._("Invalid metric."), frappe.ValidationError)


def scalar_count(doctype: str, filters: dict, user: str) -> int:
	rows = frappe.get_list(doctype, filters=filters, fields=["count(name) as value"], user=user)
	return int(rows[0].value or 0) if rows else 0


def scalar_aggregate(doctype: str, metric: str, field: str | None, filters: dict, user: str):
	rows = frappe.get_list(doctype, filters=filters, fields=[_metric_expr(metric, field)], user=user)
	return rows[0].value if rows else None


def grouped_aggregate(doctype: str, group_by: str, metric: str, field: str | None, filters: dict, user: str) -> dict:
	expr = _metric_expr(metric, field)
	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=[group_by, expr],
		group_by=group_by,
		order_by="value desc",
		limit_page_length=MAX_GROUPS + 1,
		user=user,
	)
	truncated = len(rows) > MAX_GROUPS
	rows = rows[:MAX_GROUPS]

	grand_total = scalar_aggregate(doctype, metric, field, filters, user)
	groups_omitted = None
	if truncated:
		total_groups = frappe.get_list(
			doctype,
			filters=filters,
			fields=[f"count(distinct `{group_by}`) as value"],
			user=user,
		)
		total_groups = int(total_groups[0].value or 0) if total_groups else 0
		groups_omitted = max(total_groups - MAX_GROUPS, 0)

	return {
		"source_doctype": doctype,
		"filters_applied": filters,
		"field": field or "name",
		"groups": [{"key": r.get(group_by), "value": r.value} for r in rows],
		"grand_total": grand_total,
		"truncated": truncated,
		"groups_omitted": groups_omitted,
	}


def time_trend(doctype: str, date_field: str, interval: str, metric: str, field: str | None, filters: dict, user: str) -> dict:
	fmt = DATE_FORMATS.get(interval, DATE_FORMATS["day"])
	bucket_expr = f"date_format(`{date_field}`, '{fmt}') as bucket"
	expr = _metric_expr(metric, field)
	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=[bucket_expr, expr],
		group_by="bucket",
		order_by="bucket asc",
		limit_page_length=MAX_BUCKETS + 1,
		user=user,
	)
	truncated = len(rows) > MAX_BUCKETS
	rows = rows[:MAX_BUCKETS]

	grand_total = scalar_aggregate(doctype, metric, field, filters, user)

	return {
		"source_doctype": doctype,
		"filters_applied": filters,
		"field": field or "name",
		"interval": interval,
		"buckets": [{"bucket": r.bucket, "value": r.value} for r in rows],
		"grand_total": grand_total,
		"truncated": truncated,
		"buckets_omitted": None,  # exact count of omitted buckets isn't cheap to compute for time ranges; disclose truncation, not a false precision
	}
