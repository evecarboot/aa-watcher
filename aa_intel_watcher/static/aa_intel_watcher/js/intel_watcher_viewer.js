(function () {
    "use strict";

    const cfg = window.INTEL_WATCHER;
    const grid = document.getElementById("iw-video-grid");
    const emptyState = document.getElementById("iw-empty-state");
    const STATUS_POLL_MS = 3000;
    const HLS_CONFIG = {
        lowLatencyMode: true,
        liveSyncDurationCount: 1,
        liveMaxLatencyDurationCount: 3,
        maxLiveSyncPlaybackRate: 1.5,
        enableWorker: true,
        backBufferLength: 30,
    };

    // hls_url -> { el, video, hls }
    const tiles = new Map();
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

    function createTile(stream, totalStreams) {
        const col = document.createElement("div");
        col.className = "iw-tile";
        col.style.width = "100%";

        const ratio = document.createElement("div");
        ratio.className = "ratio ratio-16x9 bg-black";
        ratio.style.width = "100%";

        const video = document.createElement("video");
        video.autoplay = true;
        video.playsInline = true;
        video.muted = true;
        video.defaultMuted = true;
        video.controls = true;
        video.preload = "auto";
        video.setAttribute("autoplay", "autoplay");
        video.setAttribute("muted", "muted");
        video.setAttribute("playsinline", "playsinline");
        ratio.appendChild(video);
        col.appendChild(ratio);

        const tryPlay = () => {
            const p = video.play();
            if (p && typeof p.catch === "function") {
                p.catch(() => {
                    video.addEventListener("loadedmetadata", () => video.play().catch(() => {}), { once: true });
                });
            }
        };

        const label = document.createElement("div");
    label.className = "iw-tile-meta";

        const name = document.createElement("span");
    name.className = "iw-tile-name";
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
            const hls = new window.Hls(HLS_CONFIG);
            hls.on(window.Hls.Events.MEDIA_ATTACHED, tryPlay);
            hls.loadSource(stream.hls_url);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, tryPlay);
            tile.hls = hls;
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = stream.hls_url;
            video.addEventListener("loadedmetadata", tryPlay, { once: true });
            video.addEventListener("canplay", tryPlay, { once: true });
            tryPlay();
        }

        return tile;
    }

    function destroyTile(tile) {
        if (tile.hls) {
            tile.hls.destroy();
        }
        tile.el.remove();
    }

    function updateGridLayout() {
        grid.style.width = "100%";

        tiles.forEach((tile) => {
            tile.el.style.width = "100%";
            tile.el.style.maxWidth = "100%";
            tile.el.style.flexBasis = tiles.size > 1 ? "24rem" : "100%";
        });
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
                        tiles.set(stream.hls_url, createTile(stream, streams.length));
                    }
                });

                updateGridLayout();
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

    updateGridLayout();
    refreshStatus();
    setInterval(refreshStatus, STATUS_POLL_MS);
})();

