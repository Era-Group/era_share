# -*- coding: utf-8 -*-
"""Settings-page + menu wiring tests (tasks 1.7 / 1.9).

Proves the Compliance Settings page actually persists values to
ir.config_parameter (the config_parameter binding works end to end) and that the
new actions/menus resolve.
"""
from odoo.tests import TransactionCase, tagged
from odoo.tools.convert import convert_file

from odoo.addons.era_crm_ai_agents_compliance.services.compliance_config import (
    ComplianceConfig,
)

_SEED = "data/ir_config_parameter_data.xml"


@tagged("post_install", "-at_install")
class TestSettings(TransactionCase):

    def test_settings_roundtrip_to_config_params(self):
        self.env["res.config.settings"].create({
            "crm_ai_working_end": "23:30",
            "crm_ai_prayer_enabled": False,
            "crm_ai_opt_out_window_hours": 48,
        }).execute()
        icp = self.env["ir.config_parameter"]
        self.assertEqual(
            icp.get_param("era_crm_ai_agents_compliance.working_end"), "23:30")
        cfg = ComplianceConfig(self.env)
        self.assertFalse(cfg.b("prayer_enabled"))      # toggle persisted as OFF
        self.assertEqual(cfg.i("opt_out_window_hours"), 48)

    def test_seed_params_exist_and_are_noupdate(self):
        # Every setting must be seeded (so toggling OFF persists) and marked
        # noupdate so manager edits survive upgrades.
        imd = self.env["ir.model.data"].search([
            ("module", "=", "era_crm_ai_agents_compliance"),
            ("model", "=", "ir.config_parameter")])
        self.assertTrue(imd, "no seeded compliance config params found")
        self.assertTrue(all(imd.mapped("noupdate")),
                        "seeded params must be noupdate=1")
        icp = self.env["ir.config_parameter"]
        # Seed value matches DEFAULTS byte-for-byte (representation consistency).
        self.assertEqual(
            icp.get_param("era_crm_ai_agents_compliance.prayer_enabled"), "True")

    def test_upgrade_does_not_overwrite_manager_toggle_off(self):
        # Pre-existing install where the manager already toggled a param OFF...
        icp = self.env["ir.config_parameter"]
        key = "era_crm_ai_agents_compliance.prayer_enabled"
        icp.set_param(key, "False")
        # ...then the module is upgraded, re-loading the seed (noupdate=1).
        convert_file(self.env, "era_crm_ai_agents_compliance", _SEED, {},
                     mode="update", noupdate=False)
        # noupdate must NOT clobber the manager's OFF choice back to True.
        self.assertEqual(icp.get_param(key), "False")
        self.assertFalse(ComplianceConfig(self.env).b("prayer_enabled"))

    def test_actions_and_menus_exist(self):
        for xmlid in (
            "action_crm_ai_compliance_settings",
            "action_crm_ai_norm_term",
            "menu_crm_ai_compliance_norms",
            "menu_crm_ai_compliance_settings",
            "menu_crm_ai_compliance_consent",
            "menu_crm_ai_compliance_status",
            "menu_crm_ai_compliance_dsar",
        ):
            self.assertTrue(
                self.env.ref("era_crm_ai_agents_compliance." + xmlid),
                "missing %s" % xmlid)
