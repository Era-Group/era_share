"""Preview wizard for era.seo.schema.template.

Lets an SEO admin choose a website.page and render the selected template
against that page to inspect the resulting JSON-LD before attaching it.

Per SPEC §8 Step 7 (Preview button on template form).
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SeoSchemaPreviewWizard(models.TransientModel):
    """Transient wizard: pick a page, preview rendered JSON-LD output."""

    _name = 'era.seo.schema.preview.wizard'
    _description = 'SEO Schema Preview'

    template_id = fields.Many2one(
        'era.seo.schema.template',
        string='Schema Template',
        required=True,
        readonly=True,
    )
    page_id = fields.Many2one(
        'website.page',
        string='Website Page',
        required=True,
        help='The page to render this template against.',
    )
    rendered_output = fields.Text(
        string='Rendered JSON-LD',
        readonly=True,
    )

    # --- Default values -------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        template_id = self.env.context.get('default_template_id')
        if template_id:
            defaults['template_id'] = template_id
        return defaults

    # --- Actions --------------------------------------------------------------

    def action_preview(self):
        """Render the template against the selected page and show the output."""
        self.ensure_one()
        if not self.template_id:
            raise UserError(_('No template selected.'))
        if not self.page_id:
            raise UserError(_('Select a website page to preview against.'))

        from odoo.addons.era_seo_manager.models.seo_schema_engine import (
            build_context,
            render_jsonld,
        )

        try:
            ctx = build_context(self.env, record=self.page_id)
            output = render_jsonld(self.template_id.body, ctx, record=self.page_id)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'SeoSchemaPreviewWizard: render error for template %d on page %d: %s',
                self.template_id.id, self.page_id.id, exc,
            )
            output = _('Render error: %s') % str(exc)

        self.rendered_output = output

        # Re-open the wizard with the result still visible.
        return {
            'type': 'ir.actions.act_window',
            'name': _('Schema Preview'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
