"""ERA SEO Blog — landing page controllers.

Routes:
  /blog/series/<slug>     — list of posts in a series, ordered by series sequence
  /blog/category/<slug>   — list of posts in a category
  /blog/author/<slug>     — list of posts authored by a profile

All three render the shared ``era_seo_suite.blog_landing`` template.
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class EraBlogLandingController(http.Controller):

    @http.route('/blog/series/<string:slug>', type='http', auth='public', website=True)
    def blog_series(self, slug, **kw):
        series = request.env['era.blog.series'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not series:
            return request.not_found()
        posts = series.post_ids.filtered(lambda p: p.is_published).sorted(
            key=lambda p: (p.era_series_sequence, p.id)
        )
        return request.render('era_seo_suite.blog_landing', {
            'main_object': series,
            'page_title': series.name,
            'page_kind': 'series',
            'description': series.description,
            'cover_image_url': series.cover_image_url,
            'posts': posts,
        })

    @http.route('/blog/category/<string:slug>', type='http', auth='public', website=True)
    def blog_category(self, slug, **kw):
        cat = request.env['era.blog.category'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not cat:
            return request.not_found()
        posts = cat.post_ids.filtered(lambda p: p.is_published).sorted(
            key=lambda p: (p.published_date or False, p.id), reverse=True,
        )
        return request.render('era_seo_suite.blog_landing', {
            'main_object': cat,
            'page_title': cat.name,
            'page_kind': 'category',
            'description': cat.description,
            'cover_image_url': False,
            'posts': posts,
        })

    @http.route('/blog/author/<string:slug>', type='http', auth='public', website=True)
    def blog_author(self, slug, **kw):
        author = request.env['era.blog.author'].sudo().search(
            [('slug', '=', slug), ('active', '=', True)], limit=1,
        )
        if not author:
            return request.not_found()
        posts = author.post_ids.filtered(lambda p: p.is_published).sorted(
            key=lambda p: (p.published_date or False, p.id), reverse=True,
        )
        return request.render('era_seo_suite.blog_landing', {
            'main_object': author,
            'page_title': author.name,
            'page_kind': 'author',
            'description': author.bio,
            'cover_image_url': author.avatar_url,
            'subtitle': author.role,
            'posts': posts,
        })
