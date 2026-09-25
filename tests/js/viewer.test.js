/* Dependency-free tests for aa_intel_watcher's viewer JS.
 *
 * Runs intel_watcher_viewer.js inside a vm sandbox against a minimal DOM
 * stub - no Node packages required:
 *
 *     node tests/js/viewer.test.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const VIEWER = path.join(
    __dirname, "..", "..", "aa_intel_watcher", "static",
    "aa_intel_watcher", "js", "intel_watcher_viewer.js"
);

// ---------------------------------------------------------------------------
// Minimal DOM stub - just what the viewer touches.
// ---------------------------------------------------------------------------
class ClassList {
    constructor() { this._set = new Set(); }
    add(c) { this._set.add(c); }
    remove(c) { this._set.delete(c); }
    toggle(c, force) {
        const on = force === undefined ? !this._set.has(c) : force;
        if (on) { this._set.add(c); } else { this._set.delete(c); }
        return on;
    }
    contains(c) { return this._set.has(c); }
}

class El {
    constructor(tag) {
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.classList = new ClassList();
        this.className = "";
        this.style = {};
        this.dataset = {};
        this.textContent = "";
        this.removed = false;
        this._attrs = {};
        this._listeners = {};

        if (this.tagName === "VIDEO") {
            this.paused = true;
            this.readyState = 0;
            this.muted = false;
            this.seekable = { length: 0 };
            this._playCalls = 0;
            this.play = () => {
                this._playCalls += 1;
                this.paused = false;
                return Promise.resolve();
            };
            this.pause = () => { this.paused = true; };
            this.load = () => { this._loadCalls = (this._loadCalls || 0) + 1; };
            this.canPlayType = () => "";
        }
        if (this.tagName === "BUTTON") {
            this.focus = () => { this._focused = true; };
        }
    }
    appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
    }
    remove() {
        this.removed = true;
        if (this.parentNode) {
            const i = this.parentNode.children.indexOf(this);
            if (i >= 0) { this.parentNode.children.splice(i, 1); }
            this.parentNode = null;
        }
    }
    addEventListener(type, fn) {
        (this._listeners[type] = this._listeners[type] || []).push(fn);
    }
    dispatchEvent(evt) {
        evt.target = evt.target || this;
        (this._listeners[evt.type] || []).slice().forEach((fn) => fn(evt));
    }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) { return this._attrs[k]; }
    removeAttribute(k) { delete this._attrs[k]; }
    querySelectorAll() { return []; }
    // Depth-first search helpers for tests.
    findAll(pred, out = []) {
        this.children.forEach((c) => {
            if (pred(c)) { out.push(c); }
            c.findAll(pred, out);
        });
        return out;
    }
}

class FakeHls {
    constructor(cfg) {
        this.cfg = cfg;
        this.destroyed = false;
        this._handlers = {};
        FakeHls.instances.push(this);
    }
    on(ev, fn) { (this._handlers[ev] = this._handlers[ev] || []).push(fn); }
    loadSource(url) { this.source = url; }
    attachMedia(video) { this.media = video; }
    startLoad() { this._startLoads = (this._startLoads || 0) + 1; }
    recoverMediaError() {}
    destroy() { this.destroyed = true; }
}
FakeHls.instances = [];
FakeHls.isSupported = () => true;
FakeHls.Events = {
    ERROR: "hlsError",
    MEDIA_ATTACHED: "mediaAttached",
    MANIFEST_PARSED: "manifestParsed",
};
FakeHls.ErrorTypes = {
    NETWORK_ERROR: "networkError",
    MEDIA_ERROR: "mediaError",
};

// Boots the viewer in a fresh sandbox. `status` is read on every poll.
function boot() {
    FakeHls.instances = []; // per-boot isolation
    const documentListeners = {};
    const grid = new El("div");
    const emptyState = new El("div");
    const byId = {
        "iw-video-grid": grid,
        "iw-empty-state": emptyState,
    };
    const document = {
        createElement: (tag) => new El(tag),
        getElementById: (id) => byId[id] || null,
        body: new El("body"),
        addEventListener: (type, fn) => {
            (documentListeners[type] = documentListeners[type] || []).push(fn);
        },
    };
    const intervals = [];
    const harness = {
        status: { streams: [] },
        document,
        grid,
        emptyState,
        keydown: (key) =>
            (documentListeners.keydown || []).forEach((fn) => fn({ key })),
        poll: async () => {
            const statusPoll = intervals.find((i) => i && i.ms === 3000);
            statusPoll.fn();
            await flush();
        },
        flushAll: () => flush(),
    };

    const sandbox = {
        window: { INTEL_WATCHER: { statusUrl: "/status" }, Hls: FakeHls },
        document,
        console,
        fetch: () =>
            Promise.resolve({
                ok: true,
                redirected: false,
                json: () => Promise.resolve(harness.status),
            }),
        setInterval: (fn, ms) => {
            intervals.push({ fn, ms });
            return intervals.length;
        },
        clearInterval: (id) => { intervals[id - 1] = null; },
        setTimeout: () => 0,
        clearTimeout: () => {},
        navigator: {},
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(VIEWER, "utf8"), sandbox, {
        filename: "intel_watcher_viewer.js",
    });
    return harness;
}

const flush = () =>
    new Promise((resolve) => setImmediate(resolve)).then(
        () => new Promise((resolve) => setImmediate(resolve))
    );

function liveStream(name, url) {
    return { display_name: name, hls_url: url };
}
const URL_A = "/hls/live/key-a/index.m3u8";
const URL_B = "/hls/live/key-b/index.m3u8";

function getTileEl(h, idx) { return h.grid.children[idx]; }
function getTileButtons(h, idx) {
    return h.grid.children[idx].findAll(
        (c) => c.tagName === "BUTTON"
    );
}
function getVideo(h, idx) {
    return h.grid.children[idx].findAll(
        (c) => c.tagName === "VIDEO"
    )[0];
}

// ---------------------------------------------------------------------------
// Test runner
// ---------------------------------------------------------------------------
let failures = 0;
async function test(name, fn) {
    try {
        await fn();
        console.log("PASS " + name);
    } catch (err) {
        failures += 1;
        console.log("FAIL " + name + "\n     " + (err && err.stack || err));
    }
}
function assert(cond, msg) {
    if (!cond) { throw new Error("assertion failed: " + msg); }
}

async function main() {
    await test("status adds a tile with video + buttons", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        assert(h.grid.children.length === 1, "one tile in grid");
        assert(getVideo(h, 0), "video element exists");
        const btns = getTileButtons(h, 0);
        assert(btns.length === 2, "solo + tabfs buttons, got " + btns.length);
        assert(FakeHls.instances.length === 1, "one Hls instance");
        assert(
            FakeHls.instances[0].source === URL_A,
            "Hls loaded the stream URL"
        );
    });

    await test("empty status removes the tile and destroys Hls", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        const hls = FakeHls.instances[0];
        h.status = { streams: [] };
        await h.poll();
        assert(h.grid.children.length === 0, "tile removed from grid");
        assert(tile.removed, "tile node detached");
        assert(hls.destroyed, "hls.destroy() called");
        assert(
            !h.emptyState.classList.contains("d-none"),
            "empty state visible again"
        );
    });

    await test("repeat polls never duplicate tiles or Hls instances", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        await h.poll();
        await h.poll();
        assert(h.grid.children.length === 1, "still one tile");
        assert(FakeHls.instances.length === 1, "still one Hls");
    });

    await test("stop/start cycles recreate a fresh tile + Hls", async () => {
        const h = boot();
        for (let i = 0; i < 3; i += 1) {
            h.status = { streams: [liveStream("Alice", URL_A)] };
            await h.poll();
            h.status = { streams: [] };
            await h.poll();
        }
        assert(h.grid.children.length === 0, "no stale tile");
        assert(FakeHls.instances.length === 3, "one Hls per session");
        FakeHls.instances.forEach((hls) =>
            assert(hls.destroyed, "every old Hls destroyed")
        );
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.poll();
        assert(h.grid.children.length === 1, "reconnect shows tile again");
    });

    await test("Tab fullscreen enters, locks scroll, and exits via button", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        const tabfsBtn = getTileButtons(h, 0)[1];

        tabfsBtn.dispatchEvent({ type: "click" });
        assert(tile.classList.contains("iw-tabfs"), "tile overlay class on");
        assert(
            h.document.body.classList.contains("iw-tabfs-lock"),
            "body scroll locked"
        );
        assert(
            tabfsBtn.textContent === "Exit tab fullscreen",
            "button becomes exit control"
        );
        assert(tabfsBtn.getAttribute("aria-pressed") === "true", "aria-pressed");

        tabfsBtn.dispatchEvent({ type: "click" });
        assert(!tile.classList.contains("iw-tabfs"), "overlay class off");
        assert(
            !h.document.body.classList.contains("iw-tabfs-lock"),
            "scroll restored"
        );
    });

    await test("Escape exits tab fullscreen", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        getTileButtons(h, 0)[1].dispatchEvent({ type: "click" });
        assert(tile.classList.contains("iw-tabfs"), "entered tabfs");
        h.keydown("Escape");
        assert(!tile.classList.contains("iw-tabfs"), "Escape exits");
        assert(!h.document.body.classList.contains("iw-tabfs-lock"), "unlock");
    });

    await test("stream ending while tab fullscreen exits cleanly", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        getTileButtons(h, 0)[1].dispatchEvent({ type: "click" });
        assert(tile.classList.contains("iw-tabfs"), "in tabfs");
        h.status = { streams: [] };
        await h.poll();
        assert(h.grid.children.length === 0, "tile gone");
        assert(
            !h.document.body.classList.contains("iw-tabfs-lock"),
            "no stuck overlay/scroll lock"
        );
    });

    await test("second tile entering tabfs evicts the first", async () => {
        const h = boot();
        h.status = {
            streams: [liveStream("A", URL_A), liveStream("B", URL_B)],
        };
        await h.flushAll();
        const tileA = getTileEl(h, 0);
        const tileB = getTileEl(h, 1);
        getTileButtons(h, 0)[1].dispatchEvent({ type: "click" });
        getTileButtons(h, 1)[1].dispatchEvent({ type: "click" });
        assert(!tileA.classList.contains("iw-tabfs"), "A no longer tabfs");
        assert(tileB.classList.contains("iw-tabfs"), "B is tabfs");
    });

    await test("multi -> single layout restores after tile removal", async () => {
        const h = boot();
        h.status = {
            streams: [liveStream("A", URL_A), liveStream("B", URL_B)],
        };
        await h.flushAll();
        assert(
            getTileEl(h, 0).style.flexBasis === "24rem",
            "two streams -> 24rem basis"
        );
        // B tabfs, then A stops, then exit: B must be single-stream layout.
        const tileB = getTileEl(h, 1);
        getTileButtons(h, 1)[1].dispatchEvent({ type: "click" });
        h.status = { streams: [liveStream("B", URL_B)] };
        await h.poll();
        getTileButtons(h, 0)[1].dispatchEvent({ type: "click" });
        assert(
            tileB.style.flexBasis === "100%",
            "single stream -> full width, got " + tileB.style.flexBasis
        );
    });

    await test("tabfs does not touch muted/playing state", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const video = getVideo(h, 0);
        video.muted = false;
        video.dispatchEvent({ type: "canplay" }); // simulates playback start
        getTileButtons(h, 0)[1].dispatchEvent({ type: "click" });
        assert(video.muted === false, "muted state preserved");
        assert(video.paused === false, "playback uninterrupted");
    });

    await test("double-click video toggles tab fullscreen", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        const video = getVideo(h, 0);
        video.dispatchEvent({ type: "dblclick" });
        assert(tile.classList.contains("iw-tabfs"), "dblclick entered");
        video.dispatchEvent({ type: "dblclick" });
        assert(!tile.classList.contains("iw-tabfs"), "dblclick exited");
    });

    await test("name refresh updates the tile without rebuilding it", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Old", URL_A)] };
        await h.flushAll();
        const tile = getTileEl(h, 0);
        h.status = { streams: [liveStream("New", URL_A)] };
        await h.poll();
        assert(h.grid.children[0] === tile, "same tile element");
        assert(
            tile.findAll((c) => c.className === "iw-tile-name")[0]
                .textContent === "New",
            "name updated"
        );
        assert(FakeHls.instances.length === 1, "no extra Hls");
    });

    await test("dead tile does not come back on later polls", async () => {
        const h = boot();
        h.status = { streams: [liveStream("Alice", URL_A)] };
        await h.flushAll();
        h.status = { streams: [] };
        await h.poll();
        await h.poll();
        await h.poll();
        assert(h.grid.children.length === 0, "stays empty");
    });

    if (failures) {
        console.log("\n" + failures + " test(s) failed");
        process.exit(1);
    }
    console.log("\nAll JS viewer tests passed");
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
