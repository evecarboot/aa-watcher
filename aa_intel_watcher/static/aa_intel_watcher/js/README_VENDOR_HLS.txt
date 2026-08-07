hls.min.js in this folder is fetched and checksum-verified automatically by:

    python manage.py fetch_hls_js

Run that once (before `collectstatic`) - works the same whether this app is
installed bare metal (into a virtualenv) or pip-installed from GitHub inside
a Docker image, since the command finds its own installed package directory
rather than assuming a host file path. See aa_intel_watcher/management/commands/fetch_hls_js.py
for the pinned version/checksum.

It is intentionally NOT fetched from a CDN at page-load time - only once,
at install/build time, verified against a pinned sha256 - so no external
network request is made when corp/alliance members load this page. Keep
the whole stack self-hosted for opsec.

If you'd rather do it by hand: download the `hls.min.js` build asset from
https://github.com/video-dev/hls.js/releases and place it in this folder.
