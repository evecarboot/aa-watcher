from django.contrib import admin

from .models import ChatMessage, IntelWatcherSettings, StreamKey


@admin.register(IntelWatcherSettings)
class IntelWatcherSettingsAdmin(admin.ModelAdmin):
    """Singleton admin: edit the one row, never add or delete."""

    list_display = ("chat_enabled",)

    def has_add_permission(self, request):
        return not IntelWatcherSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StreamKey)
class StreamKeyAdmin(admin.ModelAdmin):
    list_display = ("user", "display_name", "is_live", "last_seen", "key")
    readonly_fields = ("key", "created_at")
    search_fields = ("user__username", "display_name")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("user", "message", "created_at")
    readonly_fields = ("user", "message", "created_at")
    search_fields = ("user__username", "message")
