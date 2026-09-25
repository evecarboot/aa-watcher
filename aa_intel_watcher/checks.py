"""Django system checks for aa_intel_watcher.

Registered from AaIntelWatcherConfig.ready(). Keep these Warning-level
only: they flag configurations that are *probably* wrong but legitimate in
simple installs, so they must never block a deployment.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def rtmp_host_check(app_configs, **kwargs):
    """Warn when INTEL_WATCHER_RTMP_HOST is left to request-host inference.

    When unset, streamer_info shows streamers the hostname of whichever
    request rendered the page. That silently produces a wrong OBS server
    URL whenever the Alliance Auth hostname is behind an HTTP(S)-only proxy
    (Cloudflare, CDN, load balancer) or MediaMTX lives on different
    infrastructure - OBS then fails before it ever reaches MediaMTX.
    """
    if getattr(settings, "INTEL_WATCHER_RTMP_HOST", None):
        return []
    return [
        Warning(
            "INTEL_WATCHER_RTMP_HOST is not set - the OBS server address "
            "shown to streamers is inferred from each request's hostname.",
            hint=(
                "If your Alliance Auth hostname is behind a proxy/CDN that "
                "doesn't forward TCP/1935, or MediaMTX runs elsewhere, set "
                "INTEL_WATCHER_RTMP_HOST to a hostname that reaches MediaMTX "
                "directly (see README). Safe to silence via "
                "SILENCED_SYSTEM_CHECKS if the web host accepts RTMP."
            ),
            obj="aa_intel_watcher",
            id="aa_intel_watcher.W001",
        )
    ]
