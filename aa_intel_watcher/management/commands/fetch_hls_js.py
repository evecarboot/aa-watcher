"""Download and verify hls.js into this app's own static folder.

Runs the same way whether aa_intel_watcher is pip-installed bare metal or
inside a Docker image - it locates its own installed package directory, so
there's no host file path to hunt down. Run this once, before
`collectstatic`.
"""

import base64
import hashlib
import urllib.error
import urllib.request
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

# Pinned version + checksum - bump both together when upgrading.
# Checksum (sha256, base64) taken from:
# https://data.jsdelivr.com/v1/packages/npm/hls.js@<version>?structure=flat
HLS_JS_VERSION = "1.6.15"
HLS_JS_URL = f"https://cdn.jsdelivr.net/npm/hls.js@{HLS_JS_VERSION}/dist/hls.min.js"
HLS_JS_SHA256_B64 = "QTqD4rsMd+0L8L4QXVOdF+9F39mEoLE+zTsUqQE4OTg="

TARGET = (
    Path(__file__).resolve().parent.parent.parent
    / "static"
    / "aa_intel_watcher"
    / "js"
    / "hls.min.js"
)


class Command(BaseCommand):
    help = (
        "Download hls.js (pinned version, checksum-verified) into this "
        "app's static folder, wherever it's installed. Run before "
        "collectstatic. This is a one-time, build-time download - members "
        "loading the page never make an external request, hls.min.js is "
        "served from your own static files."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-download even if hls.min.js is already present.",
        )

    def handle(self, *args, **options):
        if TARGET.exists() and not options["force"]:
            self.stdout.write(
                self.style.WARNING(
                    f"{TARGET} already exists, skipping (use --force to re-download)."
                )
            )
            return

        self.stdout.write(f"Downloading hls.js v{HLS_JS_VERSION} from {HLS_JS_URL} ...")
        try:
            with urllib.request.urlopen(HLS_JS_URL, timeout=30) as response:
                data = response.read()
        except (urllib.error.URLError, OSError) as exc:
            raise CommandError(f"Failed to download hls.js: {exc}") from exc

        digest = base64.b64encode(hashlib.sha256(data).digest()).decode()
        if digest != HLS_JS_SHA256_B64:
            raise CommandError(
                "Downloaded hls.js failed checksum verification (expected "
                f"{HLS_JS_SHA256_B64}, got {digest}). Aborting - file was NOT written."
            )

        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_bytes(data)
        self.stdout.write(
            self.style.SUCCESS(f"Verified and wrote {TARGET} ({len(data)} bytes).")
        )
