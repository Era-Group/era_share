"""Neutralisation switches every scheduled job off; a demo server needs them on.

Restoring a dump anywhere but production stamps the database neutralized and
disables every ir.cron row. Reminders then never fire, signature retries never
run and the legislation corpus never refreshes — in front of a client the
system looks dead, and the neutralisation that protects against stray emails
says nothing about whether this project's own jobs should tick.

Core blocks exactly one path: ir.cron.toggle(), which returns early on a
neutralized database so a module cannot re-enable a job as a side effect
(ir_cron.py:724). A direct write is deliberately left open, and that is what
this does — for this project's own crons, named through ir.model.data, and
nothing else in the database.

It runs at every registry load, so it survives the case that has no upgrade at
all: restore a dump, start the server, done. Where an administrator has
switched one of these off on purpose, set the config parameter
`era.autostart_crons` to 0 and this stops touching them.
"""
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Named rather than discovered: whatever else lives in the database is not this
# project's business, and a demo server must not resurrect core jobs that were
# switched off for good reasons.
PROJECT_MODULES = (
    'era_law_firm',
    'era_law_firm_ai',
    'era_law_firm_sign',
    'era_ai_accounts',
)

# Two jobs this project does not own but cannot work without: our code calls
# _trigger() on them every time a knowledge source is added or a document is
# indexed, and a trigger still requires the job to be active before the worker
# will pick it up. Left asleep, adding a source to an agent does nothing at
# all — the demo's AI answers stay empty and nobody can see why. Named one by
# one, never a sweep of the `ai` module.
DEPENDENT_CRONS = (
    'ai.ir_cron_process_sources',
    'ai.ir_cron_generate_embedding',
)


class IrCron(models.Model):
    _inherit = 'ir.cron'

    def _register_hook(self):
        # Every model's hook is called once per registry load (loading.py:594).
        self.sudo()._era_start_project_crons()
        return super()._register_hook()

    @api.model
    def _era_start_project_crons(self, modules=PROJECT_MODULES, dependencies=DEPENDENT_CRONS):
        """Put this project's scheduled jobs back to work. Returns what it woke."""
        params = self.env['ir.config_parameter'].sudo()
        if params.get_param('era.autostart_crons', '1') in ('0', 'False', 'false'):
            return self.browse()

        data = self.env['ir.model.data'].sudo().search([
            ('model', '=', 'ir.cron'),
            ('module', 'in', list(modules)),
        ])
        crons = self.sudo().with_context(active_test=False).browse(data.mapped('res_id')).exists()
        for xmlid in dependencies:
            # ref() with the same active_test=False, or an archived job reads
            # as missing and quietly stays asleep.
            job = self.env.ref(xmlid, raise_if_not_found=False)
            if job:
                crons |= job.sudo().with_context(active_test=False)
        if not crons:
            return self.browse()

        sleeping = crons.filtered(lambda cron: not cron.active)
        if not sleeping:
            return self.browse()

        sleeping.write({'active': True})
        _logger.info(
            'era: started %s project cron(s) disabled on this database: %s',
            len(sleeping), ', '.join(sleeping.mapped('name')))
        return sleeping
