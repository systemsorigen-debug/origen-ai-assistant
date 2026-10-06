"""Real SQL aggregates, run through frappe.get_list so the database — not the app — computes the
number, under the exact same permission-query-condition machinery a normal filtered list goes through.
This is deliberately NOT built on frappe.get_all (skips permission filtering) or raw frappe.db.count
(same gap), and deliberately NOT built by fetching matching rows into Python just to measure how many
there are — a COUNT(*)/SUM(*)/AVG(*) is one query regardless of table size, so a scalar result here is
never partial. Only a *grouped* aggregate can be capped (too many distinct groups to return), and that
cap is always disclosed, never silently presented as the whole picture.

Aggregate expressions are built as real pypika objects (frappe.qb.DocType(...), Count/Sum/Avg,
frappe.query_builder.functions.DateFormat), not raw SQL strings in `fields` — confirmed live against
this Frappe version that a bare string like "count(name) as value" is rejected outright by the query
builder's field parser ("SQL functions are not allowed as strings in SELECT"), which only accepts a
pypika Term or its own restricted {"COUNT": "field", "as": "alias"} dict form. A pypika object is used
directly here rather than the dict form so DATE_FORMAT (not in the dict form's function whitelist) is
still reachable for time_trend's bucketing.
"""

import frappe
from frappe.query_builder.functions import Avg, Count, Sum
from frappe.utils import get_datetime

MAX_GROUPS = 50
MAX_BUCKETS = 366
TIME_TREND_ROW_SAFETY_CAP = 50000


def _bucket_key(dt, interval: str) -> str:
	dt = get_datetime(dt)
	if interval == "month":
		return dt.strftime("%Y-%m")
	if interval == "week":
		iso = dt.isocalendar()
		return f"{iso[0]}-W{iso[1]:02d}"
	return dt.strftime("%Y-%m-%d")


def _metric_field(table, metric: str, field: str | None):
	metric = (metric or "count").lower()
	if metric == "count":
		return Count(table.name).as_("value")
	if metric == "sum" and field:
		return Sum(table[field]).as_("value")
	if metric == "avg" and field:
		return Avg(table[field]).as_("value")
	frappe.throw(frappe._("Invalid metric."), frappe.ValidationError)


def scalar_count(doctype: str, filters: dict, user: str) -> int:
	table = frappe.qb.DocType(doctype)
	rows = frappe.get_list(doctype, filters=filters, fields=[Count(table.name).as_("value")], user=user)
	return int(rows[0].value or 0) if rows else 0


def scalar_aggregate(doctype: str, metric: str, field: str | None, filters: dict, user: str):
	table = frappe.qb.DocType(doctype)
	rows = frappe.get_list(doctype, filters=filters, fields=[_metric_field(table, metric, field)], user=user)
	return rows[0].value if rows else None


def grouped_aggregate(doctype: str, group_by: str, metric: str, field: str | None, filters: dict, user: str) -> dict:
	"""No SQL `order_by` here — confirmed live that this Frappe version's query builder validates
	any `order_by`/`filters` token as if it were a real field on the doctype and checks field-level
	permission on it, with no exemption for a value that is actually this query's own SELECT alias
	(e.g. the "value" from `.as_("value")` below). For a non-Administrator caller, that lookup always
	denies (there's no such field, so no DocPerm grants it), which would silently break this tool for
	every real user. Sorted in Python instead — GROUP BY itself already collapses to one row per
	distinct value at the database side, so this is never "fetch every record," only "fetch every
	distinct group," which is cheap even before sorting.
	"""
	table = frappe.qb.DocType(doctype)

	distinct_rows = frappe.get_list(
		doctype, filters=filters, fields=[Count(table[group_by]).distinct().as_("value")], user=user
	)
	total_groups = int(distinct_rows[0].value or 0) if distinct_rows else 0

	rows = frappe.get_list(
		doctype,
		filters=filters,
		fields=[group_by, _metric_field(table, metric, field)],
		group_by=group_by,
		user=user,
	)
	rows = sorted(rows, key=lambda r: r.value or 0, reverse=True)
	truncated = len(rows) > MAX_GROUPS
	rows = rows[:MAX_GROUPS]

	grand_total = scalar_aggregate(doctype, metric, field, filters, user)

	return {
		"source_doctype": doctype,
		"filters_applied": filters,
		"field": field or "name",
		"groups": [{"key": r.get(group_by), "value": r.value} for r in rows],
		"grand_total": grand_total,
		"truncated": truncated,
		"groups_omitted": max(total_groups - MAX_GROUPS, 0) if truncated else None,
	}


def time_trend(doctype: str, date_field: str, interval: str, metric: str, field: str | None, filters: dict, user: str) -> dict:
	"""GROUP BY on a synthetic DATE_FORMAT-derived alias hits the exact same permission-check bug as
	grouped_aggregate's order_by (see its docstring) — confirmed live: the alias isn't a real field,
	so the clause validator denies it for every non-Administrator caller. There's no DB-function
	whitelist entry that would register the alias as exempt (DATE_FORMAT isn't in FUNCTION_MAPPING),
	so this buckets in Python instead of in SQL: fetch the real, permission-checked date_field (and
	metric field, for sum/avg) row by row, bounded by a safety cap, then group into day/week/month
	buckets here. A row-level fetch, not an aggregate one — so unlike the scalar/grouped paths above,
	this CAN be incomplete if matching records exceed the safety cap; that case is disclosed via
	`sample_only`, never silently presented as a complete trend (`grand_total` below is still exact,
	computed via the safe scalar path, regardless of whether the per-bucket breakdown is sampled).
	"""
	select_fields = [date_field] + ([field] if field and metric.lower() in ("sum", "avg") else [])
	rows = frappe.get_list(
		doctype, filters=filters, fields=select_fields, limit_page_length=TIME_TREND_ROW_SAFETY_CAP + 1, user=user
	)
	sample_only = len(rows) > TIME_TREND_ROW_SAFETY_CAP
	rows = rows[:TIME_TREND_ROW_SAFETY_CAP]

	buckets: dict[str, list] = {}
	for r in rows:
		if not r.get(date_field):
			continue
		key = _bucket_key(r[date_field], interval)
		buckets.setdefault(key, []).append(r.get(field) if field else 1)

	metric = (metric or "count").lower()

	def _reduce(values):
		if metric == "count":
			return len(values)
		nums = [v for v in values if v is not None]
		if not nums:
			return 0
		if metric == "sum":
			return sum(nums)
		return sum(nums) / len(nums)

	bucket_rows = sorted(({"bucket": k, "value": _reduce(v)} for k, v in buckets.items()), key=lambda b: b["bucket"])
	truncated = len(bucket_rows) > MAX_BUCKETS
	buckets_omitted = max(len(bucket_rows) - MAX_BUCKETS, 0) if truncated else None
	bucket_rows = bucket_rows[:MAX_BUCKETS]

	grand_total = scalar_aggregate(doctype, metric, field, filters, user)

	return {
		"source_doctype": doctype,
		"filters_applied": filters,
		"field": field or "name",
		"interval": interval,
		"buckets": bucket_rows,
		"grand_total": grand_total,
		"truncated": truncated,
		"buckets_omitted": buckets_omitted,
		"sample_only": sample_only,
	}
