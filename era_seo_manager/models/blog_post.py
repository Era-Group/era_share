import logging

from odoo import models

_logger = logging.getLogger(__name__)


class BlogPost(models.Model):
    """Phase 1: apply era.seo.mixin to blog.post.

    Full blog enhancements (reading time, TOC, series, related posts, FAQs,
    author profiles) land in Phase 5.  The mixin fields are available now so
    that existing posts can be SEO-configured before Phase 5 ships.
    """

    _name = 'blog.post'
    _inherit = ['blog.post', 'era.seo.mixin']

    def _get_seo_path(self):
        """Return the blog post's frontend URL path."""
        return self.website_url or '/'
