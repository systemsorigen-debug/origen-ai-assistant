"""One single enforcement point for field exclusion and masking, applied uniformly to every outbound
payload to the model provider — tool results, permission-filtered knowledge context, and the (narrow,
see api.py) replayed conversation history alike — so exclusion/masking can't be accidentally skipped
in one path while being enforced in the others.
"""

import re

import frappe


def _masking_rules():
	try:
		settings = frappe.get_cached_doc("AI Assistant Settings")
	except Exception:
		return []
	rules = []
	for row in settings.masking_rules or []:
		try:
			rules.append((re.compile(row.pattern), row.replacement or "[redacted]"))
		except re.error:
			continue
	return rules


def redact_text(value: str, rules=None) -> str:
	rules = rules if rules is not None else _masking_rules()
	for pattern, replacement in rules:
		value = pattern.sub(replacement, value)
	return value


def redact_before_send(payload):
	"""Recursively applies masking rules to every string value in a dict/list/str payload. Does not
	attempt to guess sensitive fields by name — that's permissions.py's job (exclusion, enforced
	before data is ever fetched); this only catches values that match an admin-configured pattern
	(e.g. something that looks like a phone number or email) inside otherwise-permitted data.
	"""
	rules = _masking_rules()
	if not rules:
		return payload
	return _walk(payload, rules)


def _walk(value, rules):
	if isinstance(value, str):
		return redact_text(value, rules)
	if isinstance(value, dict):
		return {k: _walk(v, rules) for k, v in value.items()}
	if isinstance(value, list):
		return [_walk(v, rules) for v in value]
	return value
