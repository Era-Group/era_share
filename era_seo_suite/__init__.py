import logging

from . import models
from . import controllers
from . import wizards


_logger = logging.getLogger(__name__)


def _ensure_seo_agents(env):
    """Guarantee the shipped Blog/SEO AI agents exist, re-creating any an admin
    deleted, so Blog Gen and Auto-Fix always have a writer.

    The agents (``era_seo_suite.agent_seo`` / ``agent_article``) are declared
    ``noupdate="1"`` in ``data/ai_agent_data.xml`` — Odoo recreates them on a
    module UPGRADE if their xmlid is missing. This helper makes the same
    guarantee on INSTALL (via ``post_init_hook``) and is callable from
    migrations. It never duplicates the prompt text: missing agents are
    re-seeded straight from the single source of truth (the XML); existing
    agents (with their admin-tuned prompt/model) are left untouched.
    """
    wanted = ('agent_seo', 'agent_article')
    if all(env.ref('era_seo_suite.' + x, raise_if_not_found=False)
           for x in wanted):
        return
    from odoo.tools.convert import convert_file
    convert_file(env, 'era_seo_suite', 'data/ai_agent_data.xml', None,
                 mode='init', noupdate=True)
    _logger.info('era_seo_suite: (re)seeded missing Blog/SEO AI agent(s).')


def post_init_hook(env):
    """Add every Settings/Admin user (`base.group_system`) to the suite's
    SEO Manager group.

    Rationale: the wizard, the AI bulk-fill cron, the audit-run cron, and
    every Suggest/Apply button gate on `era_seo_suite.group_era_seo_manager`.
    A fresh install otherwise dead-ends an admin at the first AI action with
    `AccessError: AI auto-fix requires the SEO Manager group.` The admin
    could grant the role to themselves from Settings → Users, but that's a
    five-step UI dance at the worst moment. Granting it here is a security
    no-op (system-group members can self-elevate anyway) and matches what
    Odoo's own apps do via `implied_ids` on their manager groups.

    Idempotent — re-running on upgrade just adds whichever admins joined
    `base.group_system` since the last install.
    """
    # Always ensure the shipped Blog/SEO agents exist — must run regardless of
    # the group-membership early-returns below.
    _ensure_seo_agents(env)

    seo_manager = env.ref(
        'era_seo_suite.group_era_seo_manager', raise_if_not_found=False)
    sys_group = env.ref('base.group_system', raise_if_not_found=False)
    if not seo_manager or not sys_group:
        return
    new_members = sys_group.user_ids - seo_manager.user_ids
    if not new_members:
        return
    seo_manager.sudo().write(
        {'user_ids': [(4, u.id) for u in new_members]})
    _logger.info(
        'post_init: granted SEO Manager to %d admin user(s): %s',
        len(new_members),
        ', '.join(sorted(new_members.mapped('login'))))
    _consolidate_organization(env)


def _consolidate_organization(env):
    """Make the suite the single Organization JSON-LD source on fresh installs.

    Seeds one site-wide ``organization`` schema instance per website and, if the
    third-party ``website_schema_org`` is present, deactivates its injected
    template so the Organization node isn't emitted twice. Idempotent. The
    upgrade path is handled by the 19.0.3.0.77 migration (which also drops any
    ad-hoc page-level Organization instances).
    """
    Inst = env.get('era.seo.schema.instance')
    Tmpl = env.get('era.seo.schema.template')
    if Inst is None or Tmpl is None:
        return
    org_tmpl = Tmpl.sudo().search([('code', '=', 'organization')], limit=1)
    if org_tmpl:
        for site in env['website'].sudo().search([]):
            if not Inst.sudo().search_count([
                    ('res_model', '=', 'website'),
                    ('res_id', '=', site.id),
                    ('template_id', '=', org_tmpl.id)]):
                Inst.sudo().create({
                    'template_id': org_tmpl.id,
                    'res_model': 'website',
                    'res_id': site.id,
                    'active': True,
                })
    # Deactivate ALL website_schema_org layout views by key — including the
    # per-website copy-on-write copies that env.ref() (base record only) would
    # miss. Those COW copies are the ones actually applied on the frontend, so
    # missing them leaves the duplicate Organization emitting.
    env['ir.ui.view'].sudo().search([
        ('key', '=', 'website_schema_org.frontend_layout_schema_org'),
    ]).write({'active': False})
