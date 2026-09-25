import http.cookiejar
import json
import logging
import secrets
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import (
    Http404,
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import ChatMessage, IntelWatcherSettings, StreamKey

logger = logging.getLogger(__name__)

MAX_CHAT_LENGTH = 500
# Number of messages a fresh client (since=0) is shown, and the page size
# for incremental polling.
CHAT_BACKLOG_SIZE = 50
CHAT_PAGE_SIZE = 200
# MediaMTX auth request bodies are small (~300 bytes). Anything much larger
# is not a real MediaMTX request.
PUBLISH_AUTH_MAX_BODY = 8192
# A stream must have been live this long before the optional HLS liveness
# probe is allowed to mark it offline - a freshly published stream needs a
# few seconds before its playlist exists.
LIVENESS_GRACE_SECONDS = 15


def _hls_base_url():
    return getattr(settings, "INTEL_WATCHER_HLS_BASE_URL", "/hls").rstrip("/") or "/hls"


def _mediamtx_secret():
    return getattr(settings, "INTEL_WATCHER_MEDIAMTX_SECRET", "")


def _hls_internal_url():
    return getattr(settings, "INTEL_WATCHER_HLS_INTERNAL_URL", "").rstrip("/")


def _hls_cdn_secret():
    return getattr(settings, "INTEL_WATCHER_HLS_CDN_SECRET", "")


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
def index(request):
    context = {
        "can_stream": request.user.has_perm("aa_intel_watcher.can_stream"),
        "active_tab": "viewing",
        "chat_enabled": IntelWatcherSettings.chat_is_enabled(),
    }
    return render(request, "aa_intel_watcher/index.html", context)


def _request_hostname(request):
    """Hostname part of request.get_host(), without the port."""
    host = request.get_host()
    if host.startswith("["):  # IPv6 literal: [::1]:8000
        return host[1:].split("]")[0]
    return host.split(":")[0]


@login_required
@permission_required("aa_intel_watcher.can_stream", raise_exception=True)
def streamer_info(request):
    stream_key, _created = StreamKey.objects.get_or_create(user=request.user)
    rtmp_host = getattr(settings, "INTEL_WATCHER_RTMP_HOST", None)
    if rtmp_host:
        rtmp_host_inferred = False
    else:
        # Fallback for simple installs where the web host also accepts RTMP.
        # Behind a proxy/CDN that only forwards HTTP(S) (e.g. Cloudflare),
        # this hostname is wrong and OBS fails before reaching MediaMTX -
        # rtmp_host_inferred lets the template say so, and the
        # aa_intel_watcher.W001 system check flags it at deploy time.
        rtmp_host = _request_hostname(request)
        rtmp_host_inferred = True
    context = {
        "can_stream": True,
        "active_tab": "streamer_info",
        "stream_key": stream_key.key,
        "rtmp_path": stream_key.path_name,
        "rtmp_server": f"rtmp://{rtmp_host}:1935/live",
        "rtmp_host_inferred": rtmp_host_inferred,
    }
    return render(request, "aa_intel_watcher/streamer_info.html", context)


def _probe_hls_stream(stream):
    """Check whether MediaMTX is still serving this stream's playlist.

    MediaMTX answers the first playlist request with a 302 back to itself
    carrying a cookieCheck query plus a Set-Cookie - it does this for live
    AND dead paths, so the redirect alone proves nothing. We follow it with
    a cookie jar and judge only the final response: a successful playlist
    response means the stream exists, a final 404 means it is gone, and
    anything else (other HTTP status, unreachable host, timeout, redirect
    loop) is inconclusive - return None so the recorded state is kept and
    transient errors never flap streams.
    """
    url = f"{_hls_internal_url()}/{stream.active_path_name}/index.m3u8"

    # MediaMTX deployments front HLS with hlsCDNSecret: nginx injects the
    # Bearer for browser traffic, but this probe bypasses nginx and must
    # present the same backend secret itself or every request fails auth
    # and the check stays inconclusive forever.
    cdn_secret = _hls_cdn_secret()
    request = urllib.request.Request(url)
    if cdn_secret:
        request.add_header("Authorization", f"Bearer {cdn_secret}")

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )
    try:
        opener.open(request, timeout=2.5)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        # Other statuses (5xx, anything unexpected after the cookie flow)
        # prove neither liveness nor absence.
        return None
    except (urllib.error.URLError, OSError):
        return None


