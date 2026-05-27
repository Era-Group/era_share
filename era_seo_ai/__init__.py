import logging

from . import models

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Point era_seo.ai_agent_id at the shipped SEO agent — only when unset.

    The agent record itself is created by data/ai_agent_data.xml
    (noupdate="1"), so it exists by the time this hook runs. We set the
    ICP default only if the admin hasn't already chosen an agent, so a
    re-install never overrides a deliberate choice.
    """
    ICP = env['ir.config_parameter'].sudo()
    if ICP.get_param('era_seo.ai_agent_id'):
        return
    agent = env.ref('era_seo_ai.agent_seo', raise_if_not_found=False)
    if agent:
        ICP.set_param('era_seo.ai_agent_id', str(agent.id))
        _logger.info(
            'era_seo_ai: defaulted era_seo.ai_agent_id to the SEO agent (#%d).',
            agent.id,
        )
