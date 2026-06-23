# -*- coding: utf-8 -*-
"""Retire the per-module Lead Generation Bot; adopt the suite-wide cron identity.

The execution identity moved from a Lead-Gen-specific ``user_lead_gen_bot`` to
the shared, configurable resolver ``crm.ai.agent._get_cron_run_user()`` (Base),
defaulting to the least-privilege ``CRM AI Automation`` account.

Because ``data/ir_cron.xml`` is ``noupdate="1"``, an UPGRADE does not re-point an
existing cron or orphan-remove the old bot, so do it here for already-installed
databases (a fresh install already ships the new state). Idempotent.
"""
from odoo import SUPERUSER_ID, api

_NEW_CODE = ("model.with_user(env['crm.ai.agent']._get_cron_run_user())"
             ".run_lead_generation(unattended=True)")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    auto = env.ref("era_crm_ai_agents_base.user_crm_ai_automation",
                   raise_if_not_found=False)
    cron = env.ref("era_crm_ai_agents_lead_gen.ir_cron_lead_gen_run",
                   raise_if_not_found=False)

    # 1) Re-point the cron at the shared default identity and switch its code to
    #    the resolver (writing `code` proxies to the linked ir.actions.server).
    if cron and auto:
        vals = {"code": _NEW_CODE}
        if cron.user_id == env.ref("era_crm_ai_agents_lead_gen.user_lead_gen_bot",
                                   raise_if_not_found=False):
            vals["user_id"] = auto.id
        cron.write(vals)

    # 2) Retire the now-redundant per-module bot (it never ran and owns nothing).
    #    Prefer a clean delete; fall back to archive if a FK still pins it.
    bot = env.ref("era_crm_ai_agents_lead_gen.user_lead_gen_bot",
                  raise_if_not_found=False)
    if bot:
        try:
            with cr.savepoint():
                bot.unlink()  # cascades its ir.model.data pointer
        except Exception:
            bot.active = False  # keep the DB consistent if it cannot be removed
