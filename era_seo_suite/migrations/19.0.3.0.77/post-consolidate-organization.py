"""Consolidate Organization JSON-LD onto era_seo_suite, site-wide.

Before this version the site could emit the Organization node twice: the
third-party ``website_schema_org`` injected one (no ``@id``) on every page,
and the suite emitted its own (with ``@id``) on whichever pages had an ad-hoc
``organization`` schema instance. That left some pages duplicated and the home
page with an ``@id``-less node (so a Service block's ``provider.@id`` dangled).

This migration makes the suite the single Organization source:
  1. ensure ONE site-wide ``organization`` instance per website
     (res_model='website') so every page emits the enriched, @id'd node;
  2. drop the redundant page-level ``organization`` instances;
  3. deactivate ``website_schema_org``'s injected template so it stops
     emitting its duplicate (the module stays installed; reversible).
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'era.seo.schema.instance' not in env or 'era.seo.schema.template' not in env:
        return
    Inst = env['era.seo.schema.instance'].sudo()
    org_tmpl = env['era.seo.schema.template'].sudo().search(
        [('code', '=', 'organization')], limit=1)
    if not org_tmpl:
        return

    # 1) one site-wide Organization per website
    created = 0
    for site in env['website'].sudo().search([]):
        if not Inst.search_count([
                ('res_model', '=', 'website'),
                ('res_id', '=', site.id),
                ('template_id', '=', org_tmpl.id)]):
            Inst.create({
                'template_id': org_tmpl.id,
                'res_model': 'website',
                'res_id': site.id,
                'active': True,
            })
            created += 1

    # 2) drop redundant page-level Organization instances (site-wide covers them)
    page_level = Inst.search([
        ('res_model', '=', 'website.page'),
        ('template_id', '=', org_tmpl.id)])
    dropped = len(page_level)
    page_level.unlink()

    # 3) deactivate website_schema_org's duplicate Organization emission
    deactivated = False
    tmpl = env.ref('website_schema_org.frontend_layout_schema_org',
                   raise_if_not_found=False)
    if tmpl and tmpl.active:
        tmpl.active = False
        deactivated = True

    _logger.info(
        'consolidate-organization: +%d site-wide instance(s), -%d page-level, '
        'website_schema_org deactivated=%s', created, dropped, deactivated)
