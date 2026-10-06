// Site-wide navbar shortcut + floating chat widget. Both self-hide via frappe.boot.ai_assistant_access
// (set by boot.py's extend_bootinfo) — a user without access never sees either exist, not just a
// denied click. Everything here is wrapped so nothing can ever break Desk navigation for anyone.
(function () {
	try {
		if (!frappe.boot || !frappe.boot.ai_assistant_access) {
			return;
		}

		frappe.after_ajax(function () {
			init_navbar_icon();
			init_floating_widget();
		});
	} catch (e) {
		// Never let this break Desk boot for anyone — see the project's isolation requirement.
	}

	function init_navbar_icon() {
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
	}

	// A small, dismissible chat overlay available from anywhere in Desk, so a user can get help
	// without leaving the page they're working on — the full /app/ai-assistant Page (above) stays
	// around for a larger view and reviewing history. Positioned clear of origen_desk_kit's own
	// bottom-right customize toggle (right:18/bottom:18, 40px) so the two never overlap on a site
	// that has both installed — this app has no dependency on ODK, it's just considerate spacing.
	function init_floating_widget() {
		if ($(".ai-assistant-widget-root").length) {
			return;
		}

		const SESSION_KEY = "ai_assistant_widget_session";
		let session = null;
		try {
			session = window.localStorage.getItem(SESSION_KEY);
		} catch (e) {
			/* private-browsing/blocked storage — fall back to a per-load session below */
		}
		if (!session) {
			session = frappe.utils.get_random(16);
			try {
				window.localStorage.setItem(SESSION_KEY, session);
			} catch (e) {
				/* non-fatal — just won't persist across reloads */
			}
		}

		const state = { messages: [], sending: false, open: false };

		const $root = $(`<div class="ai-assistant-widget-root"></div>`).appendTo("body");

		const $fab = $(
			`<button class="ai-assistant-fab" title="${__("AI Assistant")}" type="button">` +
				`<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">` +
				`<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"></path>` +
				`</svg></button>`
		).appendTo($root);

		const $panel = $(`
			<div class="ai-assistant-widget" style="display: none;">
				<div class="ai-assistant-widget-header">
					<span>${__("AI Assistant")}</span>
					<div class="ai-assistant-widget-header-actions">
						<a href="/app/ai-assistant" title="${__("Open full view")}" class="ai-assistant-widget-expand">⤢</a>
						<button type="button" class="ai-assistant-widget-close" title="${__("Close")}">&times;</button>
					</div>
				</div>
				<div class="ai-assistant-messages"></div>
				<div class="ai-assistant-input-row">
					<textarea class="form-control ai-assistant-input" rows="1"
						placeholder="${__("Ask about your data…")}"></textarea>
					<button type="button" class="btn btn-primary ai-assistant-send">${__("Send")}</button>
				</div>
			</div>
		`).appendTo($root);

		const $messages = $panel.find(".ai-assistant-messages");
		const $input = $panel.find(".ai-assistant-input");
		const $send = $panel.find(".ai-assistant-send");

		function render() {
			$messages.empty();
			if (!state.messages.length) {
				$messages.append(
					`<div class="ai-assistant-widget-empty text-muted">${__(
						"Ask me anything about data you have access to — I only read, I never change anything."
					)}</div>`
				);
			}
			state.messages.forEach((m) => {
				$(`<div class="ai-assistant-msg ai-assistant-msg-${m.role}"></div>`)
					.text(m.content)
					.appendTo($messages);
			});
			$messages.scrollTop($messages[0].scrollHeight);
		}

		function set_open(open) {
			state.open = open;
			$panel.toggle(open);
			if (open) {
				load_history();
				$input.trigger("focus");
			}
		}

		function load_history() {
			if (state.messages.length) {
				return; // already loaded this page view
			}
			frappe.call({
				method: "origen_ai_assistant.api.get_chat_history",
				args: { session },
				callback: (r) => {
					(r.message || []).forEach((m) => state.messages.push({ role: m.role, content: m.content }));
					render();
				},
			});
		}

		function send() {
			const text = $input.val().trim();
			if (!text || state.sending) {
				return;
			}
			state.messages.push({ role: "user", content: text });
			render();
			$input.val("");
			state.sending = true;
			$send.prop("disabled", true);

			frappe.call({
				method: "origen_ai_assistant.api.send_message",
				args: { session, message: text },
				callback: (r) => {
					state.messages.push({ role: "assistant", content: (r.message && r.message.reply) || "" });
					render();
				},
				error: () => {
					state.messages.push({
						role: "assistant",
						content: __("Something went wrong answering that — please try again."),
					});
					render();
				},
				always: () => {
					state.sending = false;
					$send.prop("disabled", false);
				},
			});
		}

		$fab.on("click", () => set_open(!state.open));
		$panel.find(".ai-assistant-widget-close").on("click", () => set_open(false));
		$send.on("click", send);
		$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				send();
			}
		});

		render();
	}
})();