def _reconcile_live_streams(live_streams):
    """Cross-check is_live against MediaMTX's actual HLS output.

    Only runs when INTEL_WATCHER_HLS_INTERNAL_URL is configured. Marks a
    stream offline only when MediaMTX definitively 404s it and it has been
    live long enough for its playlist to exist - this recovers the state
    after a missed unpublish webhook or a MediaMTX restart.
    """
    now = timezone.now()
    for stream in live_streams:
        if not stream.last_seen:
            continue
        age = (now - stream.last_seen).total_seconds()
        if age < LIVENESS_GRACE_SECONDS:
            yield stream
            continue
        alive = _probe_hls_stream(stream)
        if alive is False:
            logger.warning(
                "Intel Watcher: %s marked live but MediaMTX has no stream at "
                "%s - clearing stale state",
                stream.user,
                stream.active_path_name,
            )
            stream.go_offline()
        else:
            yield stream


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
@require_GET
def api_status(request):
    """Returns which approved streamers are currently live."""
    live_streams = list(
        StreamKey.objects.filter(is_live=True)
        .select_related("user")
        .order_by("last_seen")
    )

    if _hls_internal_url():
        live_streams = list(_reconcile_live_streams(live_streams))

    streams = [
        {
            "display_name": stream.display_name or stream.user.username,
            "hls_url": f"{_hls_base_url()}/{stream.active_path_name}/index.m3u8",
        }
        for stream in live_streams
        if stream.is_live
    ]
    return JsonResponse({"streams": streams})


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
@require_http_methods(["GET", "POST"])
def api_chat(request):
    """GET polls for new messages since ?since=<id>. POST sends a message."""
    if not IntelWatcherSettings.chat_is_enabled():
        raise Http404
    if request.method == "POST":
        message = (request.POST.get("message") or "").strip()
        if not message:
            return JsonResponse({"error": "empty message"}, status=400)
        message = message[:MAX_CHAT_LENGTH]
        ChatMessage.objects.create(user=request.user, message=message)
        return JsonResponse({"ok": True})

    try:
        since_id = int(request.GET.get("since") or 0)
    except (ValueError, TypeError):
        since_id = 0
    since_id = max(since_id, 0)

    # Order by id (monotonic), not created_at: rows committed in the same
    # timestamp make a created_at-ordered window ambiguous, which can skip
    # or repeat messages around the page boundary.
    if since_id == 0:
        # New client: show the most recent backlog, not the oldest rows ever.
        messages = reversed(
            ChatMessage.objects.select_related("user").order_by("-id")[
                :CHAT_BACKLOG_SIZE
            ]
        )
    else:
        messages = (
            ChatMessage.objects.filter(id__gt=since_id)
            .select_related("user")
            .order_by("id")[:CHAT_PAGE_SIZE]
        )

    return JsonResponse(
        {
            "messages": [
                {
                    "id": msg.id,
                    "user": msg.user.username,
                    "message": msg.message,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in messages
            ]
        }
    )


@login_required
@permission_required("aa_intel_watcher.can_stream", raise_exception=True)
@require_http_methods(["POST"])
def api_regenerate_key(request):
    stream_key, _created = StreamKey.objects.get_or_create(user=request.user)
    stream_key.key = secrets.token_hex(16)
    stream_key.save(update_fields=["key"])
    return JsonResponse({"stream_key": stream_key.key, "rtmp_path": stream_key.path_name})


@require_GET
def hls_auth(request):
    """Lightweight auth check for nginx `auth_request` gating /hls/.

    Must be registered in the project's urls.py (next to the MediaMTX
    webhooks - see deploy/urls.py), NOT via the app's url_hook, because
    Alliance Auth wraps url_hook views in login_required, which turns the
    anonymous-user case into a 302 redirect - and auth_request treats
    anything that isn't 2xx/401/403 as a 500. This view deliberately
    returns 204 (allow) or 401 (deny) and touches no database rows.
    """
    user = request.user
    if (
        user.is_authenticated
        and user.is_active
        and user.has_perm("aa_intel_watcher.basic_access")
    ):
        return HttpResponse(status=204)
    return HttpResponse(status=401)


# ---------------------------------------------------------------------------
# Webhooks called by MediaMTX itself (server-to-server, not by browsers).
# MediaMTX should be configured to only reach these over localhost / the
# internal Docker network - see the nginx/mediamtx samples in deploy/. The
# unpublish hook additionally requires a shared secret.
# ---------------------------------------------------------------------------

# MediaMTX auth actions we understand. Anything else is denied: failing
# closed is correct for an auth boundary, and the set is fixed for the
# pinned MediaMTX version.
# Viewers only ever consume HLS through the auth-gated nginx proxy. A direct
# RTMP/RTSP/WebRTC/SRT pull of live/<key> would bypass Alliance Auth entirely
# for anyone holding a stream key, so read/playback on those protocols is
# denied. Missing/unknown protocol values are allowed for forward
# compatibility.
_DENIED_READ_PROTOCOLS = {"rtmp", "rtsp", "webrtc", "srt"}
_KNOWN_NON_PUBLISH_ACTIONS = {"read", "playback", "api", "metrics", "pprof"}


def _last_path_segment(value):
    if not isinstance(value, str):
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


@csrf_exempt
@require_http_methods(["POST"])
def mediamtx_publish_auth(request):
    """MediaMTX external auth webhook (authMethod: http / authHTTPAddress).

    MediaMTX POSTs JSON like
    {"user","password","token","ip","action","path","protocol","id","query"}.
    action is one of publish|read|playback|api|metrics|pprof; path for an
    OBS publish is "live/<stream-key>"; id is MediaMTX's connection UUID.
    Any 2xx allows, anything else denies.
    """
    content_length = request.META.get("CONTENT_LENGTH") or ""
    if content_length.isdigit() and int(content_length) > PUBLISH_AUTH_MAX_BODY:
        return HttpResponseForbidden("denied")

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return HttpResponseForbidden("denied")
    if not isinstance(payload, dict):
        return HttpResponseForbidden("denied")

    action = payload.get("action")
    if not isinstance(action, str):
        action = None
    protocol = payload.get("protocol")
    if not isinstance(protocol, str):
        protocol = None
    request_id = payload.get("id") if isinstance(payload.get("id"), str) else ""

    if action != "publish":
        # Only publishing is gated by the stream key. Reads are gated by
        # nginx auth_request instead - but only for HLS, which is the only
        # protocol browsers consume. A direct RTMP/RTSP/WebRTC/SRT pull
        # would bypass Alliance Auth entirely, so deny those.
        if action in ("read", "playback") and protocol in _DENIED_READ_PROTOCOLS:
            logger.info(
                "Intel Watcher: denied %s on protocol %r", action, protocol
            )
            return HttpResponseForbidden("denied")
        if action in _KNOWN_NON_PUBLISH_ACTIONS:
            return JsonResponse({"ok": True})
        logger.warning("Intel Watcher: denied unknown auth action %r", action)
        return HttpResponseForbidden("denied")

    # RTMP only populates `user`/`password` if OBS is configured with
    # ?user=&pass= query params, which we don't require - OBS is instead
    # configured with the stream key as the RTMP path itself (see
    # streamer_info.html and StreamKey.path_name), so fall back to that.
    user_field = payload.get("user")
    stream_key_value = user_field if isinstance(user_field, str) else ""
    if not stream_key_value:
        stream_key_value = _last_path_segment(payload.get("path"))

    try:
        stream_key = StreamKey.objects.select_related("user").get(
            key=stream_key_value
        )
    except StreamKey.DoesNotExist:
        # Deliberately identical response to the permission failure below -
        # this endpoint is reachable from the internet and distinct errors
        # would let callers probe which stream keys exist.
        logger.info(
            "Intel Watcher: rejected publish (unknown key), id=%s ip=%s",
            request_id,
            payload.get("ip"),
        )
        return HttpResponseForbidden("denied")

    owner = stream_key.user
    if not owner.is_active or not owner.has_perm("aa_intel_watcher.can_stream"):
        logger.warning(
            "Intel Watcher: rejected publish from %s (inactive or missing "
            "can_stream), id=%s",
            owner,
            request_id,
        )
        return HttpResponseForbidden("denied")

    # NOTE: never log the raw payload or the stream key - the key is a
    # credential, and `user`/`password` may contain OBS credentials.
    stream_key.go_live(key_value=stream_key_value, session_id=request_id)
    logger.info(
        "Intel Watcher: %s went live (session %s)", owner, request_id or "-"
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def mediamtx_unpublish(request):
    """Called (via MediaMTX's runOnUnavailable hook) when a stream ends."""
    secret = request.headers.get("X-Webhook-Secret", "")
    configured = _mediamtx_secret()
    if not configured or not secrets.compare_digest(secret, configured):
        return HttpResponseForbidden("denied")

    path = request.POST.get("path", "")
    stream_key_value = _last_path_segment(path)
    if not stream_key_value:
        return HttpResponseForbidden("denied")

    session_id = request.POST.get("source_id", "")
    if not isinstance(session_id, str):
        session_id = ""

    # Match on live_key too: if the key was regenerated while the stream was
    # still running, MediaMTX reports the *old* key in the path.
    candidates = StreamKey.objects.filter(
        Q(key=stream_key_value) | Q(live_key=stream_key_value)
    )
    for stream_key in candidates:
        # If we know which MediaMTX session went live, a callback carrying a
        # different source_id belongs to an older, already-ended session
        # racing a reconnect - ignore it instead of clearing is_live.
        if (
            session_id
            and stream_key.live_session_id
            and session_id != stream_key.live_session_id
        ):
            logger.info(
                "Intel Watcher: ignoring stale unpublish for %s "
                "(callback session %s, live session %s)",
                stream_key.user,
                session_id,
                stream_key.live_session_id,
            )
            continue
        stream_key.go_offline()
        logger.info("Intel Watcher: %s went offline", stream_key.user)
    return JsonResponse({"ok": True})
