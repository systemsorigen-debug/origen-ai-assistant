// Site-wide navbar shortcut to the AI Assistant Page. Self-hides via frappe.boot.ai_assistant_access
// (set by boot.py's extend_bootinfo) — a user without access never sees this button exist, not just
// a denied click. Wrapped so nothing here can ever break Desk navigation for anyone.
(function () {
	try {
		if (!frappe.boot || !frappe.boot.ai_assistant_access) {
			return;
		}

		frappe.after_ajax(function () {
			if ($(".ai-assistant-navbar-btn").length) {
				return;
			}
			const $btn = $(
				`<a class="ai-assistant-navbar-btn" title="${__("AI Assistant")}" href="/app/ai-assistant">` +
					`<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">` +
					`<circle cx="12" cy="12" r="9"></circle><path d="M9 10h.01M15 10h.01M9 15c.8.6 1.9 1 3 1s2.2-.4 3-1"></path>` +
					`</svg></a>`
			);
			$(".navbar-right, .navbar-nav").first().prepend($btn);
		});
	} catch (e) {
		// Never let this break Desk boot for anyone — see the project's isolation requirement.
	}
})();
