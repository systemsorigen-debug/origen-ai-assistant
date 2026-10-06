import frappe
from frappe.model.document import Document


class AIKnowledgeNote(Document):
	def before_insert(self):
		self.author = frappe.session.user

	def on_update(self):
		if self.approved and not self.approved_by:
			frappe.db.set_value(self.doctype, self.name, "approved_by", frappe.session.user)
		if not self.approved and self.approved_by:
			frappe.db.set_value(self.doctype, self.name, "approved_by", "")

		from origen_ai_assistant.knowledge import write_knowledge_file

		write_knowledge_file()
