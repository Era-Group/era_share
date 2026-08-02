"""Client SSRF/secret guards and record-rule access isolation."""
from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import tagged

from .common import EVCommon


@tagged("post_install", "-at_install")
class TestClientGuards(EVCommon):

    def test_base_url_scheme_validation(self):
        Client = self.env["email.verification.client"]
        # https ok
        Client._validate_base_url("https://verifier.example")
        # http on loopback ok
        Client._validate_base_url("http://127.0.0.1:8080")
        Client._validate_base_url("http://localhost:8080")
        # http on a public host is refused (no plaintext to the internet)
        with self.assertRaises(UserError):
            Client._validate_base_url("http://verifier.example")
        # garbage refused
        with self.assertRaises(UserError):
            Client._validate_base_url("not a url")

    def test_missing_key_raises_before_network(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.api_key", "")
        Client = self.env["email.verification.client"]
        with self.assertRaises(UserError):
            # must fail on the key check, never attempt a socket
            Client._request("GET", "/v1/jobs/x")

    def test_missing_base_url_raises(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "era_email_verification.base_url", "")
        Client = self.env["email.verification.client"]
        with self.assertRaises(UserError):
            Client._request("GET", "/v1/jobs/x")

    def test_service_methods_are_private_to_rpc(self):
        Client = self.env["email.verification.client"]
        for method_name in (
                "verify_one", "create_job", "get_job", "get_results", "delete_job"):
            with self.subTest(method=method_name), self.assertRaises(AccessError):
                get_public_method(Client, method_name)


@tagged("post_install", "-at_install")
class TestAccessIsolation(EVCommon):

    def _make_user(self, login, manager=False):
        # Real module users are internal users (base.group_user) plus the EV role.
        groups = [
            self.env.ref("base.group_user").id,
            self.env.ref("era_email_verification.group_email_verification_user").id,
        ]
        if manager:
            groups.append(
                self.env.ref("era_email_verification.group_email_verification_manager").id)
        return self.env["res.users"].create({
            "name": login, "login": login, "email": "%s@test.com" % login,
            "group_ids": [(6, 0, groups)],
        })

    def test_user_cannot_read_other_users_batch(self):
        alice = self._make_user("ev_alice")
        bob = self._make_user("ev_bob")
        batch_alice = self.Batch.with_user(alice).create({"source": "manual"})

        # Bob (plain user) must not see Alice's batch via search.
        visible = self.Batch.with_user(bob).search([("id", "=", batch_alice.id)])
        self.assertFalse(visible)
        # Direct read is denied by the record rule.
        with self.assertRaises(AccessError):
            self.Batch.with_user(bob).browse(batch_alice.id).read(["name"])

    def test_manager_can_read_any_batch(self):
        alice = self._make_user("ev_alice2")
        boss = self._make_user("ev_boss", manager=True)
        batch_alice = self.Batch.with_user(alice).create({"source": "manual"})
        visible = self.Batch.with_user(boss).search([("id", "=", batch_alice.id)])
        self.assertTrue(visible)
