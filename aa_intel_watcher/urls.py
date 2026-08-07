from django.urls import path

from . import views

app_name = "aa_intel_watcher"

# NOTE: MediaMTX's webhook views (mediamtx_publish_auth, mediamtx_unpublish)
# are deliberately NOT registered here. Alliance Auth wraps every view
# reachable through an app's `url_hook` registration (see auth_hooks.py) in
# `main_character_required`/`login_required` at the core urls.py level -
# this happens unconditionally, regardless of any decorators on the view
# itself, so a webhook endpoint registered this way could never be reached
# by MediaMTX (an unauthenticated server-to-server caller). Instead, those
# two views must be wired up directly in the project's own urls.py
# (e.g. conf/urls.py in the docker setup) - see deploy/urls.py for the
# snippet to add there.
urlpatterns = [
    path("", views.index, name="index"),
    path("streamer-info/", views.streamer_info, name="streamer_info"),
    path("api/status/", views.api_status, name="api_status"),
    path("api/chat/", views.api_chat, name="api_chat"),
    path("api/regenerate-key/", views.api_regenerate_key, name="api_regenerate_key"),
]
