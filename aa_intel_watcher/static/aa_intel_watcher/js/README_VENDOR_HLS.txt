hls.min.js in this folder is fetched and checksum-verified automatically by:

    python manage.py fetch_hls_js

Run that once (before `collectstatic`) - works the same whether this app is
installed bare metal (into a virtualenv) or pip-installed from GitHub inside
a Docker image, since the command finds its own installed package directory
rather than assuming a host file path. See aa_intel_watcher/management/commands/fetch_hls_js.py
for the pinned version/checksum.

The viewer template loads this vendored copy by default, so corp/alliance
members' browsers never make an external request. If this file is missing
(fetch_hls_js wasn't run), the template falls back to the same pinned,
SRI-hashed version on the jsdelivr CDN so the page still works - but run
fetch_hls_js to keep the whole stack self-hosted for opsec.

If you'd rather do it by hand: download the `hls.min.js` build asset from
https://github.com/video-dev/hls.js/releases (the version pinned in
fetch_hls_js.py) and place it in this folder.
