"""Blog Gen "Article content account" (era.ai.account) → "Article writer" (ai.agent).

The Blog Gen picker now selects an ``ai.agent`` instead of an ``era.ai.account``.
This migration keeps existing installs working without manual reconfiguration:

  1. Re-seed the shipped Blog/SEO agents if an admin deleted them (belt-and-
     suspenders on top of Odoo's own noupdate recreation).
  2. Move the retired content account (``era_seo.content_account_id``) onto the
     effective article agent's ``era_account_id`` so article generation keeps
     writing through the SAME provider (e.g. the Claude CLI account) — the
     era_ai_accounts ai.agent patch routes generation through that account.
  3. Point the new picker (``era_seo.article_agent_id``) at that agent and retire
     the old ICP key.
"""
import logging

from odoo import api, SUPERUSER_ID
from odoo.addons.era_seo_suite import _ensure_seo_agents

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    ICP = env['ir.config_parameter'].sudo()

    # 1. Make sure the shipped agents exist (recreate if deleted).
    _ensure_seo_agents(env)

    # Resolve the effective article writer: the existing override, else the
    # shipped agent_article (mirrors ai_client._resolve_article_agent / hub._blog_agent).
    Agent = env['ai.agent'].sudo()
    agent = Agent.browse()
    aid = (ICP.get_param('era_seo.article_agent_id') or '').strip()
    if aid.isdigit():
        cand = Agent.browse(int(aid))
        if cand.exists():
            agent = cand
    if not agent:
        ref = env.ref('era_seo_suite.agent_article', raise_if_not_found=False)
        if ref and ref.exists():
            agent = ref.sudo()

    # 2. Carry the old content account onto the agent so Claude (or whatever the
    #    account was) keeps doing the writing — only if the agent isn't already
    #    bound to one.
    acc_raw = (ICP.get_param('era_seo.content_account_id') or '').strip()
    Acc = env.get('era.ai.account')
    if agent and acc_raw.isdigit() and Acc is not None:
        acc = Acc.sudo().browse(int(acc_raw))
        if acc.exists() and not agent.era_account_id:
            vals = {'era_account_id': acc.id}
            try:
                model_rec = acc._default_chat_model_record()
            except Exception:  # noqa: BLE001 — never block the upgrade on this
                model_rec = None
            if model_rec:
                vals['era_model_id'] = model_rec.id
            agent.write(vals)
            _logger.info(
                'era_seo_suite: bound article agent #%s to AI account #%s (%s) '
                'so Blog Gen keeps its previous writer.',
                agent.id, acc.id, acc.name)

    # 3. Point the new picker at the agent, and retire the old content-account key.
    if agent:
        ICP.set_param('era_seo.article_agent_id', str(agent.id))
    ICP.set_param('era_seo.content_account_id', '')
