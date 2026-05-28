import logging

from . import models
from . import controllers
from . import wizards


_logger = logging.getLogger(__name__)


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
