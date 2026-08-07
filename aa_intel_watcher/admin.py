from django.contrib import admin

from .models import ChatMessage, StreamKey


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
