import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def _generate_stream_key():
    return secrets.token_hex(16)


class General(models.Model):
    """Dummy model, only used to attach app-level permissions to."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", "Can access the Intel Watcher page"),
            ("can_stream", "Can broadcast a stream to the Intel Watcher"),
        )


class StreamKey(models.Model):
    """Per-user OBS stream key and live status."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="intel_watcher_stream_key",
    )
    key = models.CharField(max_length=64, unique=True, default=_generate_stream_key)
    display_name = models.CharField(max_length=100, blank=True)
    is_live = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)
    # The key MediaMTX accepted for the *current* live session. Differs from
    # `key` when the key was regenerated while a stream was still running -
    # MediaMTX keeps serving the stream under the old key's path until it ends.
    live_key = models.CharField(max_length=64, blank=True)
    # MediaMTX's source/connection UUID (auth request `id` / $MTX_SOURCE_ID) of
    # the current live session. Used to ignore unpublish callbacks for older,
    # already-ended sessions racing a reconnect with the same key.
    live_session_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.display_name or self.user.username

    def go_live(self, key_value=None, session_id=""):
        self.is_live = True
        self.last_seen = timezone.now()
        self.live_key = key_value or self.key
        self.live_session_id = session_id or ""
        self.save(
            update_fields=["is_live", "last_seen", "live_key", "live_session_id"]
        )

    def go_offline(self):
        self.is_live = False
        self.live_key = ""
        self.live_session_id = ""
        self.save(update_fields=["is_live", "live_key", "live_session_id"])

    @property
    def path_name(self):
        """MediaMTX / RTMP path name for this user's stream.

        OBS is configured with Server=rtmp://host:1935/live and
        Stream Key=<key>, so the path MediaMTX actually publishes to is
        "live/<key>" - keep this in sync with that.
        """
        return f"live/{self.key}"

    @property
    def active_path_name(self):
        """Path MediaMTX is actually serving right now.

        Uses the key that was authenticated when the current stream started
        (live_key), which differs from path_name if the key was regenerated
        while the stream was still running.
        """
        return f"live/{self.live_key or self.key}"


class ChatMessage(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    message = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        default_permissions = ()
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user}: {self.message[:30]}"
