"""A demo server has to look alive: the project's scheduled jobs must run.

Restoring a dump onto a demo or staging server disables every scheduled job in
the database. Nothing then reminds a lawyer of a hearing, no signature is
retried, no knowledge source is ever embedded — and in front of a client that
reads as a broken product rather than as a safety measure.

These tests pin the two halves of the promise: our jobs come back, and nothing
else in the database is touched.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.era_law_firm.models.legal_cron_autostart import (
    DEPENDENT_CRONS, PROJECT_MODULES)


@tagged('post_install', '-at_install')
class TestCronAutostart(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Cron = cls.env['ir.cron'].sudo().with_context(active_test=False)
        data = cls.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.cron'), ('module', 'in', list(PROJECT_MODULES))])
        cls.project_crons = cls.Cron.browse(data.mapped('res_id')).exists()

    def test_our_jobs_come_back(self):
        self.assertTrue(self.project_crons, 'precondition: the project ships crons')
        self.project_crons.write({'active': False})
        woken = self.env['ir.cron']._era_start_project_crons()
        self.assertEqual(woken & self.project_crons, self.project_crons)
        self.assertTrue(all(self.project_crons.mapped('active')))

    def test_the_embedding_engine_comes_back_too(self):
        """Our code triggers these two; a trigger on an inactive job does
        nothing, so an unembedded corpus would look like a broken feature."""
        for xmlid in DEPENDENT_CRONS:
            job = self.env.ref(xmlid, raise_if_not_found=False)
            if not job:
                continue
            job.sudo().write({'active': False})
            self.env['ir.cron']._era_start_project_crons()
            self.assertTrue(job.sudo().with_context(active_test=False).active, xmlid)

    def test_nothing_else_in_the_database_is_touched(self):
        """A demo server must not resurrect core jobs somebody switched off."""
        stranger = self.env.ref('base.ir_cron_res_users_deletion', raise_if_not_found=False)
        if not stranger:
            self.skipTest('core cron not present')
        stranger.sudo().write({'active': False})
        self.env['ir.cron']._era_start_project_crons()
        self.assertFalse(stranger.sudo().with_context(active_test=False).active)

    def test_an_administrator_can_say_no(self):
        """Where one of these was switched off on purpose, the switch holds."""
        self.env['ir.config_parameter'].sudo().set_param('era.autostart_crons', '0')
        self.project_crons.write({'active': False})
        self.assertFalse(self.env['ir.cron']._era_start_project_crons())
        self.assertFalse(any(self.project_crons.mapped('active')))

    def test_waking_what_is_already_awake_changes_nothing(self):
        self.project_crons.write({'active': True})
        self.assertFalse(self.env['ir.cron']._era_start_project_crons()
                         & self.project_crons)
