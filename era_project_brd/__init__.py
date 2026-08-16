from . import models


def post_init_hook(env):
    """Configure the BRD agent and publish any existing BRDs to Documents."""
    agent = env.ref('era_project_brd.project_brd_agent', raise_if_not_found=False)
    if agent and 'era_account_id' in agent._fields and not agent.era_account_id:
        raw = env['ir.config_parameter'].sudo().get_param('sembly.ai_agent_id')
        try:
            source = env['ai.agent'].sudo().browse(int(raw or 0)).exists()
        except (TypeError, ValueError):
            source = env['ai.agent']
        if source and source.era_account_id:
            agent.sudo().write({
                'era_account_id': source.era_account_id.id,
                'era_model_id': source.era_model_id.id if source.era_model_id else False,
            })

    projects = env['project.project'].sudo().search([
        ('brd_state', '=', 'done'),
        ('brd_document', '!=', False),
        ('brd_document_id', '=', False),
    ])
    for project in projects:
        project._brd_sync_document()
