(function () {
    "use strict";

    const cfg = window.INTEL_WATCHER;
    const listEl = document.getElementById("iw-chat-messages");
    const form = document.getElementById("iw-chat-form");
    const input = document.getElementById("iw-chat-input");
    const statusEl = document.getElementById("iw-chat-status");
    const toggleBtn = document.getElementById("iw-chat-toggle");
    const workspace = document.querySelector(".iw-workspace");
    const HIDE_KEY = "iw-chat-hidden";
    const POLL_MS = 3000;

    // "Hide chat" is a per-browser viewing preference only - it never
    // touches the admin setting, and polling keeps running so returning
    // the panel shows current history.
    if (toggleBtn && workspace) {
        const applyHidden = (hidden) => {
            workspace.classList.toggle("iw-chat-off", hidden);
            toggleBtn.textContent = hidden ? "Show chat" : "Hide chat";
            toggleBtn.setAttribute("aria-pressed", hidden ? "true" : "false");
        };
        let hidden = false;
        try {
            hidden = localStorage.getItem(HIDE_KEY) === "1";
        } catch (_e) { /* storage unavailable - session-only toggle */ }
        applyHidden(hidden);
        toggleBtn.addEventListener("click", () => {
            hidden = !workspace.classList.contains("iw-chat-off");
            applyHidden(hidden);
            try {
                localStorage.setItem(HIDE_KEY, hidden ? "1" : "0");
            } catch (_e) { /* ignore */ }
        });
    }

    if (!cfg || !cfg.chatUrl || !listEl || !form || !input) {
        return;
    }

    let lastId = 0;
    let failureShown = false;

    function showStatus(text) {
        statusEl.textContent = text;
        statusEl.classList.toggle("d-none", !text);
    }

    function appendMessage(msg) {
        const row = document.createElement("div");
        row.className = "iw-chat-row";

        const user = document.createElement("span");
        user.className = "iw-chat-user";
        // textContent - never innerHTML: chat messages are user input.
        user.textContent = msg.user + ": ";
        row.appendChild(user);

        const body = document.createElement("span");
        body.className = "iw-chat-body";
        body.textContent = msg.message;
        row.appendChild(body);

        listEl.appendChild(row);
    }

    function refresh() {
        fetch(cfg.chatUrl + "?since=" + encodeURIComponent(lastId), {
            credentials: "same-origin"
        })
            .then((r) => {
                if (r.redirected || !r.ok) {
                    // 302 -> login page means the session expired
                    showStatus("Chat unavailable - refresh the page (your session may have expired).");
                    failureShown = true;
                    return null;
                }
                return r.json();
            })
            .then((data) => {
                if (!data) {
                    return;
                }
                if (failureShown) {
                    showStatus("");
                    failureShown = false;
                }
                const msgs = data.messages || [];
                const nearBottom =
                    listEl.scrollTop + listEl.clientHeight >=
                    listEl.scrollHeight - 40;
                msgs.forEach((msg) => {
                    appendMessage(msg);
                    lastId = Math.max(lastId, msg.id);
                });
                if (msgs.length && nearBottom) {
                    listEl.scrollTop = listEl.scrollHeight;
                }
            })
            .catch(() => {
                // transient network error - keep polling quietly
            });
    }

    form.addEventListener("submit", (e) => {
        e.preventDefault();
        const text = input.value.trim();
        if (!text) {
            return;
        }
        input.value = "";
        fetch(cfg.chatUrl, {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "X-CSRFToken": cfg.csrfToken,
                "Content-Type": "application/x-www-form-urlencoded"
            },
            body: "message=" + encodeURIComponent(text)
        })
            .then((r) => {
                if (r.redirected || !r.ok) {
                    showStatus("Message not sent - refresh the page (your session may have expired).");
                    return;
                }
                refresh();
            })
            .catch(() => showStatus("Message not sent - network error."));
    });

    refresh();
    setInterval(refresh, POLL_MS);
})();
