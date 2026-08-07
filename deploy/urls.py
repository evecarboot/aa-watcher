"""Project-level urls.py additions required for aa-intel-watcher.

Why this file exists: Alliance Auth wraps every view reachable through an
app's `url_hook` registration (see aa_intel_watcher/auth_hooks.py) in
`main_character_required` -> `login_required`, unconditionally, at the core
`allianceauth/urls.py` level - see the `decorate_url_patterns` loop over
`get_hooks('url_hook')`. This happens no matter what decorators (or lack of
them) are on the view itself.

MediaMTX's publish-auth/unpublish webhooks are called server-to-server, with
no Alliance Auth session cookie, so they can NEVER pass `login_required` -
they must be wired up directly in the *project's* urls.py instead, entirely
outside the url_hook mechanism.

In the docker-based setup (see deploy/docker-compose.mediamtx.yml), the
project's urls.py is `conf/urls.py`, mounted to
`/home/allianceauth/myauth/myauth/urls.py`. Add the two `path(...)` entries
below to that file, ABOVE `path("", include(urls))` (Django uses first-match
routing, and aa_intel_watcher's own urls.py may still define the same paths
as unreachable/shadowed - fine to leave alone, but they're not registered
there anymore as of this writing).

Example resulting conf/urls.py:

    from django.urls import include, path

    from allianceauth import urls
    from aa_intel_watcher.views import mediamtx_publish_auth, mediamtx_unpublish

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
        path("", include(urls)),
    ]

    handler500 = "allianceauth.views.Generic500Redirect"
    handler404 = "allianceauth.views.Generic404Redirect"
    handler403 = "allianceauth.views.Generic403Redirect"
    handler400 = "allianceauth.views.Generic400Redirect"

After editing, rebuild/restart allianceauth_gunicorn (and any other
containers sharing the same image) for the change to take effect.
"""
