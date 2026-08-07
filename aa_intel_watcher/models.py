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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.display_name or self.user.username

    def go_live(self):
        self.is_live = True
        self.last_seen = timezone.now()
        self.save(update_fields=["is_live", "last_seen"])

    def go_offline(self):
        self.is_live = False
        self.save(update_fields=["is_live"])

    @property
    def path_name(self):
        """MediaMTX / RTMP path name for this user's stream.

        OBS is configured with Server=rtmp://host:1935/live and
        Stream Key=<key>, so the path MediaMTX actually publishes to is
        "live/<key>" - keep this in sync with that.
        """
        return f"live/{self.key}"


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
