"""Project-level urls.py additions required for aa-intel-watcher.

Why this file exists: Alliance Auth wraps every view reachable through an
app's `url_hook` registration (see aa_intel_watcher/auth_hooks.py) in
`main_character_required` -> `login_required`, unconditionally, at the core
`allianceauth/urls.py` level - see the `decorate_url_patterns` loop over
`get_hooks('url_hook')`. This happens no matter what decorators (or lack of
them) are on the view itself.

Three endpoints must NOT live behind that wrapper:

* `mediamtx_publish_auth` / `mediamtx_unpublish` - called server-to-server
  by MediaMTX with no Alliance Auth session cookie, so they can NEVER pass
  `login_required`.
* `hls_auth` - the target of nginx's `auth_request` for /hls/. It must be
  free to return a bare 401 to anonymous users. Behind login_required it
  would emit a 302 to the login page, which auth_request turns into a 500
  for the real request instead of a clean denial.

So all three must be wired up directly in the *project's* urls.py, entirely
outside the url_hook mechanism.

In the docker-based setup (see deploy/docker-compose.mediamtx.yml), the
project's urls.py is `conf/urls.py`, mounted to
`/home/allianceauth/myauth/myauth/urls.py`. Add the three `path(...)`
entries below to that file, ABOVE `path("", include(urls))` (Django uses
first-match routing).

Example resulting conf/urls.py:

    from django.urls import include, path

    from allianceauth import urls
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
        path(
            "intel-watcher/hls-auth/",
            hls_auth,
            name="iw_hls_auth",
        ),
        path("", include(urls)),
    ]

    handler500 = "allianceauth.views.Generic500Redirect"
    handler404 = "allianceauth.views.Generic404Redirect"
    handler403 = "allianceauth.views.Generic403Redirect"
    handler400 = "allianceauth.views.Generic400Redirect"

After editing, rebuild/restart allianceauth_gunicorn (and any other
containers sharing the same image) for the change to take effect.
"""
