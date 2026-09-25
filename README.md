# AA-Watcher

An [Alliance Auth](https://gitlab.com/allianceauth/allianceauth) app that adds a
corp/alliance-only "Intel Watcher" section: members can stream from OBS to it,
and it is viewable only by logged-in Alliance Auth users with the right
permission - no YouTube/Twitch, no public exposure.

Requires Alliance Auth **5.x** (tested against 5.2).

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Alliance Auth configuration](#alliance-auth-configuration)
- [Permissions](#permissions)
- [MediaMTX setup](#mediamtx-setup)
- [nginx setup](#nginx-setup)
- [OBS / streamer setup](#obs--streamer-setup)
- [Updating](#updating)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [Licence](#licence)

## Features

- Adds one sidebar entry ("Intel Watcher") with two tabs:
  - **Intel Viewing** - a grid of every currently-live stream (one tile per
    streamer) plus chat. This is the page everyone with `basic_access` lands on.
  - **Streamer Info** - OBS server/key details and the "regenerate key" button.
    Only shown/reachable to users with `can_stream`.
- **Streaming server:** [MediaMTX](https://github.com/bluenviron/mediamtx) - a
  single binary, no separate database or website. Accepts RTMP from OBS, serves
  HLS for browser playback.
- **Who can push a stream (`can_stream` permission):** managed the normal
  Alliance Auth way, via groups/states in the admin.
- **Auto-show whoever is live:** MediaMTX calls a webhook in this app on
  publish/unpublish; the page polls a small JSON endpoint and swaps the player
  to whichever approved streamer is currently live.
- **Chat:** plain polling AJAX chat stored in the Alliance Auth database. No
  websockets/Channels/Redis pub-sub required. Sits beside the stream grid on
  wide windows and stacks below it on narrow ones; admins can disable it
  site-wide and viewers can hide it per-browser.
- **Who can view (`basic_access` permission):** gates both the page and, via the
  nginx sample, the actual video segments - not just the UI around them.
- **Multiple simultaneous streamers:** each live streamer gets their own tile
  with independent native video controls. A "Solo audio" button on each tile
  mutes every other tile.
- **Tab fullscreen:** each tile has a *Tab fullscreen* button (double-click
  works too) that expands the stream to fill the whole browser tab without
  invoking browser/OS fullscreen - tabs and the address bar stay visible.
  Escape or *Exit tab fullscreen* leaves it; native video fullscreen remains
  available alongside it. If the stream stops while expanded, the overlay
  closes itself.
- **Theming:** every template extends `allianceauth/base-bs5.html` and only uses
  standard Bootstrap classes, so the app automatically matches whatever
  Bootswatch theme a user has selected in Alliance Auth - nothing to configure.

## Requirements

- Alliance Auth **5.x** (tested against 5.2).
- Python **>= 3.10**.
- [MediaMTX](https://github.com/bluenviron/mediamtx) - the streaming server.
  One binary, no separate database. Tested against **v1.21.x**; the deploy
  samples use `runOnUnavailable`, so MediaMTX must be new enough to have
  that hook (v1.15+). Older releases call it `runOnNotReady`.
- **nginx** with the `auth_request` module compiled in (standard on most
  distros/packages) - used to gate the HLS segments behind Alliance Auth auth.
- OBS (or any RTMP source) on the streamer's side.

## How it works

```
            RTMP (1935)                HLS (8888, localhost only)
OBS  ───────────────────►  MediaMTX  ──────────────────────────►  nginx  ────►  browser
                             │                                       │
                             │ publish-auth webhook (HTTP)           │ auth_request sub-check
                             ▼                                       ▼
                          Alliance Auth  ◄────────────────────  Alliance Auth
                          (sets is_live=True,                       (/intel-watcher/hls-auth/,
                           checks can_stream)                        returns 204/401)
```

1. A streamer with `can_stream` starts OBS. MediaMTX receives the RTMP stream
   and, before accepting it, POSTs to this app's `mediamtx_publish_auth`
   webhook. The view checks the stream key exists and the owning user still has
   `can_stream`; if so, it flips `StreamKey.is_live = True` and returns 200.
2. MediaMTX remuxes the stream to HLS and serves it on `:8888` (localhost only).
3. Viewers' browsers poll `/intel-watcher/api/status/`, get the list of live
   streamers, and play each one's HLS URL with
   [hls.js](https://github.com/video-dev/hls.js).
4. nginx reverse-proxies `/hls/` to MediaMTX, but only after an `auth_request`
   sub-check against `/intel-watcher/hls-auth/` - a bare 204/401 endpoint
   requiring `basic_access` - so the video segments themselves are gated, not
   just the page around them.
5. When the streamer stops, MediaMTX fires the `runOnUnavailable` hook, which
   POSTs to `mediamtx_unpublish` (with a shared secret and the session's
   source ID) and the app flips `is_live = False`. If
   `INTEL_WATCHER_HLS_INTERNAL_URL` is set, `api_status` also cross-checks
   MediaMTX directly so a missed webhook or MediaMTX restart can't leave a
   stream stuck "live".

## Installation

These instructions assume you already have a working Alliance Auth 5.x
installation. There are two ways to run the media side: **bare metal**
(MediaMTX as a systemd service on the same host as Alliance Auth) or
**Docker** (MediaMTX as an extra container on your existing Alliance Auth
docker-compose project). Pick one of the two
[MediaMTX setup](#mediamtx-setup) options below - the Alliance Auth side is
the same either way.

### 1. Install the app

From your Alliance Auth virtualenv (bare metal) or inside the `allianceauth`
container (Docker - rebuild the image with this added to its requirements):

```bash
pip install git+https://github.com/evecarboot/aa-watcher.git
```

If you vendor the repo into your project instead of pip-installing it, make sure
`aa_intel_watcher` is importable on the Python path **and owned by the user
Alliance Auth runs as**. In a custom Docker image, `COPY` produces root-owned
files; use e.g.

```dockerfile
COPY --chown=allianceauth:allianceauth aa-watcher/aa_intel_watcher /home/allianceauth/.local/lib/python3.12/site-packages/aa_intel_watcher
```

or `python manage.py fetch_hls_js` will fail with `PermissionError` when it
tries to write `hls.min.js` into the package's own static folder.

### 2. Add to `INSTALLED_APPS`

In `local.py` (or whichever settings file you use):

```python
INSTALLED_APPS += ["aa_intel_watcher"]
```

### 3. Add the settings

See [Alliance Auth configuration](#alliance-auth-configuration) for the full
list. The only required one is the shared webhook secret:

```python
INTEL_WATCHER_MEDIAMTX_SECRET = "your-secret-here"  # see Configuration
```

### 4. Run migrations and fetch hls.js

```bash
python manage.py migrate aa_intel_watcher
python manage.py fetch_hls_js        # one-time, downloads hls.min.js
python manage.py collectstatic       # picks up the app's static files
```

`fetch_hls_js` is a one-time build-time download - it writes a pinned,
sha256-verified `hls.min.js` into the app's static folder so members loading the
page never make an external request. Re-run it with `--force` only when bumping
the pinned version in
`aa_intel_watcher/management/commands/fetch_hls_js.py`. If you skip this step
the page still works - `index.html` falls back to the same pinned, SRI-hashed
build on the jsdelivr CDN - but browsers will make an external request, so run
it to keep the stack self-hosted.

### 5. Wire up the MediaMTX + nginx auth URLs

This step is **required** and easy to miss. Alliance Auth wraps every URL
registered through an app's `url_hook` in `login_required`, which would block
MediaMTX's server-to-server webhook calls - and would turn the nginx
`auth_request` check for HLS into a redirect (which nginx reports as a 500).
So three views must be wired up directly in your **project's** `urls.py`
instead. See [`deploy/urls.py`](deploy/urls.py) for the full rationale and a
copy-paste-ready snippet.

In short, edit `conf/urls.py` (Docker) or your project's `urls.py` (bare metal)
to look like:

```python
from django.urls import include, path

from allianceauth import urls
from aa_intel_watcher.views import (
    hls_auth,
    mediamtx_publish_auth,
    mediamtx_unpublish,
)

urlpatterns = [
    path("intel-watcher/hooks/publish-auth/",
         mediamtx_publish_auth, name="iw_publish_auth"),
    path("intel-watcher/hooks/unpublish/",
         mediamtx_unpublish, name="iw_unpublish"),
    path("intel-watcher/hls-auth/",
         hls_auth, name="iw_hls_auth"),
    path("", include(urls)),
]

handler500 = "allianceauth.views.Generic500Redirect"
handler404 = "allianceauth.views.Generic404Redirect"
handler403 = "allianceauth.views.Generic403Redirect"
handler400 = "allianceauth.views.Generic400Redirect"
```

The three `path(...)` entries must be **above** `path("", include(urls))`
(Django uses first-match routing).

### 6. ALLOWED_HOSTS must cover the internal hostname

Django rejects requests whose `Host` header isn't in `ALLOWED_HOSTS` with
`400 DisallowedHost`. MediaMTX's publish-auth POSTs carry
`Host: allianceauth_gunicorn:8000` (Docker) or `Host: 127.0.0.1:8000`
(bare metal) - **not** your public domain. If `ALLOWED_HOSTS` doesn't
include the internal hostname, every publish-auth call fails and MediaMTX
rejects the stream: **OBS shows a connection failure even though TCP/1935
is reachable.**

In `local.py`:

```python
ALLOWED_HOSTS += ["allianceauth_gunicorn"]   # Docker service name
# or: ALLOWED_HOSTS += ["127.0.0.1"]         # bare metal
```

If you'd rather not allow the underscored service name, add a network alias
such as `intelwatcher-auth` to the `allianceauth_gunicorn` service, add that
to `ALLOWED_HOSTS`, and use it in `authHTTPAddress` instead.

### 7. Configure nginx

See [nginx setup](#nginx-setup) below.

### 8. Restart services

```bash
# Bare metal:
sudo systemctl restart gunicorn  # or however you run Alliance Auth
sudo systemctl reload nginx

# Docker:
docker compose restart allianceauth_gunicorn
docker compose exec nginx nginx -s reload
```

### 9. Set up MediaMTX

See [MediaMTX setup](#mediamtx-setup) below.

### 10. Give OBS a hostname that actually reaches MediaMTX

Skip this only if your Alliance Auth website hostname accepts TCP/1935
directly (simple single-host installs). If the website is behind Cloudflare
or any other HTTP(S)-only proxy/CDN, it does **not** - OBS pointed at it
fails before MediaMTX sees anything, and `docker logs mediamtx` stays
empty.

Create a dedicated DNS record that resolves directly to the MediaMTX
server (Cloudflare: **DNS only**, not proxied) and set it explicitly:

```python
INTEL_WATCHER_RTMP_HOST = "media.example.com"
```

See [Two hostnames](#two-hostnames-web-vs-rtmp). Then verify from a machine
where OBS will run - Windows: `Test-NetConnection media.example.com -Port 1935`;
Linux/macOS: `nc -vz media.example.com 1935`. If the port is unreachable,
fix DNS/firewall/Docker port publication **before** touching anything else.

## Alliance Auth configuration

All settings live in your Alliance Auth `local.py` (or whichever settings file
you use).

| Setting | Default | Description |
| --- | --- | --- |
| `INTEL_WATCHER_MEDIAMTX_SECRET` | `""` (empty) | **Required.** Shared secret MediaMTX must send back on the `unpublish` webhook (via the `X-Webhook-Secret` header). Generate with `openssl rand -hex 32`. Must match the value in your `mediamtx.yml`. |
| `INTEL_WATCHER_HLS_BASE_URL` | `"/hls"` | Public base URL where nginx reverse-proxies MediaMTX's HLS output. Only change it if you also change the nginx `location /hls/` block. |
| `INTEL_WATCHER_RTMP_HOST` | hostname of the current request | Hostname shown to streamers in "Streamer Info" (`rtmp://<host>:1935/live`). **Set this explicitly whenever the Alliance Auth website hostname can't accept RTMP TCP/1935** - typically because it's proxied through a service that only forwards HTTP(S) (Cloudflare, a CDN, a load balancer), or because MediaMTX runs on different infrastructure. See [Two hostnames](#two-hostnames-web-vs-rtmp) below. If left unset, `manage.py check` emits `aa_intel_watcher.W001` and Streamer Info flags the hostname as guessed. |
| `INTEL_WATCHER_HLS_INTERNAL_URL` | `""` (disabled) | **Recommended.** Internal base URL where *Alliance Auth itself* can reach MediaMTX's HLS port - e.g. `http://127.0.0.1:8888` (bare metal) or `http://mediamtx:8888` (Docker). When set, `api_status` cross-checks `is_live` against MediaMTX and clears streams whose playlist 404s, which self-heals stale "live" tiles after a missed unpublish webhook or a MediaMTX restart. Without it, a stream whose unpublish webhook never lands stays marked live (and on viewers' screens) until manually cleared - see TROUBLESHOOTING.md "Stream stops but the tile stays". |

### Two hostnames: web vs RTMP

Many deployments end up needing **two** different DNS names:

| Purpose | Example | Notes |
| --- | --- | --- |
| Alliance Auth website | `https://auth.example.com` | Normal HTTPS. Commonly proxied (Cloudflare, CDN, load balancer). |
| MediaMTX RTMP | `rtmp://media.example.com:1935/live` | Must resolve **directly** to the MediaMTX host. |

A normal HTTP/HTTPS proxy or CDN does not forward arbitrary TCP ports, so if
OBS is pointed at the website hostname it fails before the connection ever
reaches MediaMTX - and nothing shows up in either `mediamtx` or
`allianceauth_gunicorn` logs. If your web hostname is proxied (or MediaMTX
runs elsewhere), create a dedicated DNS record for the media host - for
Cloudflare DNS, that means **DNS only** (grey cloud), not proxied - and set:

```python
INTEL_WATCHER_RTMP_HOST = "media.example.com"
```

If the website hostname also accepts TCP/1935 directly (simple single-host
installs, no proxy in front), the default request-host behaviour works and
no setting is needed - silence `aa_intel_watcher.W001` via
`SILENCED_SYSTEM_CHECKS` if the warning bothers you.

### Site settings (Alliance Auth admin)

Administrators can toggle Intel Watcher chat site-wide in the admin:
**Intel Watcher settings -> Enable chat** (under the `AA_INTEL_WATCHER`
app). Chat is **enabled by default**, including for existing installs
after upgrade. Disabling it removes the chat UI and polling, lets the
stream grid use the full content width, and makes the chat endpoint
return 404 - no restart required, the change takes effect on the next
request/page load.

## Permissions

Two permissions are defined on the app's `General` model (in
`aa_intel_watcher/models.py`). Assign them in the Alliance Auth admin
(`/admin/`) to whichever group/state should have them - the normal Alliance
Auth way, no separate "approve streamer" workflow:

| Permission | What it grants |
| --- | --- |
| `aa_intel_watcher.basic_access` | Can view the Intel Watcher page and, via the nginx `auth_request` gate, the video segments. Give this to everyone who should be able to watch. |
| `aa_intel_watcher.can_stream` | Can see the "Streamer Info" tab, get an OBS stream key, and publish a stream. Give this only to members you trust to broadcast. |

## MediaMTX setup

Pick **one** of the two options below - bare metal or Docker.

### Option A - bare metal

1. Download a MediaMTX binary from
   <https://github.com/bluenviron/mediamtx/releases> (tested with v1.21.x -
   the config uses `runOnUnavailable`, renamed from `runOnNotReady` in
   v1.15) and place it somewhere on your PATH.
2. Copy [`deploy/mediamtx.yml`](deploy/mediamtx.yml) to
   `/etc/mediamtx/mediamtx.yml` and replace `CHANGE_ME` with the same value you
   set for `INTEL_WATCHER_MEDIAMTX_SECRET` in
   [Alliance Auth configuration](#alliance-auth-configuration). The sample
   already binds HLS to `127.0.0.1:8888` and disables RTSP/WebRTC/SRT; only
   RTMP `:1935` is public-facing, which is what OBS needs.
3. Make sure `127.0.0.1` is in `ALLOWED_HOSTS` (see Installation step 6) and
   `curl` is installed (the unpublish hook uses it).
4. Run MediaMTX as a systemd service.

### Option B - Docker

1. Put your secret in a `.env` file next to your Alliance Auth
   `docker-compose.yml`:

   ```
   INTEL_WATCHER_MEDIAMTX_SECRET=<output of: openssl rand -hex 32>
   ```

   (same value as in `local.py`).
2. Copy this repo's `deploy/` directory next to your Alliance Auth
   `docker-compose.yml` (the overlay references `./deploy/...` and
   `./conf/`), then bring it up:

   ```bash
   docker compose -f docker-compose.yml \
                 -f deploy/docker-compose.mediamtx.yml up -d
   ```

   The `mediamtx` service builds a small local image
   ([`deploy/Dockerfile.mediamtx`](deploy/Dockerfile.mediamtx)): the stock
   `bluenviron/mediamtx` image is `scratch`-based and has **no curl**, so the
   unpublish hook could never fire and streams would never go offline. The
   build pins MediaMTX to a tested version - bump `MEDIAMTX_VERSION`
   deliberately.

   The `mediamtx-config` helper container substitutes your secret into
   [`deploy/mediamtx-docker.yml`](deploy/mediamtx-docker.yml) and writes the
   result to `./conf/mediamtx.yml` on every `up`, so edit
   `deploy/mediamtx-docker.yml` (not `./conf/mediamtx.yml`) if you need to tweak
   MediaMTX's config. (`./conf/mediamtx.yml` contains the real secret and is
   covered by `.gitignore` - never commit it.)
3. Add `allianceauth_gunicorn` to `ALLOWED_HOSTS` (see Installation step 6),
   and make sure your Alliance Auth `nginx` container and the `mediamtx`
   container are on the same docker-compose network so the
   `proxy_pass http://mediamtx:8888/` in the nginx snippet resolves.

## nginx setup

Without this step, anyone with the (guessable) HLS URL could watch without
logging in. Add the snippet from
[`deploy/nginx-intel-watcher.conf`](deploy/nginx-intel-watcher.conf) (bare
metal) or
[`deploy/nginx-intel-watcher-docker.conf`](deploy/nginx-intel-watcher-docker.conf)
(Docker) inside the existing `server {}` block that serves your Alliance Auth
site. It uses nginx's `auth_request` to call back into
`/intel-watcher/hls-auth/` (which requires `basic_access` and returns a bare
204/401) before proxying any segment from MediaMTX. The snippet also
rewrites MediaMTX's relative HLS redirects (`proxy_redirect`) - without it,
first-time playback requests 404 on `/live/...` instead of `/hls/live/...`.

> **Docker gotcha:** `conf/nginx.conf` is bind-mounted as a *single file*.
> Editing it on the host replaces the inode, which the running container
> doesn't see - `nginx -s reload` keeps serving the old config. After
> editing, run `docker compose up -d --force-recreate nginx`, or verify with
> `docker compose exec nginx cat /etc/nginx/nginx.conf`. See
> TROUBLESHOOTING.md (Bug 4).

## OBS / streamer setup

Before anyone opens OBS, a new install should pass this checklist - each
item is a different failure stage (see TROUBLESHOOTING.md):

- [ ] `mediamtx` container/service is running (`docker ps --filter name=mediamtx`)
- [ ] TCP/1935 is reachable from an external machine (`nc -vz <rtmp-host> 1935`)
- [ ] The RTMP hostname resolves **directly** to the server - not through a
      proxied/CDN hostname that only forwards HTTP(S)
- [ ] "Streamer Info" shows the intended RTMP hostname
      (`INTEL_WATCHER_RTMP_HOST`, or silence `aa_intel_watcher.W001` if the
      web host genuinely accepts RTMP)
- [ ] Project `urls.py` has the three direct routes (publish-auth,
      unpublish, hls-auth) **above** `path("", include(urls))` - step 5
- [ ] The internal hostname MediaMTX POSTs to (`allianceauth_gunicorn`,
      `127.0.0.1`, or your alias) is in `ALLOWED_HOSTS` - step 6
- [ ] MediaMTX and `allianceauth_gunicorn` share a Docker network / the
      `authHTTPAddress` hostname resolves
- [ ] The streamer is an active user with `aa_intel_watcher.can_stream`

For members with `can_stream`:

1. Log in to Alliance Auth and open **Intel Watcher -> Streamer Info**.
2. Copy the **Server** (`rtmp://<host>:1935/live`) and **Stream Key** values.
3. In OBS, go to **Settings -> Stream**, paste those two values, and click
   **Start Streaming**. Nothing else to configure.
4. Use **Regenerate key** on the same page if the key ever leaks - it
   invalidates the old one immediately, so update OBS afterwards.

## Updating

```bash
pip install --upgrade --force-reinstall git+https://github.com/evecarboot/aa-watcher.git
python manage.py migrate aa_intel_watcher        # apply any new migrations
python manage.py fetch_hls_js --force            # only if the pinned hls.js version bumped
python manage.py collectstatic
```

Then restart Alliance Auth (and reload nginx if you changed any of the
`deploy/` snippets).

## Uninstall

1. Remove `"aa_intel_watcher"` from `INSTALLED_APPS`.
2. Remove the three `path(...)` entries (two webhooks + `hls-auth`) from
   your project's `urls.py`
   (added in [Installation step 5](#5-wire-up-the-mediamtx--nginx-auth-urls)).
3. Remove the nginx `/hls/` and `/intel-watcher/_auth_check/` `location` blocks.
4. Stop and remove MediaMTX (the systemd service or the docker-compose overlay).
5. Drop the app's tables (optional - leaves the data in place otherwise):

   ```bash
   python manage.py migrate aa_intel_watcher zero
   ```

6. `pip uninstall aa-intel-watcher` and restart Alliance Auth.

## Troubleshooting

See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) - it starts with a staged
diagnosis for "OBS can't publish" (first establish whether the connection
even reached MediaMTX before touching Django), then documents the historical
bug log: why the webhook URLs must live in the project `urls.py`, not the
app's `url_hook`, the `ALLOWED_HOSTS`/`DisallowedHost` trap, and more.

## Licence

AA-Watcher is proprietary/source-available software.

The source repository is publicly accessible so Alliance Auth administrators
can inspect and install the official plugin. Downloading and installing the
official unmodified plugin is permitted.

Modification, redistribution, republishing and derivative works are not
permitted except where expressly authorised. See [`LICENSE`](LICENSE) for the
full terms.
