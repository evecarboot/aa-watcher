(function () {
    "use strict";

    const cfg = window.INTEL_WATCHER;
    const grid = document.getElementById("iw-video-grid");
    const emptyState = document.getElementById("iw-empty-state");
    const chatLog = document.getElementById("iw-chat-log");
    const chatForm = document.getElementById("iw-chat-form");
    const chatInput = document.getElementById("iw-chat-input");

    // hls_url -> { el, video, hls }
    const tiles = new Map();
    let lastChatId = 0;
    let anyUnmutedYet = false;

    function soloAudio(key) {
        tiles.forEach((tile, tileKey) => {
            const isTarget = tileKey === key;
            tile.video.muted = !isTarget;
            tile.soloBtn.classList.toggle("btn-success", isTarget);
            tile.soloBtn.classList.toggle("btn-outline-secondary", !isTarget);
            tile.soloBtn.textContent = isTarget ? "\uD83D\uDD0A Audio on" : "\uD83D\uDD07 Solo audio";
        });
        anyUnmutedYet = true;
    }

    function createTile(stream) {
        const col = document.createElement("div");
        col.className = "col-12 col-md-6 iw-tile";

        const ratio = document.createElement("div");
        ratio.className = "ratio ratio-16x9 bg-black";

        const video = document.createElement("video");
        video.autoplay = true;
        video.playsInline = true;
        video.muted = true;
        video.controls = true;
        ratio.appendChild(video);
        col.appendChild(ratio);

        const label = document.createElement("div");
        label.className = "d-flex justify-content-between align-items-center mt-1";

        const name = document.createElement("span");
        name.className = "fw-semibold";
        name.textContent = stream.display_name;
        label.appendChild(name);

        const soloBtn = document.createElement("button");
        soloBtn.type = "button";
        soloBtn.className = "btn btn-sm btn-outline-secondary";
        soloBtn.textContent = "\uD83D\uDD07 Solo audio";
        label.appendChild(soloBtn);

        col.appendChild(label);
        grid.appendChild(col);

        const tile = { el: col, video, hls: null, soloBtn };
        soloBtn.addEventListener("click", () => soloAudio(stream.hls_url));

        if (window.Hls && window.Hls.isSupported()) {
            const hls = new window.Hls();
            hls.loadSource(stream.hls_url);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, () => video.play());
            tile.hls = hls;
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = stream.hls_url;
            video.play();
        }

        return tile;
    }

    function destroyTile(tile) {
        if (tile.hls) {
            tile.hls.destroy();
        }
        tile.el.remove();
    }

    function refreshStatus() {
        fetch(cfg.statusUrl, { credentials: "same-origin" })
            .then((r) => r.json())
            .then((data) => {
                const streams = data.streams || [];
                const liveKeys = new Set(streams.map((s) => s.hls_url));

                // Remove tiles for streams that went offline.
                tiles.forEach((tile, key) => {
                    if (!liveKeys.has(key)) {
                        destroyTile(tile);
                        tiles.delete(key);
                    }
                });

                // Add tiles for newly live streams.
                streams.forEach((stream) => {
                    if (!tiles.has(stream.hls_url)) {
                        tiles.set(stream.hls_url, createTile(stream));
                    }
                });

                emptyState.classList.toggle("d-none", streams.length > 0);

                // Auto solo the first stream so the very first viewer isn't
                // stuck fully muted, but never fight a viewer's own choice
                // once at least one unmute has happened.
                if (!anyUnmutedYet && streams.length > 0) {
                    soloAudio(streams[0].hls_url);
                }
            })
            .catch(() => {});
    }

    function appendMessage(msg) {
        const div = document.createElement("div");
        div.className = "iw-msg";
        const userSpan = document.createElement("span");
        userSpan.className = "iw-user";
        userSpan.textContent = msg.user + ": ";
        div.appendChild(userSpan);
        div.appendChild(document.createTextNode(msg.message));
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
        lastChatId = Math.max(lastChatId, msg.id);
    }

    function refreshChat() {
        fetch(cfg.chatUrl + "?since=" + lastChatId, { credentials: "same-origin" })
            .then((r) => r.json())
            .then((data) => {
                (data.messages || []).forEach(appendMessage);
            })
            .catch(() => {});
    }

    if (chatForm) {
        chatForm.addEventListener("submit", (e) => {
            e.preventDefault();
            const message = chatInput.value.trim();
            if (!message) {
                return;
            }
            fetch(cfg.chatUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRFToken": cfg.csrfToken
                },
                body: "message=" + encodeURIComponent(message)
            }).then(() => {
                chatInput.value = "";
                refreshChat();
            });
        });
    }

    refreshStatus();
    refreshChat();
    setInterval(refreshStatus, 8000);
    setInterval(refreshChat, 3000);
})();

