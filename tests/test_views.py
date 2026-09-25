import json
import logging
import urllib.error
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from aa_intel_watcher.models import ChatMessage, General, StreamKey

User = get_user_model()

PUBLISH_URL = "/intel-watcher/hooks/publish-auth/"
UNPUBLISH_URL = "/intel-watcher/hooks/unpublish/"
HLS_AUTH_URL = "/intel-watcher/hls-auth/"
STATUS_URL = "/intel-watcher/api/status/"
CHAT_URL = "/intel-watcher/api/chat/"
REGEN_URL = "/intel-watcher/api/regenerate-key/"


def grant(user, *codenames):
    ct = ContentType.objects.get_for_model(General)
    perms = Permission.objects.filter(content_type=ct, codename__in=codenames)
    user.user_permissions.add(*perms)
    # fresh instance so has_perm doesn't use a stale _perm_cache
    return User.objects.get(pk=user.pk)


def make_user(username="streamer", password="pw", **kwargs):
    return User.objects.create_user(username=username, password=password, **kwargs)


def post_auth(client, payload):
    if isinstance(payload, (bytes, str)):
        body = payload
    else:
        body = json.dumps(payload)
    return client.post(
        PUBLISH_URL, data=body, content_type="application/json"
    )


class PublishAuthTests(TestCase):
    def setUp(self):
        self.user = grant(make_user(), "can_stream")
        self.stream_key = StreamKey.objects.create(user=self.user)
        self.key = self.stream_key.key

    def publish_payload(self, **overrides):
        payload = {
            "user": "",
            "password": "",
            "ip": "10.0.0.1",
            "action": "publish",
            "path": f"live/{self.key}",
            "protocol": "rtmp",
            "id": "conn-uuid-1",
            "query": "",
        }
        payload.update(overrides)
        return payload

    def test_valid_publish_allowed_and_goes_live(self):
        r = post_auth(self.client, self.publish_payload())
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertTrue(self.stream_key.is_live)
        self.assertEqual(self.stream_key.live_key, self.key)
        self.assertEqual(self.stream_key.live_session_id, "conn-uuid-1")
        self.assertIsNotNone(self.stream_key.last_seen)

    def test_stream_key_via_user_field(self):
        # OBS configured with ?user=<key> style credentials
        r = post_auth(
            self.client, self.publish_payload(user=self.key, path="live")
        )
        self.assertEqual(r.status_code, 200)

    def test_trailing_slash_and_bare_path(self):
        r = post_auth(self.client, self.publish_payload(path=f"live/{self.key}/"))
        self.assertEqual(r.status_code, 200)
        self.stream_key.go_offline()
        r = post_auth(self.client, self.publish_payload(path=self.key))
        self.assertEqual(r.status_code, 200)

    def test_unknown_key_denied(self):
        r = post_auth(self.client, self.publish_payload(path="live/deadbeef"))
        self.assertEqual(r.status_code, 403)
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)

    def test_same_response_for_unknown_key_and_missing_permission(self):
        user2 = make_user(username="noperm")
        key2 = StreamKey.objects.create(user=user2)
        r1 = post_auth(self.client, self.publish_payload(path="live/deadbeef"))
        r2 = post_auth(
            self.client, self.publish_payload(path=f"live/{key2.key}")
        )
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertEqual(r1.content, r2.content)

    def test_missing_can_stream_denied(self):
        user2 = grant(make_user(username="basic"), "basic_access")
        key2 = StreamKey.objects.create(user=user2)
        r = post_auth(
            self.client, self.publish_payload(path=f"live/{key2.key}")
        )
        self.assertEqual(r.status_code, 403)

    def test_inactive_user_denied(self):
        self.user.is_active = False
        self.user.save()
        r = post_auth(self.client, self.publish_payload())
        self.assertEqual(r.status_code, 403)

    def test_superuser_allowed(self):
        su = make_user(username="admin", is_superuser=True, is_staff=True)
        key3 = StreamKey.objects.create(user=su)
        r = post_auth(
            self.client, self.publish_payload(path=f"live/{key3.key}")
        )
        self.assertEqual(r.status_code, 200)

    def test_malformed_payloads_deny_not_500(self):
        for body in [
            b"not json",
            b"{",
            b"null",
            b"[]",
            b'"string"',
            b"123",
            b"",
            b'{"action": ["publish"]}',
            b'{"action": "read", "protocol": ["hls"]}',
        ]:
            r = post_auth(self.client, body)
            self.assertIn(r.status_code, (200, 403), body)
            self.assertNotEqual(r.status_code, 500, body)

    def test_non_string_types_dont_500(self):
        r = post_auth(
            self.client,
            self.publish_payload(user=12345, path=12345),
        )
        self.assertEqual(r.status_code, 403)
        r = post_auth(
            self.client,
            self.publish_payload(user=["x"], path={"a": 1}),
        )
        self.assertEqual(r.status_code, 403)

    def test_missing_action_denied(self):
        payload = self.publish_payload()
        del payload["action"]
        r = post_auth(self.client, payload)
        self.assertEqual(r.status_code, 403)

    def test_unknown_action_denied(self):
        r = post_auth(self.client, self.publish_payload(action="defcon"))
        self.assertEqual(r.status_code, 403)

    def test_hls_read_allowed(self):
        r = post_auth(
            self.client,
            self.publish_payload(action="read", protocol="hls", path="live/x"),
        )
        self.assertEqual(r.status_code, 200)

    def test_non_hls_reads_denied(self):
        # Direct rtmp/rtsp/webrtc/srt pulls would bypass AA auth entirely.
        for proto in ("rtmp", "rtsp", "webrtc", "srt"):
            r = post_auth(
                self.client,
                self.publish_payload(
                    action="read", protocol=proto, path=f"live/{self.key}"
                ),
            )
            self.assertEqual(r.status_code, 403, proto)

    def test_read_with_missing_protocol_allowed(self):
        # don't break older/newer MediaMTX variants that omit the field
        r = post_auth(
            self.client, self.publish_payload(action="read", protocol=None)
        )
        self.assertEqual(r.status_code, 200)

    def test_api_metrics_pprof_actions_allowed(self):
        for action in ("api", "metrics", "pprof"):
            r = post_auth(self.client, self.publish_payload(action=action))
            self.assertEqual(r.status_code, 200, action)
        r = post_auth(
            self.client,
            self.publish_payload(action="playback", protocol="hls"),
        )
        self.assertEqual(r.status_code, 200)

    def test_playback_non_hls_denied(self):
        r = post_auth(
            self.client,
            self.publish_payload(action="playback", protocol="rtsp"),
        )
        self.assertEqual(r.status_code, 403)

    def test_oversized_body_denied(self):
        r = self.client.post(
            PUBLISH_URL,
            data="x" * (8192 + 1),
            content_type="application/json",
            # Django's test client sets CONTENT_LENGTH automatically
        )
        self.assertEqual(r.status_code, 403)

    def test_get_method_rejected(self):
        r = self.client.get(PUBLISH_URL)
        self.assertEqual(r.status_code, 405)

    def test_stream_key_not_logged(self):
        with self.assertLogs("aa_intel_watcher.views", level="INFO") as cm:
            post_auth(self.client, self.publish_payload())
        for record in cm.records:
            self.assertNotIn(self.key, record.getMessage())


