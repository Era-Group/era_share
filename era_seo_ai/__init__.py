import logging

from . import models

_logger = logging.getLogger(__name__)

# Optional models that carry era.seo.mixin only when era_seo_blog is
# installed. We bind the AI fill/rewrite actions to them at install time
# if they're present, so era_seo_ai covers the blog suite without taking a
# hard dependency on era_seo_blog.
_OPTIONAL_SEO_MODELS = (
    'blog.post',
    'era.blog.series',
    'era.blog.category',
    'era.blog.author',
)


def post_init_hook(env):
    """Default the SEO agent and bind AI fill actions to optional models."""
    _default_seo_agent(env)
    _bind_optional_models(env)


def _default_seo_agent(env):
    """Point era_seo.ai_agent_id at the shipped SEO agent — only when unset."""
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


def _bind_optional_models(env):
    """Create AI fill/rewrite server actions for blog models, if installed.

    Idempotent: skips any (model, method) binding that already exists, so
    re-running the hook never duplicates the actions.
    """
    IrModel = env['ir.model'].sudo()
    Server = env['ir.actions.server'].sudo()

    specs = [
        ('action_ai_fill_seo', 'AI: Fill Missing SEO'),
        ('action_ai_rewrite_seo', 'AI: Rewrite All SEO'),
    ]

    for model_name in _OPTIONAL_SEO_MODELS:
        if model_name not in env:
            continue
        Model = env[model_name]
        # Only bind models that actually carry the mixin fields.
        if 'seo_title' not in Model._fields:
            continue
        ir_model = IrModel.search([('model', '=', model_name)], limit=1)
        if not ir_model:
            continue
        for method, label in specs:
            code = 'if records:\n    action = records.%s()' % method
            existing = Server.search([
                ('binding_model_id', '=', ir_model.id),
                ('code', '=', code),
            ], limit=1)
            if existing:
                continue
            Server.create({
                'name': label,
                'model_id': ir_model.id,
                'binding_model_id': ir_model.id,
                'binding_view_types': 'list,form',
                'state': 'code',
                'code': code,
            })
            _logger.info(
                'era_seo_ai: bound "%s" to %s.', label, model_name,
            )
