from django.apps import AppConfig

from . import __version__


class AaIntelWatcherConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aa_intel_watcher"
    label = "aa_intel_watcher"
    verbose_name = f"Intel Watcher v{__version__}"

    def ready(self):
        from . import checks  # noqa: F401 - registers system checks