class StreamerInfoTests(TestCase):
    """The RTMP server URL shown to streamers: explicit setting vs request
    host fallback - the fallback is what silently breaks behind HTTP-only
    proxies (Cloudflare etc.), so it must be flagged as inferred."""

    def setUp(self):
        self.user = grant(make_user(), "can_stream")
        self.client.force_login(self.user)

    def _context(self, host=None):
        # tests/settings.py has no TEMPLATES (the base template lives in
        # Alliance Auth), so intercept render() and inspect the context.
        extra = {"HTTP_HOST": host} if host else {}
        with mock.patch("aa_intel_watcher.views.render") as m:
            m.return_value = HttpResponse()
            self.client.get("/intel-watcher/streamer-info/", **extra)
        self.assertTrue(m.called)
        return m.call_args[0][2]

    @override_settings(INTEL_WATCHER_RTMP_HOST="media.example.com")
    def test_explicit_rtmp_host_used(self):
        ctx = self._context(host="auth.example.com")
        self.assertEqual(ctx["rtmp_server"], "rtmp://media.example.com:1935/live")
        self.assertFalse(ctx["rtmp_host_inferred"])

    def test_falls_back_to_request_host_flagged_inferred(self):
        ctx = self._context(host="auth.example.com")
        self.assertEqual(ctx["rtmp_server"], "rtmp://auth.example.com:1935/live")
        self.assertTrue(ctx["rtmp_host_inferred"])

    def test_request_host_port_stripped(self):
        ctx = self._context(host="auth.example.com:8443")
        self.assertEqual(ctx["rtmp_server"], "rtmp://auth.example.com:1935/live")

    def test_ipv6_request_host(self):
        ctx = self._context(host="[::1]:8000")
        self.assertEqual(ctx["rtmp_server"], "rtmp://::1:1935/live")

    def test_creates_stream_key(self):
        self.assertFalse(
            StreamKey.objects.filter(user=self.user).exists()
        )
        ctx = self._context()
        self.assertTrue(
            StreamKey.objects.filter(user=self.user).exists()
        )
        self.assertEqual(ctx["rtmp_path"], f"live/{ctx['stream_key']}")


