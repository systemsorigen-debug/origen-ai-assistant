frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "AI Assistant",
		single_column: true,
	});

	if (!frappe.boot.ai_assistant_access) {
		$(page.body).html(
			`<div class="text-muted" style="padding: 2rem;">${__("You do not have access to the AI Assistant.")}</div>`
		);
		return;
	}

	page.add_button(__("New Conversation"), () => {
		state.session = frappe.utils.get_random(16);
		render();
	});

	const state = { session: frappe.utils.get_random(16), messages: [], sending: false };

	const $wrapper = $(`
		<div class="ai-assistant-chat">
			<div class="ai-assistant-messages"></div>
			<div class="ai-assistant-input-row">
				<textarea class="form-control ai-assistant-input" rows="2"
					placeholder="${__("Ask about your data — I can only read what you're permitted to see, and I never change anything.")}"></textarea>
				<button class="btn btn-primary ai-assistant-send">${__("Send")}</button>
			</div>
		</div>
	`).appendTo(page.body);

	const $messages = $wrapper.find(".ai-assistant-messages");
	const $input = $wrapper.find(".ai-assistant-input");
	const $send = $wrapper.find(".ai-assistant-send");

	function render() {
		$messages.empty();
		state.messages.forEach((m) => {
			$(`<div class="ai-assistant-msg ai-assistant-msg-${m.role}"></div>`)
				.text(m.content)
				.appendTo($messages);
		});
		$messages.scrollTop($messages[0].scrollHeight);
	}

	function send() {
		const text = $input.val().trim();
		if (!text || state.sending) return;

		state.messages.push({ role: "user", content: text });
		render();
		$input.val("");
		state.sending = true;
		$send.prop("disabled", true);

		frappe.call({
			method: "origen_ai_assistant.api.send_message",
			args: { session: state.session, message: text },
			callback: (r) => {
				state.messages.push({ role: "assistant", content: (r.message && r.message.reply) || "" });
				render();
			},
			always: () => {
				state.sending = false;
				$send.prop("disabled", false);
			},
		});
	}

	$send.on("click", send);
	$input.on("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	});

	render();
};
