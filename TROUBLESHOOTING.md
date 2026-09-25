# Intel Watcher — Troubleshooting Log

This document has two halves:

1. **Current diagnostics** — the staged failure-classification flow and
   checks that apply to any deployment following the README.
2. **Bug log** — historical incidents from the reference deployment where
   this plugin was developed, kept for context. Some describe behaviour
   that has since changed; each entry names its root cause and fix.

## Architecture recap

This describes the *reference deployment* the bug log below was gathered
on. The shipped `deploy/` samples differ slightly (see notes); always check
the README/samples for current recommended layout.

- **Alliance Auth** (`allianceauth_gunicorn` container, alias
  `intelwatcher-auth` on the `aa-docker_default` network) serves the Django
  app, including the `aa_intel_watcher` plugin (views, chat, webhook
  receivers). The alias exists because this deployment added it to dodge an
  underscore-hostname issue (Bug 2); the shipped samples just use the
  `allianceauth_gunicorn` service name directly - either works as long as
  it's in `ALLOWED_HOSTS`.
- **MediaMTX** (separate compose project `~/mediamtx`, joined to
  `aa-docker_default` via the external network `aa_shared`) receives RTMP
  streams from OBS and re-serves them as HLS on port `8888`. It calls back
  into Alliance Auth for publish auth via
  `authHTTPAddress: http://intelwatcher-auth:8000/intel-watcher/hooks/publish-auth/`
  and fires `runOnUnavailable` at the unpublish hook. *Shipped layout:*
  `deploy/docker-compose.mediamtx.yml` adds MediaMTX to the *same* compose
  project as Alliance Auth, so no external network is needed; if you run it
  as a separate project you must join both stacks to a shared external
  network (like `aa_shared` here).
- **nginx** (`aa-docker-nginx-1`) is the public-facing reverse proxy on port
  80, fronting both Alliance Auth (`location /`) and MediaMTX's HLS output
  (`location /hls/`), gated by an internal `auth_request` sub-check against
  Alliance Auth's **`hls_auth` endpoint** (`/intel-watcher/hls-auth/`).
  (The sub-check used to point at `api_status` - see Bug 11.)
- **Cloudflare** proxies `auth.example.com` (TLS terminates at Cloudflare;
  origin nginx only ever sees plain HTTP). `media.example.com` is **not**
  proxied by Cloudflare (direct DNS to the server) - the same split the
  README now prescribes for `INTEL_WATCHER_RTMP_HOST`. *Note:* at the time
  of these bugs this deployment also had MediaMTX's `:8888` published, so
  `http://media.example.com:8888/...` worked as a bypass sanity check. The
  shipped config deliberately keeps `:8888` internal-only - don't publish
  it; use a disposable container on the Docker network for direct HLS
  checks instead (see "General debugging techniques").

## OBS can't publish — staged diagnosis

An OBS "Failed to connect" can originate at four different boundaries.
Diagnose them in order - each stage that fails makes everything after it
irrelevant:

```
Nothing in MediaMTX logs when OBS tries
    -> DNS / proxy / firewall / TCP / wrong RTMP hostname
MediaMTX sees the connection but rejects the publish
    -> publish-auth route / ALLOWED_HOSTS / Docker networking
publish-auth reachable but still denied
    -> stream key / inactive user / can_stream
```

### Stage 1 - Does TCP/1935 reach the server?

From the machine running OBS:

```powershell
# Windows
Resolve-DnsName media.example.com
Test-NetConnection media.example.com -Port 1935
```

```bash
# Linux / macOS
nc -vz media.example.com 1935
```

If this fails, **AA-Watcher permissions and stream keys are irrelevant** -
OBS hasn't reached MediaMTX. Check, in order: DNS resolves to the right
server IP; the record isn't proxied through something that only forwards
HTTP(S) (see the Cloudflare note below); the host firewall/security group
allows 1935; the port is actually published:

```bash
# on the server
ss -lntp | grep 1935
docker ps --filter name=mediamtx
docker port mediamtx        # should show 1935 -> 0.0.0.0:1935
```

