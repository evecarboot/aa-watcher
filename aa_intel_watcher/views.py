import json
import logging
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .models import ChatMessage, StreamKey

logger = logging.getLogger(__name__)

# Base URL where MediaMTX's HLS output is reverse-proxied to (see README).
HLS_BASE_URL = getattr(settings, "INTEL_WATCHER_HLS_BASE_URL", "/hls")

# Shared secret MediaMTX must send back on the unpublish hook. Publish
# authentication itself is validated via the stream key, this secret just
# protects the "mark offline" webhook from being called by anyone else.
MEDIAMTX_WEBHOOK_SECRET = getattr(settings, "INTEL_WATCHER_MEDIAMTX_SECRET", "")

MAX_CHAT_LENGTH = 500


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
def index(request):
    context = {
        "can_stream": request.user.has_perm("aa_intel_watcher.can_stream"),
        "active_tab": "viewing",
    }
    return render(request, "aa_intel_watcher/index.html", context)


@login_required
@permission_required("aa_intel_watcher.can_stream", raise_exception=True)
def streamer_info(request):
    stream_key, _created = StreamKey.objects.get_or_create(user=request.user)
    rtmp_host = getattr(settings, "INTEL_WATCHER_RTMP_HOST", None) or request.get_host().split(":")[0]
    context = {
        "can_stream": True,
        "active_tab": "streamer_info",
        "stream_key": stream_key.key,
        "rtmp_path": stream_key.path_name,
        "rtmp_server": f"rtmp://{rtmp_host}:1935/live",
    }
    return render(request, "aa_intel_watcher/streamer_info.html", context)


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
@require_GET
def api_status(request):
    """Returns which approved streamers are currently live."""
    live_streams = (
        StreamKey.objects.filter(is_live=True)
        .select_related("user")
        .order_by("last_seen")
    )
    streams = [
        {
            "display_name": stream.display_name or stream.user.username,
            "hls_url": f"{HLS_BASE_URL}/{stream.path_name}/index.m3u8",
        }
        for stream in live_streams
    ]
    return JsonResponse({"streams": streams})


@login_required
@permission_required("aa_intel_watcher.basic_access", raise_exception=True)
@require_http_methods(["GET", "POST"])
def api_chat(request):
    """GET polls for new messages since ?since=<id>. POST sends a message."""
    if request.method == "POST":
        message = (request.POST.get("message") or "").strip()
        if not message:
            return JsonResponse({"error": "empty message"}, status=400)
        message = message[:MAX_CHAT_LENGTH]
        ChatMessage.objects.create(user=request.user, message=message)
        return JsonResponse({"ok": True})

    since_id = request.GET.get("since") or 0
    try:
        since_id = int(since_id)
    except ValueError:
        since_id = 0

    messages = (
        ChatMessage.objects.filter(id__gt=since_id)
        .select_related("user")
        .order_by("created_at")[:200]
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


# ---------------------------------------------------------------------------
# Webhooks called by MediaMTX itself (server-to-server, not by browsers).
# MediaMTX should be configured to only reach these over localhost - see the
# nginx/mediamtx samples in deploy/. We additionally require a shared secret
# on the unpublish hook as defense in depth.
# ---------------------------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
def mediamtx_publish_auth(request):
    """MediaMTX external auth webhook, called before a publish is allowed.

    MediaMTX POSTs a JSON body including `user` (the stream key supplied by
    OBS) and `action` (e.g. "publish"). We return 200 to allow, any other
    status to deny.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return HttpResponseForbidden("bad payload")

    if payload.get("action") != "publish":
        # Only gate publishing here; reading is gated separately by nginx.
        return JsonResponse({"ok": True})

    stream_key_value = payload.get("user") or ""
    try:
        stream_key = StreamKey.objects.select_related("user").get(key=stream_key_value)
    except StreamKey.DoesNotExist:
        logger.warning("Intel Watcher: rejected publish with unknown stream key")
        return HttpResponseForbidden("unknown stream key")

    if not stream_key.user.has_perm("aa_intel_watcher.can_stream"):
        logger.warning(
            "Intel Watcher: rejected publish from %s - missing can_stream permission",
            stream_key.user,
        )
        return HttpResponseForbidden("not approved to stream")

    stream_key.go_live()
    return JsonResponse({"ok": True})


@csrf_exempt
@require_http_methods(["POST"])
def mediamtx_unpublish(request):
    """Called (e.g. via MediaMTX's runOnUnpublish hook) when a stream ends."""
    secret = request.headers.get("X-Webhook-Secret", "")
    if not MEDIAMTX_WEBHOOK_SECRET or not secrets.compare_digest(
        secret, MEDIAMTX_WEBHOOK_SECRET
    ):
        return HttpResponseForbidden("bad secret")

    path = request.POST.get("path", "")
    try:
        user_id = int(path.removeprefix("stream_"))
    except ValueError:
        return HttpResponseForbidden("bad path")

    StreamKey.objects.filter(user_id=user_id).update(is_live=False)
    return JsonResponse({"ok": True})
