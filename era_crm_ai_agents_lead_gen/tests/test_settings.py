# -*- coding: utf-8 -*-
"""Settings round-trip + seed-noupdate + upgrade-preserves-OFF (16.3/16.7).

Mirrors the Compliance settings test: proves the config_parameter binding
persists values (including a toggle turned OFF — the first-OFF-lost fix), that
every setting is seeded and noupdate, and that re-loading the seed on upgrade
does NOT clobber a manager's OFF choice.
"""
from odoo.tests import TransactionCase, tagged
from odoo.tools.convert import convert_file

_SEED = "data/ir_config_parameter_data.xml"
_NS = "era_crm_ai_agents_lead_gen."


@tagged("post_install", "-at_install")
class TestSettings(TransactionCase):

    def test_settings_roundtrip_to_config_params(self):
        self.env["res.config.settings"].create({
            "lead_gen_enabled": True,
            "lead_gen_fetch_decision_makers": False,
            "lead_gen_target_regions": "Jeddah, Riyadh",
            "lead_gen_daily_cap": 25,
            "lead_gen_dedup_mode": "update",
        }).execute()
        icp = self.env["ir.config_parameter"].sudo()
        self.assertEqual(icp.get_param(_NS + "enabled"), "True")
        # The OFF toggle must persist as the explicit string, not vanish.
        self.assertEqual(icp.get_param(_NS + "fetch_decision_makers"), "False")
        self.assertEqual(icp.get_param(_NS + "target_regions"), "Jeddah, Riyadh")
        self.assertEqual(icp.get_param(_NS + "daily_cap"), "25")
        self.assertEqual(icp.get_param(_NS + "dedup_mode"), "update")

    def test_seed_params_exist_and_are_noupdate(self):
        imd = self.env["ir.model.data"].search([
            ("module", "=", "era_crm_ai_agents_lead_gen"),
            ("model", "=", "ir.config_parameter")])
        self.assertTrue(imd, "no seeded lead-gen config params found")
        self.assertTrue(all(imd.mapped("noupdate")),
                        "seeded params must be noupdate=1")
        icp = self.env["ir.config_parameter"].sudo()
        # Conservative defaults present.
        self.assertEqual(icp.get_param(_NS + "enabled"), "False")
        self.assertEqual(icp.get_param(_NS + "fetch_decision_makers"), "False")
        self.assertEqual(icp.get_param(_NS + "dedup_mode"), "skip")
        self.assertEqual(icp.get_param(_NS + "default_jurisdiction"), "sa")
        self.assertEqual(icp.get_param(_NS + "decision_maker_consent"), "required")

    def test_upgrade_does_not_overwrite_manager_toggle_off(self):
        icp = self.env["ir.config_parameter"].sudo()
        key = _NS + "require_compliance_check"  # seeded True; manager turns OFF
        icp.set_param(key, "False")
        # Re-load the seed as an upgrade would (noupdate=1 protects edits).
        convert_file(self.env, "era_crm_ai_agents_lead_gen", _SEED, {},
                     mode="update", noupdate=False)
        self.assertEqual(icp.get_param(key), "False",
                         "upgrade must not clobber the manager's OFF choice")

    def test_menus_and_actions_exist(self):
        for xmlid in (
            "action_crm_ai_lead_gen_provider",
            "action_crm_ai_lead_gen_sourced_partners",
            "action_crm_ai_lead_gen_settings",
            "menu_crm_ai_lead_gen_root",
            "menu_crm_ai_lead_gen_sources",
            "menu_crm_ai_lead_gen_sourced",
            "menu_crm_ai_lead_gen_settings",
        ):
            self.assertTrue(self.env.ref("era_crm_ai_agents_lead_gen." + xmlid),
                            "missing %s" % xmlid)
