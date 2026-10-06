app_name = "origen_ai_assistant"
app_title = "Origen AI Assistant"
app_publisher = "Origen Systems"
app_description = "Permission-aware, read-only AI analyst chat for Desk"
app_email = "systemsorigen@gmail.com"
app_license = "mit"

# No dependency on ERPNext/CRM/HRMS/Origen Desk Kit or any other app — must install on a bare Frappe site.
required_apps = []

# Site-wide navbar shortcut only — it self-hides via frappe.boot.ai_assistant_access, set below.
# No override_whitelisted_methods entry exists in this app: nothing already in Frappe is intercepted
# or replaced, by design (see the project's isolation requirements).
app_include_js = [
	"origen_ai_assistant.bundle.js",
]
app_include_css = [
	"origen_ai_assistant.bundle.css",
]

# Boot
# ------------------
extend_bootinfo = [
	"origen_ai_assistant.boot.extend_bootinfo",
]

# Installation
# ------------------
after_install = "origen_ai_assistant.setup.after_install"
before_uninstall = "origen_ai_assistant.setup.before_uninstall"

# Scheduled Tasks
# ------------------
# Wrapped internally so a provider/discovery failure here can never fail the scheduler cycle for any
# other app's jobs — see knowledge.scheduled_refresh.
scheduler_events = {
	"daily": [
		"origen_ai_assistant.knowledge.scheduled_refresh",
	],
}