**Direct-IP isolation test:** point OBS at `rtmp://SERVER_PUBLIC_IP:1935/live`
with the same stream key. If direct IP works but `media.example.com` doesn't,
the problem is DNS/proxying/hostname resolution, not MediaMTX or Django.
This is a diagnostic only - go back to a proper hostname once identified;
raw-IP RTMP is not a recommended permanent setup.

### Cloudflare / reverse-proxy note

If your Alliance Auth hostname is proxied through a service that only
handles your HTTP/HTTPS traffic, use a separate direct hostname for
MediaMTX RTMP. For Cloudflare DNS this commonly means setting the
media/RTMP record to **DNS only** (grey cloud) rather than normal proxy
mode - unless you deliberately use a Cloudflare product/configuration that
proxies the required TCP service. Then set
`INTEL_WATCHER_RTMP_HOST = "media.example.com"` so streamers are handed the
direct hostname instead of the website's.

### Stage 2 - Did OBS reach MediaMTX?

```bash
docker logs --since=2m mediamtx
```

Run this, **then** click "Start Streaming" in OBS. If *nothing* new
appears, the failure is before MediaMTX - go back to Stage 1. Do not start
debugging Django permissions yet. (Real incident: a domain moved behind a
Cloudflare-proxied hostname; OBS died at connect time and both `mediamtx`
and `allianceauth_gunicorn` logs stayed completely silent - the request
never reached either service.)

If a connection/publish attempt *does* appear (MediaMTX logs
`is publishing to path 'live/...'` followed by an auth result or a
`closed:` line), continue.

### Stage 3 - MediaMTX -> Alliance Auth publish-auth

Watch both services while OBS tries:

```bash
docker logs --since=2m mediamtx
docker compose logs --since=2m allianceauth_gunicorn
```

MediaMTX logs the outcome of its `authHTTPAddress` call; gunicorn logs
`Intel Watcher: rejected publish ...` with a reason (never the key itself).

To test the internal route directly, curl the endpoint from inside the
Docker network. The shipped `mediamtx` image has curl, or use a disposable
container on the right network:

```bash
# inside the mediamtx container (shipped image includes curl):
docker exec mediamtx curl -sv -X POST \
  http://allianceauth_gunicorn:8000/intel-watcher/hooks/publish-auth/ \
  -H 'Content-Type: application/json' \
  -d '{"action":"read","protocol":"hls","path":"live/x"}'

# or from a scratch container - find the network first:
docker inspect mediamtx --format '{{json .NetworkSettings.Networks}}'
docker run --rm --network <that-network> curlimages/curl \
  curl -sv -X POST \
  http://allianceauth_gunicorn:8000/intel-watcher/hooks/publish-auth/ \
  -H 'Content-Type: application/json' \
  -d '{"action":"read","protocol":"hls","path":"live/x"}'
```

This payload is a **reachability test**, not a valid-stream test - a
`read`/`hls` action deliberately returns 200 without credentials, proving
the route, the Host header and the network path end-to-end. Interpreting
the response:

| Response | Meaning |
| --- | --- |
| `200`/`2xx` | Route reachable, webhook answered. Networking is fine - a real `publish` denied here is Stage 4. |
| `400` | `DisallowedHost` (check gunicorn logs) - the `authHTTPAddress` hostname isn't in `ALLOWED_HOSTS`. Add `allianceauth_gunicorn` (or your alias) to it. Also possible: malformed request. |
| `401`/`403` | Route reachable, auth *rejected* - e.g. a `publish` action with a bogus key. Go to Stage 4. |
| `404` | The three direct routes were never added to the project `urls.py` (README step 5 / `deploy/urls.py`). |
| `302` | The hooks are registered via the app's `url_hook` and wrapped in `login_required` - move them to the project `urls.py` (Bug 1). |
| timeout | Docker network/hostname/service problem - MediaMTX can't reach the Django container at all. See below. |
| connection refused | Hostname resolved but nothing listens there - wrong service/port, or gunicorn down. |

