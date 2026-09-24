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
            regenBtn.disabled = true;
            fetch(cfg.regenerateUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-CSRFToken": cfg.csrfToken }
            })
                .then((r) => {
                    if (r.redirected || !r.ok) {
                        throw new Error("request failed");
                    }
                    return r.json();
                })
                .then((data) => {
                    keyEl.textContent = data.stream_key;
                })
                .catch(() => {
                    alert("Could not regenerate the key - refresh the page and try again.");
                })
                .finally(() => {
                    regenBtn.disabled = false;
                });
        });
    }

    document.querySelectorAll(".iw-copy").forEach((btn) => {
        btn.addEventListener("click", () => {
            const target = document.getElementById(btn.dataset.target);
            if (!target) {
                return;
            }
            const text = target.textContent.trim();
            const done = () => {
                const original = btn.textContent;
                btn.textContent = "Copied!";
                setTimeout(() => (btn.textContent = original), 1500);
            };
            const failed = () => {
                const original = btn.textContent;
                btn.textContent = "Select & copy manually";
                setTimeout(() => (btn.textContent = original), 2000);
            };
            // navigator.clipboard is unavailable on plain-HTTP origins
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(done).catch(failed);
            } else {
                failed();
            }
        });
    });
})();