class RtmpHostCheckTests(TestCase):
    def test_warns_when_unset(self):
        from aa_intel_watcher.checks import rtmp_host_check

        messages = rtmp_host_check(app_configs=None)
        self.assertEqual(
            [m.id for m in messages], ["aa_intel_watcher.W001"]
        )

    @override_settings(INTEL_WATCHER_RTMP_HOST="media.example.com")
    def test_silent_when_set(self):
        from aa_intel_watcher.checks import rtmp_host_check

        self.assertEqual(rtmp_host_check(app_configs=None), [])


class UnpublishTests(TestCase):
    def setUp(self):
        self.user = grant(make_user(), "can_stream")
        self.stream_key = StreamKey.objects.create(user=self.user)
        self.key = self.stream_key.key

    def unpublish(self, path=None, secret="test-webhook-secret", **extra):
        data = {"path": path if path is not None else f"live/{self.key}"}
        data.update(extra)
        headers = {}
        if secret is not None:
            headers["HTTP_X_WEBHOOK_SECRET"] = secret
        return self.client.post(UNPUBLISH_URL, data=data, **headers)

    def go_live(self, session_id="s1", key=None):
        self.stream_key.go_live(key_value=key or self.key, session_id=session_id)

    def test_correct_secret_marks_offline(self):
        self.go_live()
        r = self.unpublish()
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)
        self.assertEqual(self.stream_key.live_key, "")
        self.assertEqual(self.stream_key.live_session_id, "")

    def test_wrong_secret_denied(self):
        self.go_live()
        r = self.unpublish(secret="wrong")
        self.assertEqual(r.status_code, 403)
        self.stream_key.refresh_from_db()
        self.assertTrue(self.stream_key.is_live)

    def test_missing_secret_denied(self):
        self.go_live()
        r = self.unpublish(secret=None)
        self.assertEqual(r.status_code, 403)

    @override_settings(INTEL_WATCHER_MEDIAMTX_SECRET="")
    def test_blank_configured_secret_fails_closed(self):
        self.go_live()
        r = self.unpublish(secret="")
        self.assertEqual(r.status_code, 403)
        r = self.unpublish(secret="test-webhook-secret")
        self.assertEqual(r.status_code, 403)

    def test_bad_path_denied(self):
        r = self.unpublish(path="")
        self.assertEqual(r.status_code, 403)

    def test_unknown_path_is_idempotent_200(self):
        r = self.unpublish(path="live/nonexistent")
        self.assertEqual(r.status_code, 200)

    def test_stale_callback_ignored(self):
        # publish A (s1) -> disconnect -> publish B (s2) -> delayed
        # unpublish for A must not mark B offline.
        self.go_live(session_id="s2")
        r = self.unpublish(source_id="s1")
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertTrue(self.stream_key.is_live)

        # the real session's callback still works
        r = self.unpublish(source_id="s2")
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)

    def test_unpublish_without_source_id_still_works(self):
        # older MediaMTX configs that don't send source_id keep working
        self.go_live(session_id="s2")
        r = self.unpublish()
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)

    def test_regenerated_key_unpublish_matches_live_key(self):
        # stream went live with old key, key was regenerated, then the
        # stream ends: MediaMTX reports the OLD key in the path.
        old_key = self.key
        self.go_live(session_id="s1", key=old_key)
        self.stream_key.key = "newkey123"
        self.stream_key.save(update_fields=["key"])
        r = self.unpublish(path=f"live/{old_key}", source_id="s1")
        self.assertEqual(r.status_code, 200)
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)

    def test_get_method_rejected(self):
        r = self.client.get(UNPUBLISH_URL)
        self.assertEqual(r.status_code, 405)