**Docker networking:** MediaMTX and `allianceauth_gunicorn` must share a
network and the `authHTTPAddress` hostname must resolve. The shipped
overlay puts `mediamtx` in the same compose project, so this is automatic.
If you run MediaMTX as a *separate* compose project, join both stacks to a
shared external network (e.g. `docker network create aa_shared` +
`external: true` on both projects' networks) - the reference deployment in
the architecture recap does this. Verify with the `docker inspect` command
above: both containers should appear on the same network name.

If nginx's `auth_request` for `/hls/` fails rather than publish-auth, run
the same curl against `/intel-watcher/hls-auth/` from the `nginx`
container's network - a 401 there is expected for anonymous (the endpoint
is working), a 302/404/500 means wiring problems as above.

### Stage 4 - Key, user and permission

Only after the webhook is proven reachable: a publish needs a valid stream
key whose owner is an *active* user with `aa_intel_watcher.can_stream`.
Check without printing the key:

```bash
docker compose exec allianceauth_gunicorn python manage.py shell -c "
from aa_intel_watcher.models import StreamKey
for s in StreamKey.objects.select_related('user'):
    u = s.user
    print(u.username, '| active:', u.is_active,
          '| can_stream:', u.has_perm('aa_intel_watcher.can_stream'),
          '| is_live:', s.is_live)
"
```

- `can_stream: False` -> grant the permission in `/admin/` (group/state).
- `active: False` -> re-enable the account.
- Also confirm the streamer is using the key shown on *their* Streamer Info
  page right now - a regenerated key invalidates the old one instantly.

## Bug 1 — `is_live` never flips to `True`

**Symptom:** Streamer goes live in OBS, MediaMTX accepts the stream fine, but
the web UI never shows the video tile / `is_live` stays `False` in the DB.

**Root cause:** The publish/unpublish webhook views were registered via
Alliance Auth's `url_hook` mechanism, which wraps every registered URL in
`login_required`. MediaMTX's webhook POST has no Django session cookie, so
it was silently redirected to `/account/login/`. MediaMTX's Go HTTP client
followed the redirect, got a `200` (the login page HTML), and treated that
as "auth succeeded" — so publishing always appeared to work, but our actual
view (which sets `is_live = True`) never ran.

**Fix:** Register the webhook URLs directly in `conf/urls.py` (bypassing the
`login_required`-wrapping `url_hook`), protected instead by the
`X-Webhook-Secret` header check already in the view.

## Bug 2 — `_auth_check` subrequest fails with `DisallowedHost`

**Symptom:** nginx's internal `auth_request` to gate `/hls/` intermittently
failed with Django raising `DisallowedHost`.

**Root cause:** nginx's `_auth_check` location proxied to
`http://allianceauth_gunicorn:8000/...` — an underscore in a hostname is not
a valid label component. Docker Compose's default network only reliably
gives containers hostnames without underscores; the DNS resolution either
failed or produced a `Host` header Django's `ALLOWED_HOSTS` didn't expect.

**Fix:** Added an explicit network alias `intelwatcher-auth` (no
underscores) to the `allianceauth_gunicorn` service and pointed nginx's
`proxy_pass` at that alias instead. Confirmed `ALLOWED_HOSTS` includes
`intelwatcher-auth`.

## Bug 3 — HLS manifest redirect loses the `/hls/` prefix and scheme

**Symptom:** Video tile rendered, but playback never started. Browser
Network tab showed a request to MediaMTX's `.m3u8` following a redirect to
a 404, or (once partially fixed) a "Mixed Content" console error blocking an
`http://` URL on the `https://` page.

**Root cause:** MediaMTX implements a "cookieCheck" session-establishment
step: the first request to an HLS path gets a `302` with a **relative**
`Location: /live/<key>/index.m3u8?cookieCheck=1` header (no scheme, no
`/hls/` prefix — this is normal MediaMTX behavior, confirmed by curling
MediaMTX directly, bypassing nginx). The browser resolved that relative
redirect against the *current page's* origin, landing on
`https://auth.example.com/live/...` (missing the `/hls/` prefix nginx uses
to route to MediaMTX), which Django doesn't route → 404. An intermediate
fix attempt correctly added the `/hls/` prefix but kept `http://`, which the
browser then blocked as mixed content on the HTTPS page.

