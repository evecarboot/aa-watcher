/* Dependency-free tests for aa_intel_watcher's chat JS.
 *
 * Runs intel_watcher_chat.js inside a vm sandbox against a minimal DOM
 * stub - no Node packages required:
 *
 *     node tests/js/chat.test.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CHAT_JS = path.join(
    __dirname, "..", "..", "aa_intel_watcher", "static",
    "aa_intel_watcher", "js", "intel_watcher_chat.js"
);

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
        this.value = "";
        this.scrollTop = 0;
        this.scrollHeight = 0;
        this.clientHeight = 0;
        this._attrs = {};
        this._listeners = {};
    }
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
    remove() {
        if (this.parentNode) {
            const i = this.parentNode.children.indexOf(this);
            if (i >= 0) { this.parentNode.children.splice(i, 1); }
            this.parentNode = null;
        }
    }
    addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
    dispatchEvent(evt) {
        evt.target = evt.target || this;
        evt.preventDefault = evt.preventDefault || (() => {});
        (this._listeners[evt.type] || []).slice().forEach((fn) => fn(evt));
    }
    setAttribute(k, v) { this._attrs[k] = String(v); }
    getAttribute(k) { return this._attrs[k]; }
}

function boot(initialStore, initialResponses) {
    const byId = {
        "iw-chat-messages": new El("div"),
        "iw-chat-form": new El("form"),
        "iw-chat-input": new El("input"),
        "iw-chat-status": new El("div"),
        "iw-chat-toggle": new El("button"),
    };
    const workspace = new El("div");
    workspace.className = "iw-workspace iw-workspace--chat";
    const store = initialStore || {};
    const intervals = [];
    const fetches = [];
    const responses = initialResponses || [];

    const harness = {
        byId,
        workspace,
        store,
        fetches,
        responses,
        queueResponse: (data) => responses.push(data),
        poll: async () => {
            const p = intervals.find((i) => i && i.ms === 3000);
            p.fn();
            await flush();
        },
        submit: () => {
            byId["iw-chat-form"].dispatchEvent({ type: "submit" });
        },
        clickToggle: () => {
            byId["iw-chat-toggle"].dispatchEvent({ type: "click" });
        },
    };

    const document = {
        createElement: (t) => new El(t),
        getElementById: (id) => byId[id] || null,
        querySelector: (sel) => (sel === ".iw-workspace" ? workspace : null),
        body: new El("body"),
    };

    const sandbox = {
        window: {
            INTEL_WATCHER: { chatUrl: "/chat/", csrfToken: "tok" },
        },
        document,
        console,
        localStorage: {
            getItem: (k) => (k in store ? store[k] : null),
            setItem: (k, v) => { store[k] = String(v); },
        },
        encodeURIComponent,
        fetch: (url, opts) => {
            fetches.push({ url, opts });
            const data = responses.length
                ? responses.shift()
                : { messages: [] };
            return Promise.resolve({
                ok: true,
                redirected: false,
                json: () => Promise.resolve(data),
            });
        },
        setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
        clearInterval: (id) => { intervals[id - 1] = null; },
        setTimeout: () => 0,
        navigator: {},
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(CHAT_JS, "utf8"), sandbox, {
        filename: "intel_watcher_chat.js",
    });
    return harness;
}

const flush = () =>
    new Promise((resolve) => setImmediate(resolve)).then(
        () => new Promise((resolve) => setImmediate(resolve))
    );

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
    await test("toggle hides the chat column and labels itself", async () => {
        const h = boot();
        await flush();
        const btn = h.byId["iw-chat-toggle"];
        h.clickToggle();
        assert(
            h.workspace.classList.contains("iw-chat-off"),
            "workspace marked chat-off"
        );
        assert(btn.textContent === "Show chat", "button says Show chat");
        assert(btn.getAttribute("aria-pressed") === "true", "aria-pressed");
        h.clickToggle();
        assert(!h.workspace.classList.contains("iw-chat-off"), "chat back");
        assert(btn.textContent === "Hide chat", "button says Hide chat");
    });

    await test("hidden preference persists via localStorage", async () => {
        const h = boot();
        h.clickToggle();
        assert(h.store["iw-chat-hidden"] === "1", "preference stored");
    });

    await test("previously-hidden preference applies on boot", async () => {
        const h = boot({ "iw-chat-hidden": "1" });
        await flush();
        assert(
            h.workspace.classList.contains("iw-chat-off"),
            "stored preference applied at boot"
        );
        assert(
            h.byId["iw-chat-toggle"].textContent === "Show chat",
            "button reflects stored preference"
        );
    });

    await test("chat polls since=0 then since=<lastId>", async () => {
        const h = boot({}, [{ messages: [{ id: 7, user: "a", message: "hi" }] }]);
        await flush(); // initial refresh()
        await h.poll();
        const urls = h.fetches.map((f) => f.url);
        assert(urls[0].includes("since=0"), "first poll since=0, got " + urls[0]);
        assert(
            urls[1].includes("since=7"),
            "second poll since=7, got " + urls[1]
        );
    });

    await test("messages render as text nodes only", async () => {
        const h = boot({}, [{
            messages: [{ id: 1, user: "<b>x</b>", message: "<img>" }],
        }]);
        await flush();
        const list = h.byId["iw-chat-messages"];
        assert(list.children.length === 1, "one row appended");
        const userSpan = list.children[0].children[0];
        assert(
            userSpan.textContent.includes("<b>x</b>"),
            "username kept verbatim (textContent, not HTML)"
        );
    });

    await test("POST sends urlencoded message with CSRF header", async () => {
        const h = boot();
        await flush();
        h.byId["iw-chat-input"].value = "hello world";
        h.submit();
        await flush();
        const post = h.fetches.find((f) => f.opts && f.opts.method === "POST");
        assert(post, "a POST fetch happened");
        assert(
            post.opts.headers["X-CSRFToken"] === "tok",
            "CSRF header sent"
        );
        assert(
            post.opts.body === "message=hello%20world",
            "urlencoded body, got " + post.opts.body
        );
    });

    if (failures) {
        console.log("\n" + failures + " test(s) failed");
        process.exit(1);
    }
    console.log("\nAll JS chat tests passed");
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