class ApiStatusTests(TestCase):
    def setUp(self):
        self.viewer = grant(make_user(username="v"), "basic_access")
        self.streamer = grant(make_user(username="s"), "can_stream")
        self.stream_key = StreamKey.objects.create(user=self.streamer)

    def test_anonymous_redirected(self):
        r = self.client.get(STATUS_URL)
        self.assertEqual(r.status_code, 302)

    def test_no_permission_forbidden(self):
        self.client.force_login(make_user(username="noperm"))
        r = self.client.get(STATUS_URL)
        self.assertEqual(r.status_code, 403)

    def test_offline_streams_excluded(self):
        self.client.force_login(self.viewer)
        r = self.client.get(STATUS_URL)
        self.assertEqual(r.json()["streams"], [])

    def test_live_stream_listed_with_active_path(self):
        self.stream_key.go_live(key_value=self.stream_key.key, session_id="x")
        self.client.force_login(self.viewer)
        r = self.client.get(STATUS_URL)
        streams = r.json()["streams"]
        self.assertEqual(len(streams), 1)
        self.assertEqual(
            streams[0]["hls_url"],
            f"/hls/live/{self.stream_key.key}/index.m3u8",
        )
        self.assertEqual(streams[0]["display_name"], "s")

    def test_live_url_uses_old_key_after_regen(self):
        old_key = self.stream_key.key
        self.stream_key.go_live(key_value=old_key, session_id="x")
        self.stream_key.key = "rotated"
        self.stream_key.save(update_fields=["key"])
        self.client.force_login(self.viewer)
        r = self.client.get(STATUS_URL)
        streams = r.json()["streams"]
        self.assertEqual(
            streams[0]["hls_url"], f"/hls/live/{old_key}/index.m3u8"
        )


class LivenessReconcileTests(TestCase):
    def setUp(self):
        self.viewer = grant(make_user(username="v"), "basic_access")
        self.streamer = grant(make_user(username="s"), "can_stream")
        self.stream_key = StreamKey.objects.create(user=self.streamer)
        self.client.force_login(self.viewer)

    def go_live_aged(self):
        self.stream_key.go_live(session_id="s1")
        StreamKey.objects.filter(pk=self.stream_key.pk).update(
            last_seen=timezone.now() - timezone.timedelta(seconds=60)
        )
        self.stream_key.refresh_from_db()

    def test_no_internal_url_no_reconcile(self):
        # default setting: no probe, DB state trusted as before
        self.go_live_aged()
        r = self.client.get(STATUS_URL)
        self.assertEqual(len(r.json()["streams"]), 1)

    @override_settings(INTEL_WATCHER_HLS_INTERNAL_URL="http://mediamtx:8888")
    def test_dead_stream_marked_offline(self):
        self.go_live_aged()
        err = urllib.error.HTTPError(
            "u", 404, "nf", {}, None  # type: ignore[arg-type]
        )
        with mock.patch(
            "aa_intel_watcher.views.urllib.request.build_opener"
        ) as m:
            m.return_value.open.side_effect = err
            r = self.client.get(STATUS_URL)
        self.assertEqual(r.json()["streams"], [])
        self.stream_key.refresh_from_db()
        self.assertFalse(self.stream_key.is_live)

    @override_settings(INTEL_WATCHER_HLS_INTERNAL_URL="http://mediamtx:8888")
    def test_unreachable_mediamtx_keeps_state(self):
        self.go_live_aged()
        with mock.patch(
            "aa_intel_watcher.views.urllib.request.build_opener"
        ) as m:
            m.return_value.open.side_effect = urllib.error.URLError("down")
            r = self.client.get(STATUS_URL)
        self.assertEqual(len(r.json()["streams"]), 1)
        self.stream_key.refresh_from_db()
        self.assertTrue(self.stream_key.is_live)

    @override_settings(INTEL_WATCHER_HLS_INTERNAL_URL="http://mediamtx:8888")
    def test_fresh_stream_not_probed(self):
        self.stream_key.go_live(session_id="s1")  # last_seen = now
        with mock.patch(
            "aa_intel_watcher.views.urllib.request.build_opener"
        ) as m:
            r = self.client.get(STATUS_URL)
        m.assert_not_called()
        self.assertEqual(len(r.json()["streams"]), 1)