**Fix:** Rewrite MediaMTX's redirect in nginx with a regex-based
`proxy_redirect` in the `location /hls/` block:

```nginx
location /hls/ {
    auth_request /intel-watcher/_auth_check/;
    proxy_pass http://mediamtx:8888/;
    proxy_redirect ~^/(.*)$ https://$host/hls/$1;
}
```

This rewrites *any* relative `Location` header from MediaMTX into a fully
qualified `https://<host>/hls/...` URL.

## Bug 4 — nginx kept ignoring config edits (the real time-sink)

**Symptom:** After drafting the `proxy_redirect` fix above, *nothing*
changed no matter which syntax variant was tried (path-only, multi-rule,
single-rule, regex). `nginx -t` and `nginx -s reload` always reported
success, and `cat` on the host file always showed the intended edits saved
correctly — yet a fresh `curl -v` against the live site always showed the
exact same original, unmodified `Location` header.

**Root cause:** The nginx service bind-mounts a **single file**, not a
directory:

```yaml
volumes:
  - ./conf/nginx.conf:/etc/nginx/nginx.conf
```

Editing the host file with `sed -i` (and many editors) doesn't modify the
file in place — it writes a new temp file and **renames it over** the
original path, which creates a **new inode**. Docker's bind mount for a
single file is attached to the original inode at container-start time, so
the running container kept serving the stale content from the old inode
indefinitely. `nginx -s reload` reloads from that same stale bind — it
can't fix a broken mount.

**How it was found:** Comparing `cat ~/aa-docker/conf/nginx.conf` (host,
showed the latest edit) against
`docker compose exec nginx cat /etc/nginx/nginx.conf` (container, showed
older/different content) proved the mount was out of sync.

**Fix:** `docker compose up -d --force-recreate nginx` — recreates the
container so the bind mount re-attaches to the current file. Confirmed by
re-running the `docker compose exec ... cat` check until it matched the
host file.

**Takeaway for future config edits:** After editing any single-file
bind-mounted config (`nginx.conf`, etc.), don't just `reload`/`restart` —
run `docker compose up -d --force-recreate <service>`, or verify with
`docker compose exec <service> cat <path>` before assuming a fix "isn't
working" and iterating on syntax.

## Bug 5 — Video tile too small / autoplay unreliable

**Symptom:** Once playback worked, the single video tile rendered small
(fixed `col-md-6` grid sizing meant for multiple simultaneous streams), and
autoplay sometimes didn't kick in.

**Fix** (`aa_intel_watcher/static/aa_intel_watcher/js/intel_watcher_viewer.js`):
- Tiles now size `col-12` (full width) when there's only one live stream,
  and `col-12 col-md-6` when there are multiple.
- `video.play()`'s returned promise is now handled explicitly, with a retry
  on `loadedmetadata` if the initial `play()` call is rejected.

## Deployment loop

The server's `~/aa-docker/custom.dockerfile` builds from a local clone at
`~/aa-docker/aa-watcher/` (not a pip/git install inside the image). After
committing and pushing changes locally:

```bash
cd ~/aa-docker/aa-watcher && git pull
cd ~/aa-docker
docker compose build --no-cache
docker compose up -d --force-recreate
```

Verify `is_live` state directly via Django shell (doesn't print the key
itself):

```bash
docker compose exec allianceauth_gunicorn python manage.py shell -c \
  "from aa_intel_watcher.models import StreamKey; [print(s.user, s.is_live, s.last_seen) for s in StreamKey.objects.all()]"
```

## Bug 6 - DisallowedHost on the publish-auth webhook (repo-level fix)

**Symptom:** TCP/1935 reachable, OBS instantly rejected.

