# Intel Watcher — Troubleshooting Log

This document records the bugs found and fixed while getting live HLS video
working end-to-end at `https://auth.banr.online/intel-watcher/`, along with
the debugging techniques that found them. Keep it updated as new issues are
found.

## Architecture recap

- **Alliance Auth** (`allianceauth_gunicorn` container, alias `intelwatcher-auth`
  on the `aa-docker_default` network) serves the Django app, including the
  `aa_intel_watcher` plugin (views, chat, webhook receivers).
- **MediaMTX** (separate compose project `~/mediamtx`, joined to
  `aa-docker_default` via the external network `aa_shared`) receives RTMP
  streams from OBS and re-serves them as HLS on port `8888`. It calls back
  into Alliance Auth for publish/unpublish auth via
  `authHTTPAddress: http://intelwatcher-auth:8000/intel-watcher/hooks/publish-auth/`.
- **nginx** (`aa-docker-nginx-1`) is the public-facing reverse proxy on port 80,
  fronting both Alliance Auth (`location /`) and MediaMTX's HLS output
  (`location /hls/`), gated by an internal `auth_request` sub-check against
  Alliance Auth's `api_status` view.
- **Cloudflare** proxies `auth.banr.online` (TLS terminates at Cloudflare;
  origin nginx only ever sees plain HTTP). `media.banr.online` is **not**
  proxied by Cloudflare (direct DNS to the droplet), which is why direct HLS
  playback at `http://media.banr.online:8888/...` always worked and was
  useful as a "known-good" sanity check throughout this debugging.

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
`https://auth.banr.online/live/...` (missing the `/hls/` prefix nginx uses
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

Verify `is_live` state directly via Django shell:

```bash
docker compose exec allianceauth_gunicorn python manage.py shell -c \
  "from aa_intel_watcher.models import StreamKey; [print(s.id, s.user_id, s.is_live, s.last_seen, s.path_name) for s in StreamKey.objects.all()]"
```

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
