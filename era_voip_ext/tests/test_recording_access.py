import base64

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestVoipCallRecordingAccess(HttpCase):
    """Guards the /voip_call/recording/<id> endpoint against the IDOR /
    cross-record leakage fixed in era_voip_ext (see controllers/voip_call.py).

    Base ``voip`` restricts a regular user to their own calls
    (``user_id = user.id``). The endpoint must honour that: serve the owner
    their own recording, and return 404 — never someone else's audio — for a
    call the user cannot read or for an id that does not exist.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "recording-test-pw"
        internal = cls.env.ref("base.group_user")
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.user_a = Users.create({
            "name": "VoIP User A",
            "login": "era_voip_user_a",
            "password": cls.password,
            "group_ids": [(6, 0, internal.ids)],
        })
        cls.user_b = Users.create({
            "name": "VoIP User B",
            "login": "era_voip_user_b",
            "password": cls.password,
            "group_ids": [(6, 0, internal.ids)],
        })

        Call = cls.env["voip.call"]
        cls.call_a = Call.create({
            "phone_number": "+966500000001",
            "user_id": cls.user_a.id,
        })
        cls.call_b = Call.create({
            "phone_number": "+966500000002",
            "user_id": cls.user_b.id,
        })

        cls.audio_a = b"FAKE-AUDIO-OWNED-BY-A"
        cls.audio_b = b"FAKE-AUDIO-OWNED-BY-B"
        cls._attach(cls.call_a, cls.audio_a)
        cls._attach(cls.call_b, cls.audio_b)

    @classmethod
    def _attach(cls, call, data):
        return cls.env["ir.attachment"].create({
            "name": "recording.webm",
            "res_model": "voip.call",
            "res_id": call.id,
            "mimetype": "audio/webm",
            "datas": base64.b64encode(data),
        })

    def test_owner_can_fetch_own_recording(self):
        self.authenticate("era_voip_user_a", self.password)
        res = self.url_open(f"/voip_call/recording/{self.call_a.id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, self.audio_a)

    def test_cannot_fetch_other_users_recording(self):
        self.authenticate("era_voip_user_a", self.password)
        res = self.url_open(f"/voip_call/recording/{self.call_b.id}")
        self.assertEqual(res.status_code, 404)
        # The core guarantee: B's audio must never be served to A.
        self.assertNotIn(self.audio_b, res.content)

    def test_missing_call_does_not_leak_any_recording(self):
        # The old code fell through to a global "recording.webm" search and
        # returned an arbitrary call's audio for an unknown id.
        self.authenticate("era_voip_user_a", self.password)
        missing_id = max(self.call_a.id, self.call_b.id) + 100000
        res = self.url_open(f"/voip_call/recording/{missing_id}")
        self.assertEqual(res.status_code, 404)
        self.assertNotIn(self.audio_a, res.content)
        self.assertNotIn(self.audio_b, res.content)