**Root cause:** the shipped Docker config pointed `authHTTPAddress` at
`http://allianceauth_gunicorn:8000/...`. MediaMTX sends
`Host: allianceauth_gunicorn` on that request, and if ALLOWED_HOSTS doesn't
contain it Django answers `400 DisallowedHost`, which MediaMTX treats as
"auth denied". The alias fix from Bug 2 was documented but never applied
to the shipped configs, and ALLOWED_HOSTS was never documented.

**Fix:** nginx `_auth_check`/`hls-auth` proxies now send
`proxy_set_header Host $host`, and the README/mediamtx comments spell out the
ALLOWED_HOSTS requirement (add `allianceauth_gunicorn`, or an alias like
`intelwatcher-auth`, in local.py).

## Bug 7 - runOnUnavailable could never run in the Docker image

**Symptom:** streams never went offline in the Docker deployment -
`is_live` stuck `True` forever after every broadcast.

**Root cause:** `bluenviron/mediamtx` is a `scratch` image containing only
the mediamtx binary - no shell, no curl, no wget. The `runOnUnavailable:
curl ...` hook exited instantly with `exec: "curl": executable file not
found`.

**Fix:** `deploy/Dockerfile.mediamtx` builds `alpine + curl + mediamtx`
(pinned to a tested version via `MEDIAMTX_VERSION`) and the compose overlay
uses it. Bonus: `curl --max-time 10` so a hung Alliance Auth can't wedge
the hook, and `source_id=$MTX_SOURCE_ID` is sent so the app can ignore
stale callbacks after a fast reconnect (see Bug 8).

## Bug 8 - unpublish races and stale is_live

**Symptom:** streamer reconnects quickly and the page flips them offline
mid-stream, or a stream stays "live" forever after a key regeneration.

**Root cause:** the unpublish webhook matched `StreamKey.key` against the
path's last segment. Two failure modes:

* publish A -> drop -> publish B (same key) -> delayed unpublish from A
  arrives *after* B -> `is_live=False` while B is actually live.
* regenerate key while streaming: the stream keeps broadcasting under the
  OLD path (`live/<old-key>`), but the unpublish for it matched on `key`
  which now holds the new value -> nothing updated, `is_live` stuck `True`,
  and `api_status` pointed viewers at the new (dead) path.

**Fix:** `StreamKey` now records `live_key` (the key MediaMTX accepted for
the current session) and `live_session_id` (MediaMTX's connection UUID -
the auth request `id`, also sent as `$MTX_SOURCE_ID` to the hook). An
unpublish callback whose `source_id` doesn't match the live session is
ignored, and the lookup matches `key OR live_key` so a rotated key still
clears. `api_status` serves `active_path_name` so viewers keep getting the
URL that's actually broadcasting after a mid-stream regen.

## Bug 9 - pip-installed package had no templates or static files

**Symptom:** `pip install git+...` succeeded but every page raised
`TemplateDoesNotExist` and no JS/CSS was served.

**Root cause:** `pyproject.toml` declared no `package-data`, so the wheel
contained only `.py` files. Proven by building the wheel and listing its
contents.

**Fix:** `[tool.setuptools.package-data]` + `MANIFEST.in` - the wheel now
contains templates, CSS and JS.

## Bug 10 - bare-metal MediaMTX config exposed HLS (and RTSP/WebRTC) publicly

**Symptom:** on a bare-metal install following the old sample, anyone on
the internet could fetch `http://<host>:8888/live/<key>/index.m3u8` or pull
`rtsp://<host>:8554/live/<key>` and watch streams without an Alliance Auth
session - the auth endpoint returned 200 for every non-`publish` action.

**Fix:** `hlsAddress` is now `127.0.0.1:8888`, unused protocols
(`rtsp`, `webrtc`, `srt`) are explicitly disabled, and the auth endpoint
denies `read`/`playback` on non-HLS protocols so a leaked stream key can't
be used for a direct RTMP pull.

## Bug 11 - anonymous HLS requests produced 500s

**Symptom:** auth_request sub-check hit `api_status`, which is wrapped in
`login_required` -> 302 to the login page -> auth_request maps non-2xx,
non-401/403 to 500. Logged-out users got 500 instead of 401/403, and every
segment request ran a pointless DB query.

