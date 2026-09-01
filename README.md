# aa-intel-watcher

An [Alliance Auth](https://github.com/allianceauth/allianceauth) app that adds a
corp/alliance-only "Intel Watcher" section: members can stream from OBS to it,
and it's viewable only by logged-in Alliance Auth users with the right
permission - no YouTube/Twitch, no public exposure.

Compatible with Alliance Auth **4.x** and **5.x** (tested against 5.2).

## Contents

- [What it does](#what-it-does)
- [Features](#features)
- [Requirements](#requirements)
- [How it works](#how-it-works)
- [Installation](#installation)
- [Configuration](#configuration)
- [Permissions](#permissions)
- [MediaMTX setup](#mediamtx-setup)
- [Streamer setup](#streamer-setup-for-members-with-can_stream)
- [Upgrading](#upgrading)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## What it does

Adds one sidebar entry ("Intel Watcher") with two tabs inside it:

- **Intel Viewing** - a grid of every currently-live stream (one tile per
  streamer, no clutter around them) plus chat. This is the page everyone with
  `basic_access` lands on.
- **Streamer Info** - OBS server/key details and the "regenerate key" button.
  Only shown/reachable to users with `can_stream`.

## Features

- **Streaming server:** [MediaMTX](https://github.com/bluenviron/mediamtx) - a
  single binary, no separate database or website. Accepts RTMP from OBS, serves
  HLS for browser playback.
- **Who can push a stream (`can_stream` permission):** managed the normal
  Alliance Auth way, via groups/states in the admin. No separate "approve
  streamer" workflow to build/maintain.
- **Auto-show whoever is live:** MediaMTX calls a webhook in this app on
  publish/unpublish; the page polls a small JSON endpoint and swaps the player
  to whichever approved streamer is currently live.
- **Chat:** plain polling AJAX chat stored in the Alliance Auth database. No
  websockets/Channels/Redis pub-sub required.
- **Who can view (`basic_access` permission):** gates both the page and, via the
  nginx sample, the actual video segments - not just the UI around them.
- **Multiple simultaneous streamers:** each live streamer gets their own tile
  with independent native video controls. A "Solo audio" button on each tile
  mutes every other tile - handy if you and another streamer both end up in the
  same system and don't want two audio tracks playing at once.
- **Theming:** every template extends `allianceauth/base-bs5.html` and only uses
  standard Bootstrap classes, so the app automatically matches whatever
  Bootswatch theme a user has selected in Alliance Auth - nothing to configure.

## Requirements

- Alliance Auth **4.x** or **5.x** (tested against 5.2).
- Python **>= 3.10**.
- [MediaMTX](https://github.com/bluenviron/mediamtx) - the streaming server.
  One binary, no separate database.
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
                          (sets is_live=True,                       (/intel-watcher/api/status/,
                           checks can_stream)                        requires basic_access)
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
   sub-check against `/intel-watcher/api/status/` - so the video segments
   themselves are gated by `basic_access`, not just the page around them.
5. When the streamer stops, MediaMTX fires the `runOnUnavailable` hook, which
   POSTs to `mediamtx_unpublish` (with a shared secret) and the app flips
   `is_live = False`.

## Installation

These instructions assume you already have a working Alliance Auth installation
(4.x or 5.x). There are two ways to run the media side: **bare metal** (MediaMTX
as a systemd service on the same host as Alliance Auth) or **Docker** (MediaMTX
as an extra container on your existing Alliance Auth docker-compose project).
Pick one of the two [MediaMTX setup](#mediamtx-setup) options below - the
Alliance Auth side is the same either way.

### 1. Install the app

From your Alliance Auth virtualenv (bare metal) or inside the `allianceauth`
container (Docker - rebuild the image with this added to its requirements):

```bash
pip install git+https://github.com/evecarboot/aa-watcher.git
```

If you vendor the repo into your project instead of pip-installing it, make sure
`aa_intel_watcher` is importable on the Python path.

### 2. Add to `INSTALLED_APPS`

In `local.py` (or whichever settings file you use):

```python
INSTALLED_APPS += ["aa_intel_watcher"]
```

### 3. Add the settings

See [Configuration](#configuration) for the full list. The only required one is
the shared webhook secret:

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
`aa_intel_watcher/management/commands/fetch_hls_js.py`.

> **Note:** the current `index.html` template loads hls.js from the jsdelivr CDN
> with an SRI integrity hash. If you prefer the fully self-hosted path described
> above, change the `<script src=...>` in
> `aa_intel_watcher/templates/aa_intel_watcher/index.html` to
> `{% static 'aa_intel_watcher/js/hls.min.js' %}` and run `fetch_hls_js` +
> `collectstatic`.

### 5. Wire up the MediaMTX webhook URLs

This step is **required** and easy to miss. Alliance Auth wraps every URL
registered through an app's `url_hook` in `login_required`, which would block
MediaMTX's server-to-server webhook calls. So the two webhook views must be
wired up directly in your **project's** `urls.py` instead. See
[`deploy/urls.py`](deploy/urls.py) for the full rationale and a
copy-paste-ready snippet.

In short, edit `conf/urls.py` (Docker) or your project's `urls.py` (bare metal)
to look like:

```python
from django.urls import include, path

from allianceauth import urls
from aa_intel_watcher.views import mediamtx_publish_auth, mediamtx_unpublish

urlpatterns = [
    path("intel-watcher/hooks/publish-auth/",
         mediamtx_publish_auth, name="iw_publish_auth"),
    path("intel-watcher/hooks/unpublish/",
         mediamtx_unpublish, name="iw_unpublish"),
    path("", include(urls)),
]

handler500 = "allianceauth.views.Generic500Redirect"
handler404 = "allianceauth.views.Generic404Redirect"
handler403 = "allianceauth.views.Generic403Redirect"
handler400 = "allianceauth.views.Generic400Redirect"
```

The two `path(...)` entries must be **above** `path("", include(urls))` (Django
uses first-match routing).

### 6. Configure nginx to gate the HLS segments

Without this, anyone with the (guessable) HLS URL could watch without logging
in. Add the snippet from
[`deploy/nginx-intel-watcher.conf`](deploy/nginx-intel-watcher.conf) (bare
metal) or
[`deploy/nginx-intel-watcher-docker.conf`](deploy/nginx-intel-watcher-docker.conf)
(Docker) inside the existing `server {}` block that serves your Alliance Auth
site. It uses nginx's `auth_request` to call back into
`/intel-watcher/api/status/` (which requires `basic_access`) before proxying
any segment from MediaMTX.

### 7. Restart services

```bash
# Bare metal:
sudo systemctl restart gunicorn  # or however you run Alliance Auth
sudo systemctl reload nginx

# Docker:
docker compose restart allianceauth_gunicorn
docker compose exec nginx nginx -s reload
```

### 8. Set up MediaMTX

See [MediaMTX setup](#mediamtx-setup) below.

## Configuration

All settings live in your Alliance Auth `local.py` (or whichever settings file
you use).

| Setting | Default | Description |
| --- | --- | --- |
| `INTEL_WATCHER_MEDIAMTX_SECRET` | `""` (empty) | **Required.** Shared secret MediaMTX must send back on the `unpublish` webhook (via the `X-Webhook-Secret` header). Generate with `openssl rand -hex 32`. Must match the value in your `mediamtx.yml`. |
| `INTEL_WATCHER_HLS_BASE_URL` | `"/hls"` | Public base URL where nginx reverse-proxies MediaMTX's HLS output. Only change it if you also change the nginx `location /hls/` block. |
| `INTEL_WATCHER_RTMP_HOST` | hostname of the current request | Hostname OBS should connect to for RTMP. Set it explicitly if your public RTMP host differs from your web host (e.g. `stream.example.com` vs `auth.example.com`). |

## Permissions

Two permissions are defined on the app's `General` model (in
`aa_intel_watcher/models.py`). Assign them in the Alliance Auth admin
(`/admin/`) to whichever group/state should have them - the normal Alliance Auth
way, no separate "approve streamer" workflow:

| Permission | What it grants |
| --- | --- |
| `aa_intel_watcher.basic_access` | Can view the Intel Watcher page and, via the nginx `auth_request` gate, the video segments. Give this to everyone who should be able to watch. |
| `aa_intel_watcher.can_stream` | Can see the "Streamer Info" tab, get an OBS stream key, and publish a stream. Give this only to members you trust to broadcast. |

## MediaMTX setup

Pick **one** of the two options below - bare metal or Docker.

### Option A - bare metal

1. Download the MediaMTX binary from
   <https://github.com/bluenviron/mediamtx/releases> and place it somewhere on
   your PATH.
2. Copy [`deploy/mediamtx.yml`](deploy/mediamtx.yml) to
   `/etc/mediamtx/mediamtx.yml` and replace `CHANGE_ME` with the same value you
   set for `INTEL_WATCHER_MEDIAMTX_SECRET` in
   [Configuration](#configuration).
3. Run MediaMTX as a systemd service, bound to localhost / a private interface
   only - never the public internet (nginx is the only public-facing entry
   point).

### Option B - Docker

1. Put your secret in a `.env` file next to your Alliance Auth
   `docker-compose.yml`:

   ```
   INTEL_WATCHER_MEDIAMTX_SECRET=<output of: openssl rand -hex 32>
   ```

   (same value as in `local.py`).
2. Bring up the overlay from the directory containing your Alliance Auth
   `docker-compose.yml`:

   ```bash
   docker compose -f docker-compose.yml \
                 -f deploy/docker-compose.mediamtx.yml up -d
   ```

   The `mediamtx-config` helper container substitutes your secret into
   [`deploy/mediamtx-docker.yml`](deploy/mediamtx-docker.yml) and writes the
   result to `./conf/mediamtx.yml` on every `up`, so edit
   `deploy/mediamtx-docker.yml` (not `./conf/mediamtx.yml`) if you need to tweak
   MediaMTX's config.
3. Make sure your Alliance Auth `nginx` container and the `mediamtx` container
   are on the same docker-compose network so the
   `proxy_pass http://mediamtx:8888/` in the nginx snippet resolves.

## Streamer setup (for members with `can_stream`)

1. Log in to Alliance Auth and open **Intel Watcher -> Streamer Info**.
2. Copy the **Server** (`rtmp://<host>:1935/live`) and **Stream Key** values.
3. In OBS, go to **Settings -> Stream**, paste those two values, and click
   **Start Streaming**. Nothing else to configure.
4. Use **Regenerate key** on the same page if the key ever leaks - it
   invalidates the old one immediately, so update OBS afterwards.

## Upgrading

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
2. Remove the two `path(...)` webhook entries from your project's `urls.py`
   (added in [step 5](#5-wire-up-the-mediamtx-webhook-urls) of Installation).
3. Remove the nginx `/hls/` and `/intel-watcher/_auth_check/` `location` blocks.
4. Stop and remove MediaMTX (the systemd service or the docker-compose overlay).
5. Drop the app's tables (optional - leaves the data in place otherwise):

   ```bash
   python manage.py migrate aa_intel_watcher zero
   ```

6. `pip uninstall aa-intel-watcher` and restart Alliance Auth.

## Troubleshooting

See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for known gotchas and the
debugging log - e.g. why the webhook URLs must live in the project `urls.py`,
not the app's `url_hook`.

## License

MIT - see [`pyproject.toml`](pyproject.toml).
