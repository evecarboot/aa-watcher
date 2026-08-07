from django.urls import path

from . import views

app_name = "aa_intel_watcher"

urlpatterns = [
    path("", views.index, name="index"),
    path("streamer-info/", views.streamer_info, name="streamer_info"),
    path("api/status/", views.api_status, name="api_status"),
    path("api/chat/", views.api_chat, name="api_chat"),
    path("api/regenerate-key/", views.api_regenerate_key, name="api_regenerate_key"),
    # Called by MediaMTX on the server itself, not by browsers.
    path(
        "hooks/publish-auth/",
        views.mediamtx_publish_auth,
        name="mediamtx_publish_auth",
    ),
    path("hooks/unpublish/", views.mediamtx_unpublish, name="mediamtx_unpublish"),
]
