"""URLConf mirroring the documented production wiring: the MediaMTX webhooks
and the nginx auth_request endpoint live in the project's urls.py (outside
the app's url_hook / login_required wrapping), the rest of the app is
included normally under /intel-watcher/.
"""

from django.urls import include, path

from aa_intel_watcher.views import (
    hls_auth,
    mediamtx_publish_auth,
    mediamtx_unpublish,
)

urlpatterns = [
    path(
        "intel-watcher/hooks/publish-auth/",
        mediamtx_publish_auth,
        name="iw_publish_auth",
    ),
    path(
        "intel-watcher/hooks/unpublish/",
        mediamtx_unpublish,
        name="iw_unpublish",
    ),
    path("intel-watcher/hls-auth/", hls_auth, name="iw_hls_auth"),
    path("intel-watcher/", include("aa_intel_watcher.urls")),
]
