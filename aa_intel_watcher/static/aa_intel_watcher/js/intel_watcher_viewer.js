(function () {
    "use strict";

    const cfg = window.INTEL_WATCHER;
    const grid = document.getElementById("iw-video-grid");
    const emptyState = document.getElementById("iw-empty-state");
    const STATUS_POLL_MS = 3000;
    const DRIFT_CHECK_INTERVAL_MS = 2000;
    const DRIFT_SNAP_THRESHOLD_SEC = 8;
    const DRIFT_TARGET_BEHIND_LIVE_SEC = 1.5;
    const HLS_CONFIG = {
        lowLatencyMode: true,
        // Bias toward starting playback quickly and staying stable on slower
        // connections, rather than forcing the viewer as close to live as possible.
        startLevel: 0,
        testBandwidth: false,
        liveSyncDurationCount: 4,
        liveMaxLatencyDurationCount: 8,
        maxLiveSyncPlaybackRate: 1.2,
        enableWorker: true,
        capLevelToPlayerSize: true,
        maxBufferLength: 16,
        backBufferLength: 60,
    };

    // hls_url -> { el, video, hls, driftTimer }
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

    function bindPlaybackRecovery(video, tryPlay) {
        // Browser decoders occasionally enter stalled/waiting states on
        // rough links; this nudges playback to resume once data appears.
        video.addEventListener("stalled", tryPlay);
        video.addEventListener("waiting", tryPlay);
        video.addEventListener("canplay", tryPlay);
    }

    function bindHlsRecovery(hls, video, streamUrl, tryPlay) {
        const HlsEvents = window.Hls.Events;
        const HlsErrorTypes = window.Hls.ErrorTypes;

        hls.on(HlsEvents.ERROR, (_event, data) => {
            if (!data || !data.fatal) {
                return;
            }

            if (data.type === HlsErrorTypes.NETWORK_ERROR) {
                hls.startLoad();
                return;
            }

            if (data.type === HlsErrorTypes.MEDIA_ERROR) {
                hls.recoverMediaError();
                tryPlay();
                return;
            }

            // Last resort for unrecoverable errors.
            hls.destroy();
            video.src = streamUrl;
            video.addEventListener("loadedmetadata", tryPlay, { once: true });
            tryPlay();
        });
    }

    function snapNearLive(video) {
        if (video.paused || video.seeking || video.readyState < 2) {
            return;
        }

        if (!video.seekable || video.seekable.length < 1) {
            return;
        }

        const lastRange = video.seekable.length - 1;
        const liveEdge = video.seekable.end(lastRange);
        const liveStart = video.seekable.start(lastRange);
        if (!Number.isFinite(liveEdge) || !Number.isFinite(video.currentTime)) {
            return;
        }

        const drift = liveEdge - video.currentTime;
        if (drift <= DRIFT_SNAP_THRESHOLD_SEC) {
            return;
        }

        const target = Math.max(liveEdge - DRIFT_TARGET_BEHIND_LIVE_SEC, liveStart);
        if (target > video.currentTime) {
            video.currentTime = target;
        }
    }

    function createTile(stream) {
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

        bindPlaybackRecovery(video, tryPlay);

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

        const tile = {
            el: col,
            video,
            hls: null,
            soloBtn,
            driftTimer: setInterval(() => snapNearLive(video), DRIFT_CHECK_INTERVAL_MS)
        };
        soloBtn.addEventListener("click", () => soloAudio(stream.hls_url));

        if (window.Hls && window.Hls.isSupported()) {
            const hls = new window.Hls(HLS_CONFIG);
            hls.on(window.Hls.Events.MEDIA_ATTACHED, tryPlay);
            hls.loadSource(stream.hls_url);
            hls.attachMedia(video);
            hls.on(window.Hls.Events.MANIFEST_PARSED, tryPlay);
            bindHlsRecovery(hls, video, stream.hls_url, tryPlay);
            tile.hls = hls;
        } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = stream.hls_url;
            video.addEventListener("loadedmetadata", tryPlay, { once: true });
            video.addEventListener("canplay", tryPlay, { once: true });
            tryPlay();
        }

        video.addEventListener("canplay", () => snapNearLive(video));

        return tile;
    }

    function destroyTile(tile) {
        if (tile.driftTimer) {
            clearInterval(tile.driftTimer);
        }
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
                        tiles.set(stream.hls_url, createTile(stream));
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

