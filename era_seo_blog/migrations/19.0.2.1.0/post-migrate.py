"""Post-migration for 19.0.2.1.0 — refresh word count / reading time.

`era_word_count` is a *stored* compute over the translatable `content`. On
posts whose English source content stayed the "Start writing here..." stub
while the real article was written in another language, the stored value was
the stub's 3 words. 19.0.2.1.0 changes the compute to count the richest
language variant; recompute existing posts so the stored values catch up
(changing compute code does not auto-recompute stored fields).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    from odoo import api, SUPERUSER_ID

    env = api.Environment(cr, SUPERUSER_ID, {})
    posts = env['blog.post'].search([])
    if not posts:
        _logger.info('era_seo_blog 19.0.2.1.0: no blog posts; nothing to recompute.')
        return
    _logger.info(
        'era_seo_blog 19.0.2.1.0: recomputing reading stats on %d post(s).',
        len(posts),
    )
    posts.modified(['content'])
    env.flush_all()
