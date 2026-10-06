import frappe
from frappe.model.document import Document


class AIKnowledgeSuggestion(Document):
	def on_update(self):
		if self.status != "Pending" and not self.reviewed_by:
			frappe.db.set_value(self.doctype, self.name, "reviewed_by", frappe.session.user)
			frappe.db.set_value(self.doctype, self.name, "reviewed_on", frappe.utils.now_datetime())

		# A conversation can only ever propose a correction — it becomes real knowledge only once a
		# Knowledge Manager approves it here, and only once (promoted_note guards a repeat save from
		# creating a duplicate). This is the actual mechanism behind "the assistant improves from
		# what we tell it": nothing is believed automatically, a human has to say yes first.
		if self.status == "Approved" and not self.promoted_note:
			note = frappe.get_doc(
				{
					"doctype": "AI Knowledge Note",
					"doctype_name": self.doctype_name,
					"fieldname": self.fieldname,
					"note": self.proposed_text,
					"approved": 1,
				}
			).insert(ignore_permissions=True)
			frappe.db.set_value("AI Knowledge Note", note.name, "approved_by", frappe.session.user)
			frappe.db.set_value(self.doctype, self.name, "promoted_note", note.name)
			frappe.db.commit()
