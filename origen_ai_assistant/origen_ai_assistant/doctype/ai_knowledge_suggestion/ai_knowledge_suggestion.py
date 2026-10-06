import frappe
from frappe.model.document import Document


class AIKnowledgeSuggestion(Document):
	def on_update(self):
		if self.status != "Pending" and not self.reviewed_by:
			frappe.db.set_value(self.doctype, self.name, "reviewed_by", frappe.session.user)
			frappe.db.set_value(self.doctype, self.name, "reviewed_on", frappe.utils.now_datetime())
