from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls


class IntelWatcherMenuItem(MenuItemHook):
    def __init__(self):
        MenuItemHook.__init__(
            self,
            _("Intel Watcher"),
            "fas fa-video",
            "aa_intel_watcher:index",
            navactive=["aa_intel_watcher:index", "aa_intel_watcher:streamer_info"],
        )

    def render(self, request):
        if request.user.has_perm("aa_intel_watcher.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return IntelWatcherMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "aa_intel_watcher", r"^intel-watcher/")