class ChatTests(TestCase):
    def setUp(self):
        self.user = grant(make_user(), "basic_access")
        self.client.force_login(self.user)

    def test_anonymous_redirected(self):
        self.client.logout()
        r = self.client.get(CHAT_URL)
        self.assertEqual(r.status_code, 302)

    def test_no_permission_forbidden(self):
        self.client.force_login(make_user(username="noperm"))
        self.assertEqual(self.client.get(CHAT_URL).status_code, 403)

    def test_post_and_get(self):
        r = self.client.post(CHAT_URL, {"message": "hello"})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(CHAT_URL)
        msgs = r.json()["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["message"], "hello")
        self.assertEqual(msgs[0]["user"], self.user.username)

    def test_empty_and_whitespace_rejected(self):
        self.assertEqual(
            self.client.post(CHAT_URL, {"message": "   "}).status_code, 400
        )
        self.assertEqual(
            self.client.post(CHAT_URL, {}).status_code, 400
        )

    def test_max_length_truncated(self):
        self.client.post(CHAT_URL, {"message": "x" * 600})
        self.assertEqual(ChatMessage.objects.get().message, "x" * 500)

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        r = client.post(CHAT_URL, {"message": "hi"})
        self.assertEqual(r.status_code, 403)

    def test_since_zero_returns_latest_not_oldest(self):
        for i in range(60):
            ChatMessage.objects.create(user=self.user, message=f"m{i}")
        r = self.client.get(CHAT_URL)
        msgs = r.json()["messages"]
        self.assertEqual(len(msgs), 50)
        self.assertEqual(msgs[0]["message"], "m10")
        self.assertEqual(msgs[-1]["message"], "m59")

    def test_incremental_pagination_by_id(self):
        msgs = [
            ChatMessage.objects.create(user=self.user, message=f"m{i}")
            for i in range(5)
        ]
        r = self.client.get(CHAT_URL, {"since": msgs[2].id})
        got = r.json()["messages"]
        self.assertEqual([m["id"] for m in got], [msgs[3].id, msgs[4].id])

    def test_backlog_drains_forward(self):
        for i in range(250):
            ChatMessage.objects.create(user=self.user, message=f"m{i}")
        first = ChatMessage.objects.order_by("id").first()
        r = self.client.get(CHAT_URL, {"since": first.id})
        got = r.json()["messages"]
        self.assertEqual(len(got), 200)
        self.assertEqual(got[-1]["id"], first.id + 200)

    def test_malformed_and_negative_since(self):
        ChatMessage.objects.create(user=self.user, message="m")
        for since in ("abc", "-5", "1.5", ""):
            r = self.client.get(CHAT_URL, {"since": since})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(r.json()["messages"]), 1)

    def test_xss_payload_stored_verbatim_escaped_by_client(self):
        payload = '<img src=x onerror=alert(1)>'
        self.client.post(CHAT_URL, {"message": payload})
        msgs = self.client.get(CHAT_URL).json()["messages"]
        # stored/returned verbatim - the frontend renders via textContent
        self.assertEqual(msgs[0]["message"], payload)


