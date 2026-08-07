# aa-intel-watcher

Alliance Auth app that adds a corp/alliance-only "Intel Watcher" section:
members can stream from OBS to it, and it's viewable only by logged-in
Alliance Auth users with the right permission - no YouTube/Twitch, no
public exposure.

One sidebar entry ("Intel Watcher") with two tabs inside it:

- **Intel Viewing** - a grid of every currently-live stream (one tile per
  streamer, no clutter around them) plus chat. This is the page everyone
  with `basic_access` lands on.
- **Streamer Info** - OBS server/key details and the "regenerate key"
  button. Only shown/reachable to users with `can_stream`.

- **Streaming server:** [MediaMTX](https://github.com/bluenviron/mediamtx) -
  a single binary, no separate database or website. Accepts RTMP from OBS,
  serves HLS for browser playback.
- **Who can push a stream (`can_stream` permission):** managed the normal
  Alliance Auth way, via groups/states in the admin. No separate "approve
  streamer" workflow to build/maintain.
- **Auto-show whoever is live:** MediaMTX calls a webhook in this app on
  publish/unpublish; the page polls a small JSON endpoint and swaps the
  player to whichever approved streamer is currently live.
- **Chat:** plain polling AJAX chat stored in the Alliance Auth database.
  No websockets/Channels/Redis pub-sub required.
- **Who can view (`basic_access` permission):** gates both the page and,
  via the nginx sample, the actual video segments - not just the UI around
  them.
- **Multiple simultaneous streamers:** each live streamer gets their own
  tile with independent native video controls. A "Solo audio" button on
  each tile mutes every other tile - handy if you and another streamer
  both end up in the same system and don't want two audio tracks playing
  at once.
- **Theming:** every template extends `allianceauth/base-bs5.html` and
  only uses standard Bootstrap classes, so the app automatically matches
  whatever Bootswatch theme a user has selected in Alliance Auth - nothing
  to configure.

## Prerequisites

Everything here assumes you already have a working Alliance Auth install -
either bare metal (see the [official install docs](https://allianceauth.readthedocs.io/en/latest/installation/allianceauth.html),
SSH'd in as the `allianceserver` user or equivalent) or a Docker Compose
deployment. This app doesn't replace or duplicate any of that - it just
needs a few extra tools alongside it:

| Tool | Why it's needed | How to check if it's already there |
|---|---|---|
| Alliance Auth + its Python virtualenv | This app `pip install`s into it | `source /path/to/venv/auth/bin/activate` then `python -c "import allianceauth"` (no error = OK) |
| nginx **or** Apache | Reverse-proxies the video and gates it behind an active AA login | `systemctl status nginx` / `systemctl status apache2` |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | Accepts OBS's RTMP stream and serves it back out as HLS for the browser | Not installed by default - see "Install MediaMTX" below |
| `curl` | Used by MediaMTX's "streamer went offline" hook to notify Alliance Auth | `curl --version` (present on nearly every Linux distro already) |
| A firewall rule/port for `1935` (RTMP) | So OBS can actually reach MediaMTX from wherever streamers are | Check with your hosting provider's firewall/security group settings |

Nothing here needs Redis, Celery, or Django Channels beyond what a
standard Alliance Auth install already has, and MediaMTX is just a single
binary/container - no extra database or service of its own.

Running Alliance Auth via **Docker** instead of bare metal? Skip straight
to [Docker install](#docker-install) below - the steps and file paths
differ enough that it gets its own section.

## Install (bare metal / virtualenv)

1. Add this app to your Alliance Auth project's virtualenv, straight from
   GitHub (this app isn't published to PyPI):

   ```bash
   pip install git+https://github.com/evecarboot/aa-watcher.git@main
   ```

   To upgrade later, re-run the same command with `-U`:

   ```bash
   pip install -U git+https://github.com/evecarboot/aa-watcher.git@main
   ```

2. In your AA project's `myauth/settings/local.py`:

   ```python
   INSTALLED_APPS += ["aa_intel_watcher"]

   # Must match the nginx `location /hls/` you configure below.
   INTEL_WATCHER_HLS_BASE_URL = "/hls"

   # Long random string, must match deploy/mediamtx.yml's runOnUnpublish hook.
   INTEL_WATCHER_MEDIAMTX_SECRET = "generate-a-long-random-string-here"
   ```

3. Run migrations and collect static files:

   ```bash
   python manage.py migrate aa_intel_watcher
   python manage.py collectstatic
   ```

4. Download `hls.js` and place it at
   `aa_intel_watcher/static/aa_intel_watcher/js/hls.min.js` (see the
   `README_VENDOR_HLS.txt` in that folder) - this keeps the whole stack
   self-hosted with no external CDN request when members load the page.

5. In the Django admin, grant:
   - `aa_intel_watcher | general | Can access the Intel Watcher page`
     (`basic_access`) to your corp/alliance member group(s).
   - `aa_intel_watcher | general | Can broadcast a stream to the Intel
     Watcher` (`can_stream`) to yourself and the other approved
     streamer(s).

## Install MediaMTX (bare metal)

1. Download a release from
   [MediaMTX releases](https://github.com/bluenviron/mediamtx/releases) for
   your server, and run it as a systemd service.
2. Use [deploy/mediamtx.yml](deploy/mediamtx.yml) as a starting config -
   fill in `INTEL_WATCHER_MEDIAMTX_SECRET`'s value in the
   `runOnUnpublish` line.
3. Make sure MediaMTX only listens on localhost/private interfaces for
   RTMP (`1935`) if you're tunnelling it, or open `1935` on your firewall
   only if streamers connect directly over the public internet/VPN - never
   expose the HLS port (`8888`) publicly, nginx is the only thing that
   should reach it (see below).

(Running Alliance Auth in Docker? See
[Docker install](#docker-install) instead.)

## Web server (nginx or Apache, bare metal)

A standard Alliance Auth install already runs nginx or Apache in front of
gunicorn (see the [AA install docs](https://allianceauth.readthedocs.io/en/latest/installation/allianceauth.html#web-server)),
so this is almost always just adding a couple of `location` blocks to an
existing config, not installing anything new. Check which one you're
running with `systemctl status nginx` / `systemctl status apache2` on the
server if you're not sure.

- **nginx:** add [deploy/nginx-intel-watcher.conf](deploy/nginx-intel-watcher.conf)'s
  `location` blocks into the existing `server {}` block for your Alliance
  Auth domain, then `sudo systemctl reload nginx`.
- **Apache:** not included yet - if your install uses Apache instead of
  nginx, the same idea applies (proxy `/hls/` to MediaMTX's HLS port,
  gated by an auth check against `/intel-watcher/api/status/`) but the
  config will need to use `mod_proxy` + `mod_auth_request`/`mod_authnz`
  equivalents instead.

Either way, this makes sure the video itself (not just the page) is
gated behind an active, permitted Alliance Auth login.

## Docker install

This covers the common community Docker Compose layout for Alliance Auth:
a `custom.dockerfile` that builds the AA image, a `conf/` folder with
`local.py`/`nginx.conf` mounted as volumes into the containers, an inner
`nginx` container that serves static files and proxies to
`allianceauth_gunicorn`, and (optionally) something like nginx-proxy-manager
in front of that for TLS. Adjust container/service names below if yours
differ.

### 1. Get the app into the Alliance Auth image

This app isn't published to PyPI, so add a line to `conf/requirements.txt`
(the same file `custom.dockerfile` already uses for other extra apps like
`aa-discordnotify`) pointing straight at the public GitHub repo:

```
git+https://github.com/evecarboot/aa-watcher.git@main
```

Rebuild and recreate the containers that run Alliance Auth code
(gunicorn, celery worker(s), beat, discordbot, etc. - anything using the
`allianceauth-base` image):

```bash
docker compose build
docker compose up -d
```

### 2. Settings

Since `conf/local.py` is already bind-mounted into the container at
`myauth/settings/local.py`, just edit it on the host like any other AA
setting - no rebuild needed for this step, only a container restart:

```python
INSTALLED_APPS += ["aa_intel_watcher"]

# Must match the nginx `location /hls/` configured in step 4 below.
INTEL_WATCHER_HLS_BASE_URL = "/hls"

# Long random string, must match the mediamtx.yml runOnUnpublish hook
# added in step 3.
INTEL_WATCHER_MEDIAMTX_SECRET = "generate-a-long-random-string-here"
```

### 3. Migrate, collect static, and add MediaMTX

```bash
docker compose exec allianceauth_gunicorn python manage.py migrate aa_intel_watcher
docker compose exec allianceauth_gunicorn python manage.py collectstatic --noinput
```

Download `hls.js` and place it at
`aa_intel_watcher/static/aa_intel_watcher/js/hls.min.js` (see
`README_VENDOR_HLS.txt` in that folder) **before** running `collectstatic`
above, so it ends up in the shared `static-volume`.

Add MediaMTX as its own container on the same compose project, using
[deploy/docker-compose.mediamtx.yml](deploy/docker-compose.mediamtx.yml)
as an overlay (or copy its `mediamtx:` service block straight into your
existing `docker-compose.yml`):

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.mediamtx.yml up -d
```

Copy [deploy/mediamtx-docker.yml](deploy/mediamtx-docker.yml) (not the
bare-metal `deploy/mediamtx.yml`) to `./conf/mediamtx.yml` and fill in
`INTEL_WATCHER_MEDIAMTX_SECRET`'s value in the `runOnUnpublish` line - it
already targets Alliance Auth via the `allianceauth_gunicorn` Docker DNS
name instead of `127.0.0.1`, since MediaMTX now runs in its own container.

### 4. nginx

Add [deploy/nginx-intel-watcher-docker.conf](deploy/nginx-intel-watcher-docker.conf)'s
`location` blocks into `conf/nginx.conf` (the file mounted into the inner
`nginx` container), inside the existing `server {}` block, then reload
that container:

```bash
docker compose restart nginx
```

This variant proxies to `allianceauth_gunicorn` and `mediamtx` by Docker
service name instead of `127.0.0.1`, since it runs in its own container.
If you're fronting everything with something like nginx-proxy-manager
(as in the `proxy` service above), no changes are needed there - it just
needs to keep forwarding to the `nginx` container like it already does
for the rest of Alliance Auth.

### 5. Ports for OBS

Make sure RTMP (`1935`) reaches the `mediamtx` container - the compose
overlay above publishes it to the host (`"1935:1935"`). Never publish the
HLS port (`8888`); only the `nginx` container should reach that, over the
internal docker network.

Grant the `basic_access`/`can_stream` permissions the same way as the
bare-metal install (see step 5 in [Install (bare metal / virtualenv)](#install-bare-metal--virtualenv)
above).

### Updating later

After a new commit lands on the `main` branch of the GitHub repo, force a
fresh pull of it and rebuild (pip's git support caches by default, so
`docker compose build` alone won't pick up new commits on its own):

```bash
docker compose build --no-cache allianceauth_gunicorn allianceauth_worker allianceauth_beat allianceauth_worker_services allianceauth_discordbot
docker compose up -d
docker compose exec allianceauth_gunicorn python manage.py migrate aa_intel_watcher
docker compose exec allianceauth_gunicorn python manage.py collectstatic --noinput
```

## OBS setup (for approved streamers)

Nothing to configure ahead of time - log in, open the Intel Watcher page's
**Streamer Info** tab, and copy the two fields it shows directly into
OBS's Stream settings:

- **Server** - filled in automatically from your Alliance Auth domain.
  If your RTMP hostname differs from the web domain (e.g. a different
  public IP/DNS record), set `INTEL_WATCHER_RTMP_HOST` in `local.py` to
  override it.
- **Stream key** - unique per user, auto-generated the first time they
  open the page. Regenerate any time from the page if a key ever leaks.

## Opsec notes

- Everything (MediaMTX, nginx, Alliance Auth) should run on
  infrastructure you control - not a third-party streaming platform.
- Stream keys are secrets - anyone with a valid key can publish under
  that identity. Regenerate a key immediately if you suspect it leaked.
- The `basic_access` permission gates viewing; keep it scoped to your
  corp/alliance member group(s), not "logged in to anything."
- Consider only exposing the RTMP port (1935) over your corp's VPN
  rather than the open internet, if that's already part of your
  infrastructure.