**Fix:** new `hls_auth` view (registered in the project urls.py next to the
webhooks) returns bare `204`/`401` with no DB access; both nginx samples
now point `_auth_check` at `/intel-watcher/hls-auth/` and forward the real
`Host` header.

## Bug 12 - publish-auth logged credentials and could 500 on bad input

**Root cause:** the view logged the entire request payload - including the
stream key and any OBS `user`/`password` credentials - into application
logs. Non-dict JSON or a non-string `action` (e.g. `{"action": ["x"]}`)
raised TypeError -> HTTP 500. Inactive users could still publish.

**Fix:** payload is type-checked, bodies are size-capped, the stream key
and credentials are never logged (decisions are logged by username +
MediaMTX connection id), unknown actions are denied, and deactivated users
are rejected even if they still hold `can_stream`.

## Bug 13 - chat had no UI and paginated wrong

**Root cause:** the chat API existed but no template/JS ever called it, and
`order_by("created_at")[:200]` with `id > since` could skip messages around
same-timestamp boundaries; `since=0` returned the *oldest* 200 messages
ever rather than the recent backlog.

**Fix:** a chat box + `intel_watcher_chat.js` now live on the viewer page
(textContent-only rendering, CSRF header on POST); the endpoint orders by
`id` and returns the newest 50 for `since=0`.

## Bug 14 - stale "live" tiles after a missed webhook

**Symptom:** MediaMTX restarts or the unpublish curl fails -> stream shows
live forever.

**Fix:** set `INTEL_WATCHER_HLS_INTERNAL_URL` (`http://127.0.0.1:8888` bare
metal, `http://mediamtx:8888` Docker) and `api_status` will probe each live
stream's playlist - a definitive 404 (with a 15s grace period after
publish) clears `is_live`. Unreachable MediaMTX never flips state.

## Bug 15 - `fetch_hls_js` PermissionError in a vendored Docker install

**Symptom:** `python manage.py fetch_hls_js --force` (and later
`collectstatic` on the same files) crashed with `PermissionError` creating
`.../static/aa_intel_watcher/js/hls.min.js.tmp`, inside a custom Alliance
Auth image that vendors the plugin:

```dockerfile
COPY aa-watcher/aa_intel_watcher /home/allianceauth/.local/lib/python3.12/site-packages/aa_intel_watcher
```

**Root cause:** `COPY` produces root-owned files, but the container runs as
`allianceauth`. `fetch_hls_js` writes into the package's own static folder,
which the worker can't write to.

**Fix:** own the files at copy time:

```dockerfile
COPY --chown=allianceauth:allianceauth aa-watcher/aa_intel_watcher /home/allianceauth/.local/lib/python3.12/site-packages/aa_intel_watcher
```

Applies to any vendored/manual install - the pip-install path can't hit
this because pip installs with the right ownership. Bare-metal equivalent:
run `fetch_hls_js` as the same user gunicorn runs as, or `chown -R` the
package dir.

## General debugging techniques that worked well

- **Bypass nginx entirely** to get ground truth from a service: spin up a
  disposable container on the same Docker network and `curl` the target
  service directly, e.g.
  ```bash
  docker run --rm --network aa-docker_default curlimages/curl \
    curl -v http://mediamtx:8888/live/<key>/index.m3u8
  ```
- **Verify config actually loaded inside the container**, not just on the
  host filesystem: `docker compose exec <service> cat <config-path>`.
  A successful `-t`/`reload`/`restart` does **not** prove the running
  process is using the file you think it is.
- Remember nginx's `auth_request` only treats `2xx` as allow and `401`/`403`
  as deny — any other status code from the subrequest (e.g. a `302` from an
  unrelated redirect) is treated as an upstream error and turns into a
  `500` for the real client request.
- Distinguish Cloudflare-proxied vs. direct-DNS subdomains when debugging
  scheme/TLS issues — an origin server behind Cloudflare's proxy only ever
  sees plain HTTP even though the public URL is HTTPS.
