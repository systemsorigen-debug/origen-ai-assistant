frappe.ui.form.on("AI Assistant Settings", {
	refresh(frm) {
		if (frappe.user.has_role("AI Assistant Knowledge Manager") || frappe.user.has_role("System Manager")) {
			frm.add_custom_button(__("Discover Site Now"), () => {
				frappe.call("origen_ai_assistant.api.trigger_discovery").then(() => {
					frappe.show_alert({ message: __("Discovery queued — this runs in the background."), indicator: "blue" });
				});
			});
			frm.add_custom_button(__("Rebuild Knowledge File"), () => {
				frappe.call("origen_ai_assistant.api.refresh_knowledge_file").then(() => {
					frappe.show_alert({ message: __("Knowledge file rebuilt."), indicator: "green" });
				});
			});
		}
	},
});
