# aa-intel-watcher

Compatible with Alliance Auth 4.x and 5.x (tested against 5.2).

Alliance Auth app that adds a corp/alliance-only "Intel Watcher" section:
members can stream from OBS to it, and it's viewable only by logged-in
Alliance Auth users with the right permission - no YouTube/Twitch, no
public exposure.

One sidebar entry ("Intel Watcher") with two tabs inside it:

- **Intel Viewing** - a grid of every currently-live stream (one tile per
  streamer, no clutter around them) plus chat. This is the page everyone
  with `basic_access` lands on.
- **Streamer Info** - OBS server/key details and the "regenerate key"
  button. Only shown/reachable to users with `can_stream`.

- **Streaming server:** [MediaMTX](https://github.com/bluenviron/mediamtx) -
  a single binary, no separate database or website. Accepts RTMP from OBS,
  serves HLS for browser playback.
- **Who can push a stream (`can_stream` permission):** managed the normal
  Alliance Auth way, via groups/states in the admin. No separate "approve
  streamer" workflow to build/maintain.
- **Auto-show whoever is live:** MediaMTX calls a webhook in this app on
  publish/unpublish; the page polls a small JSON endpoint and swaps the
  player to whichever approved streamer is currently live.
- **Chat:** plain polling AJAX chat stored in the Alliance Auth database.
  No websockets/Channels/Redis pub-sub required.
- **Who can view (`basic_access` permission):** gates both the page and,
  via the nginx sample, the actual video segments - not just the UI around
  them.
- **Multiple simultaneous streamers:** each live streamer gets their own
  tile with independent native video controls. A "Solo audio" button on
  each tile mutes every other tile - handy if you and another streamer
  both end up in the same system and don't want two audio tracks playing
  at once.
- **Theming:** every template extends `allianceauth/base-bs5.html` and
  only uses standard Bootstrap classes, so the app automatically matches
  whatever Bootswatch theme a user has selected in Alliance Auth - nothing
  to configure.

Install instructions coming soon.
