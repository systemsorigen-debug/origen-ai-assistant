"""Daily retention cleanup for Chat Messages and Audit Log rows, per AI Assistant Settings'
configured retention windows (chat_retention_days / audit_retention_days). Wrapped so a failure
here can never break the scheduler cycle for any other job — same isolation guarantee as
knowledge.scheduled_refresh.
"""

import frappe
from frappe.utils import add_days, now_datetime


def scheduled_cleanup():
	try:
		settings = frappe.get_single("AI Assistant Settings")
		_delete_older_than("AI Assistant Chat Message", settings.chat_retention_days or 90)
		_delete_older_than("AI Assistant Audit Log", settings.audit_retention_days or 180)
	except Exception:
		frappe.log_error(title="AI Assistant: retention cleanup failed")


def _delete_older_than(doctype: str, days: int):
	if not days or days <= 0:
		return
	cutoff = add_days(now_datetime(), -days)
	names = frappe.get_all(doctype, filters={"creation": ["<", cutoff]}, pluck="name", limit_page_length=0)
	for name in names:
		frappe.delete_doc(doctype, name, ignore_permissions=True, force=True, delete_permanently=True)
	if names:
		frappe.db.commit()
