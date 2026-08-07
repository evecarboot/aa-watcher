(function () {
    "use strict";

    const cfg = window.INTEL_WATCHER;
    const regenBtn = document.getElementById("iw-regen-key");
    const keyEl = document.getElementById("iw-stream-key");

    if (regenBtn) {
        regenBtn.addEventListener("click", () => {
            if (!confirm("Regenerate your stream key? You'll need to update OBS.")) {
                return;
            }
            fetch(cfg.regenerateUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": cfg.csrfToken }
            })
                .then((r) => r.json())
                .then((data) => {
                    keyEl.textContent = data.stream_key;
                });
        });
    }

    document.querySelectorAll(".iw-copy").forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = document.getElementById(btn.dataset.target);
            if (!target) {
                return;
            }
            navigator.clipboard.writeText(target.textContent.trim()).then(() => {
                const original = btn.textContent;
                btn.textContent = "Copied!";
                setTimeout(() => (btn.textContent = original), 1500);
            });
        });
    });
})();