class RegenerateKeyTests(TestCase):
    def setUp(self):
        self.user = grant(make_user(), "can_stream")
        self.stream_key = StreamKey.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_regen_rotates_and_old_key_rejected(self):
        old = self.stream_key.key
        r = self.client.post(REGEN_URL)
        self.assertEqual(r.status_code, 200)
        new = r.json()["stream_key"]
        self.assertNotEqual(old, new)
        self.stream_key.refresh_from_db()
        self.assertEqual(self.stream_key.key, new)

        r = post_auth(
            self.client,
            {
                "action": "publish",
                "path": f"live/{old}",
                "protocol": "rtmp",
                "id": "x",
            },
        )
        self.assertEqual(r.status_code, 403)
        r = post_auth(
            self.client,
            {
                "action": "publish",
                "path": f"live/{new}",
                "protocol": "rtmp",
                "id": "x",
            },
        )
        self.assertEqual(r.status_code, 200)

    def test_get_rejected(self):
        self.assertEqual(self.client.get(REGEN_URL).status_code, 405)

    def test_permission_required(self):
        self.client.force_login(make_user(username="noperm"))
        self.assertEqual(self.client.post(REGEN_URL).status_code, 403)


class HlsAuthTests(TestCase):
    def test_anonymous_gets_401_not_redirect(self):
        r = self.client.get(HLS_AUTH_URL)
        self.assertEqual(r.status_code, 401)

    def test_no_permission_gets_401(self):
        self.client.force_login(make_user(username="noperm"))
        r = self.client.get(HLS_AUTH_URL)
        self.assertEqual(r.status_code, 401)

    def test_basic_access_gets_204(self):
        user = grant(make_user(), "basic_access")
        self.client.force_login(user)
        r = self.client.get(HLS_AUTH_URL)
        self.assertEqual(r.status_code, 204)

    def test_inactive_user_gets_401(self):
        user = grant(make_user(is_active=False), "basic_access")
        self.client.force_login(user)
        r = self.client.get(HLS_AUTH_URL)
        self.assertEqual(r.status_code, 401)

    def test_post_rejected(self):
        user = grant(make_user(), "basic_access")
        self.client.force_login(user)
        self.assertEqual(self.client.post(HLS_AUTH_URL).status_code, 405)


class ModelTests(TestCase):
    def test_path_names(self):
        user = make_user()
        sk = StreamKey.objects.create(user=user)
        self.assertEqual(sk.path_name, f"live/{sk.key}")
        self.assertEqual(sk.active_path_name, sk.path_name)
        sk.live_key = "oldkey"
        self.assertEqual(sk.active_path_name, "live/oldkey")

    def test_keys_unique(self):
        u1, u2 = make_user(username="a"), make_user(username="b")
        StreamKey.objects.create(user=u1, key="same")
        with self.assertRaises(Exception):
            StreamKey.objects.create(user=u2, key="same")

    def test_key_rotation_keeps_live_state_visible(self):
        user = grant(make_user(), "can_stream")
        sk = StreamKey.objects.create(user=user)
        sk.go_live(session_id="s1")
        sk.key = "rotated"
        sk.save(update_fields=["key"])
        self.assertTrue(sk.is_live)
        self.assertEqual(sk.active_path_name, f"live/{sk.live_key}")


class NoSecretLoggingTests(TestCase):
    def test_publish_denial_does_not_log_key_or_password(self):
        user = grant(make_user(), "can_stream")
        StreamKey.objects.create(user=user)
        logger_name = "aa_intel_watcher.views"
        with self.assertLogs(logger_name, level=logging.INFO) as cm:
            post_auth(
                self.client,
                {
                    "action": "publish",
                    "user": "SECRETUSER",
                    "password": "SECRETPASS",
                    "path": "live/SECRETKEY",
                    "id": "x",
                },
            )
        out = "\n".join(r.getMessage() for r in cm.records)
        self.assertNotIn("SECRETUSER", out)
        self.assertNotIn("SECRETPASS", out)
        self.assertNotIn("SECRETKEY", out)
