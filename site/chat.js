// The chat bubble: talks to the backend /chat and /lead endpoints.
// Settings come from data attributes on <body>: data-client, data-api, data-phone, data-name.
// All text from the server or the visitor is rendered with textContent, never innerHTML.
(function () {
  "use strict";

  var MAX_HISTORY = 20;
  var MAX_CHARS = 2000;
  var body = document.body;
  var settings = {
    client: body.getAttribute("data-client") || "",
    api: (body.getAttribute("data-api") || "http://localhost:8000").replace(/\/+$/, ""),
    phone: body.getAttribute("data-phone") || "",
    name: body.getAttribute("data-name") || "",
  };

  var history = [];
  var busy = false;
  var lastFocus = null;

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, attrs[key]);
      });
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function fallbackMessage() {
    return settings.phone
      ? "Sorry, something went wrong. Please call us on " + settings.phone + "."
      : "Sorry, something went wrong. Please try again later.";
  }

  // --- build the UI --------------------------------------------------------------------

  var launcher = el("button", {
    type: "button",
    class: "chat-launcher",
    "aria-label": "Open chat",
    "aria-expanded": "false",
    "aria-controls": "chat-panel",
  });
  var svgNs = "http://www.w3.org/2000/svg";
  var icon = document.createElementNS(svgNs, "svg");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  var path = document.createElementNS(svgNs, "path");
  path.setAttribute(
    "d",
    "M4 3h16a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H8l-4 4V5a2 2 0 0 1 2-2z"
  );
  icon.appendChild(path);
  launcher.appendChild(icon);

  var panel = el("div", {
    id: "chat-panel",
    class: "chat-panel",
    role: "dialog",
    "aria-modal": "false",
    "aria-labelledby": "chat-title",
  });
  panel.hidden = true;

  var header = el("div", { class: "chat-header" });
  header.appendChild(el("h2", { id: "chat-title" }, settings.name || "Chat with us"));
  var closeBtn = el("button", { type: "button", class: "chat-close", "aria-label": "Close chat" }, "×");
  header.appendChild(closeBtn);
  panel.appendChild(header);

  var tabs = el("div", { class: "chat-tabs", role: "tablist", "aria-label": "Chat options" });
  var chatTab = el("button", {
    type: "button", class: "chat-tab", role: "tab", id: "chat-tab-chat",
    "aria-controls": "chat-view-chat", "aria-selected": "true",
  }, "Ask a question");
  var leadTab = el("button", {
    type: "button", class: "chat-tab", role: "tab", id: "chat-tab-lead",
    "aria-controls": "chat-view-lead", "aria-selected": "false",
  }, "Leave your details");
  tabs.appendChild(chatTab);
  tabs.appendChild(leadTab);
  panel.appendChild(tabs);

  // Chat view
  var chatView = el("div", {
    class: "chat-view", id: "chat-view-chat", role: "tabpanel", "aria-labelledby": "chat-tab-chat",
  });
  var messages = el("div", {
    class: "chat-messages", role: "log", "aria-live": "polite", "aria-label": "Conversation",
  });
  var chatForm = el("form", { class: "chat-form" });
  var input = el("input", {
    type: "text", class: "chat-input", "aria-label": "Type your message",
    placeholder: "Type your message", maxlength: String(MAX_CHARS), autocomplete: "off",
  });
  var sendBtn = el("button", { type: "submit", class: "btn btn-primary" }, "Send");
  chatForm.appendChild(input);
  chatForm.appendChild(sendBtn);
  chatView.appendChild(messages);
  chatView.appendChild(chatForm);
  panel.appendChild(chatView);

  // Lead view
  var leadView = el("div", {
    class: "chat-view", id: "chat-view-lead", role: "tabpanel", "aria-labelledby": "chat-tab-lead",
  });
  leadView.hidden = true;
  var leadForm = el("form", { class: "lead-form", novalidate: "" });
  leadForm.appendChild(el("p", null, "Leave your name and a phone number or email, and we will get back to you."));

  function field(labelText, name, type, attrs) {
    var label = el("label", null);
    label.appendChild(document.createTextNode(labelText));
    var control = el(type === "textarea" ? "textarea" : "input", attrs || {});
    if (type !== "textarea") {
      control.setAttribute("type", type);
    }
    control.setAttribute("name", name);
    label.appendChild(control);
    leadForm.appendChild(label);
    return control;
  }

  var leadName = field("Name", "name", "text", { maxlength: "100", autocomplete: "name", required: "" });
  var leadPhone = field("Phone", "phone", "tel", { maxlength: "200", autocomplete: "tel" });
  var leadEmail = field("Email", "email", "email", { maxlength: "200", autocomplete: "email" });
  var leadMessage = field("Message (optional)", "message", "textarea", { maxlength: String(MAX_CHARS) });
  var leadStatus = el("p", { class: "lead-status", role: "status", "aria-live": "polite" });
  var leadSubmit = el("button", { type: "submit", class: "btn btn-primary" }, "Send my details");
  leadForm.appendChild(leadStatus);
  leadForm.appendChild(leadSubmit);
  leadView.appendChild(leadForm);
  panel.appendChild(leadView);

  body.appendChild(panel);
  body.appendChild(launcher);

  // --- behaviour -----------------------------------------------------------------------

  function showView(which) {
    var isChat = which !== "lead";
    chatView.hidden = !isChat;
    leadView.hidden = isChat;
    chatTab.setAttribute("aria-selected", String(isChat));
    leadTab.setAttribute("aria-selected", String(!isChat));
    (isChat ? input : leadName).focus();
  }

  function openPanel(which) {
    if (panel.hidden) {
      lastFocus = document.activeElement;
    }
    panel.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    launcher.setAttribute("aria-label", "Close chat");
    if (!messages.childNodes.length) {
      addMessage(
        "bot",
        "Hi! Ask me anything about " + (settings.name || "us") + ", or leave your details."
      );
    }
    showView(which);
  }

  function closePanel() {
    panel.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    launcher.setAttribute("aria-label", "Open chat");
    (lastFocus && lastFocus.focus ? lastFocus : launcher).focus();
  }

  function addMessage(kind, text) {
    var node = el("div", { class: "chat-msg chat-msg-" + kind });
    node.textContent = text; // never innerHTML
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  function showTyping() {
    var node = el("div", { class: "chat-typing", "aria-label": "Assistant is typing" });
    node.appendChild(el("span"));
    node.appendChild(el("span"));
    node.appendChild(el("span"));
    messages.appendChild(node);
    messages.scrollTop = messages.scrollHeight;
    return node;
  }

  function errorDetail(data) {
    if (data && typeof data.detail === "string" && data.detail) {
      return data.detail;
    }
    return fallbackMessage();
  }

  function postJson(endpoint, payload) {
    return fetch(settings.api + endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
    });
  }

  function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
    input.disabled = value;
  }

  function sendMessage(text) {
    text = text.trim().slice(0, MAX_CHARS);
    if (!text || busy) {
      return;
    }
    addMessage("user", text);
    history.push({ role: "user", content: text });
    while (history.length > MAX_HISTORY) {
      history.shift();
    }
    setBusy(true);
    var typing = showTyping();
    postJson("/chat", { client: settings.client, messages: history })
      .then(function (result) {
        typing.remove();
        if (result.ok && result.data && typeof result.data.reply === "string") {
          addMessage("bot", result.data.reply);
          history.push({ role: "assistant", content: result.data.reply.slice(0, MAX_CHARS) });
          while (history.length > MAX_HISTORY) {
            history.shift();
          }
        } else {
          // 4xx/5xx: show the server's message, or a fallback with the phone number.
          history.pop();
          addMessage("error", result.status >= 500 ? errorDetail(result.data) : fallbackMessage());
        }
      })
      .catch(function () {
        typing.remove();
        history.pop();
        addMessage("error", fallbackMessage());
      })
      .then(function () {
        setBusy(false);
        input.focus();
      });
  }

  function submitLead() {
    var payload = {
      client: settings.client,
      name: leadName.value.trim(),
      phone: leadPhone.value.trim(),
      email: leadEmail.value.trim(),
      message: leadMessage.value.trim(),
    };
    leadStatus.className = "lead-status";
    if (!payload.name) {
      leadStatus.className = "lead-status is-error";
      leadStatus.textContent = "Please enter your name.";
      leadName.focus();
      return;
    }
    if (!payload.phone && !payload.email) {
      leadStatus.className = "lead-status is-error";
      leadStatus.textContent = "Please enter a phone number or an email address.";
      leadPhone.focus();
      return;
    }
    leadSubmit.disabled = true;
    leadStatus.textContent = "Sending…";
    postJson("/lead", payload)
      .then(function (result) {
        if (result.ok) {
          leadForm.reset();
          leadStatus.className = "lead-status is-success";
          leadStatus.textContent = "Thanks! We have your details and will be in touch soon.";
        } else {
          leadStatus.className = "lead-status is-error";
          leadStatus.textContent =
            result.status >= 500 ? errorDetail(result.data) : "Please check your details and try again.";
        }
      })
      .catch(function () {
        leadStatus.className = "lead-status is-error";
        leadStatus.textContent = fallbackMessage();
      })
      .then(function () {
        leadSubmit.disabled = false;
      });
  }

  launcher.addEventListener("click", function () {
    if (panel.hidden) {
      openPanel("chat");
    } else {
      closePanel();
    }
  });
  closeBtn.addEventListener("click", closePanel);
  chatTab.addEventListener("click", function () {
    showView("chat");
  });
  leadTab.addEventListener("click", function () {
    showView("lead");
  });
  chatForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = input.value;
    input.value = "";
    sendMessage(text);
  });
  leadForm.addEventListener("submit", function (event) {
    event.preventDefault();
    submitLead();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !panel.hidden) {
      closePanel();
    }
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-open-chat]"), function (button) {
    button.addEventListener("click", function () {
      openPanel(button.getAttribute("data-open-chat"));
    });
  });
})();
