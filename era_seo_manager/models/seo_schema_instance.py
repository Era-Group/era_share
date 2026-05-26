import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class EraSeoSchemaInstance(models.Model):
    """Concrete attachment of an `era.seo.schema.template` to a specific record.

    Instances are polymorphic: (res_model, res_id) points at any model that
    participates in the ERA SEO engine (typically website.page or blog.post).

    The rendered JSON-LD is computed live — never stored — so editing the
    template or the host record is immediately reflected on next page load.

    Per SPEC §8.1.2.

    ONDELETE PATTERN
    ----------------
    Because the polymorphic FK has no automatic CASCADE in the DB, every host
    model must implement an ``unlink`` override that deletes matching instances
    before the host record is removed.  See ``website_page.py`` for the
    canonical example.  When adding a new host model in a later phase, copy
    that pattern and add a docstring pointing here.
    """

    _name = 'era.seo.schema.instance'
    _description = 'SEO Schema Instance'
    _order = 'sequence, id'

    name = fields.Char(string='Name', compute='_compute_name', store=True)
    template_id = fields.Many2one(
        'era.seo.schema.template',
        string='Schema Template',
        required=True,
        ondelete='restrict',
    )
    res_model = fields.Char(string='Record Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(default=True)
    data_json = fields.Text(
        string='Context Overrides (JSON)',
        help='Optional JSON object whose keys override the global rendering '
             'context for this instance only.',
        default='{}',
    )
    rendered_json = fields.Text(
        string='Rendered JSON-LD',
        compute='_compute_rendered_json',
        store=False,
    )

    # --- SQL constraints & indexes --------------------------------------------

    def init(self):
        """Create composite index on (res_model, res_id, active)."""
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS era_seo_schema_instance_model_id_active_idx
            ON era_seo_schema_instance (res_model, res_id, active)
        """)

    _template_not_null = models.Constraint(
        'CHECK(template_id IS NOT NULL)',
        'A schema instance must reference a template.',
    )

    # --- Computes -------------------------------------------------------------

    @api.depends('template_id', 'res_model', 'res_id')
    def _compute_name(self):
        for rec in self:
            tpl_name = rec.template_id.name or ''
            rec.name = '{} on {}#{}'.format(tpl_name, rec.res_model or '', rec.res_id or 0)

    @api.depends('template_id', 'res_model', 'res_id', 'data_json')
    def _compute_rendered_json(self):
        for rec in self:
            if not rec.template_id:
                rec.rendered_json = ''
                continue
            try:
                rec.rendered_json = rec.get_rendered_json_ld()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'era.seo.schema.instance[%d]: render failed: %s', rec.id, exc
                )
                rec.rendered_json = ''

    # --- Constraints ----------------------------------------------------------

    @api.constrains('data_json')
    def _check_data_json(self):
        for rec in self:
            if not rec.data_json or rec.data_json.strip() in ('', '{}'):
                continue
            try:
                parsed = json.loads(rec.data_json)
                if not isinstance(parsed, dict):
                    raise ValueError('Must be a JSON object.')
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValidationError(
                    _('Context overrides must be a valid JSON object: %s') % str(exc)
                ) from exc

    # --- Public rendering API -------------------------------------------------

    def get_rendered_json_ld(self, page_record=None):
        """Return the final JSON-LD string to embed in <head>.

        :param page_record: Optional override for the ``record`` context key.
                            Defaults to the host record referenced by
                            (res_model, res_id).
        :returns: A JSON string (valid JSON even when placeholders fail).
        :raises ValueError: Only on template body syntax errors that are
                            unrecoverable.
        """
        self.ensure_one()
        from .seo_schema_engine import build_context, render_jsonld  # local import avoids circular

        if page_record is None and self.res_model and self.res_id:
            try:
                page_record = self.env[self.res_model].sudo().browse(self.res_id)
                # Verify the record actually exists.
                if not page_record.exists():
                    page_record = None
            except KeyError:
                page_record = None

        # Build instance-level context overrides.
        instance_data: dict = {}
        if self.data_json and self.data_json.strip() not in ('', '{}'):
            try:
                instance_data = json.loads(self.data_json)
            except (json.JSONDecodeError, ValueError):
                _logger.warning(
                    'era.seo.schema.instance[%d]: invalid data_json, ignoring', self.id
                )

        ctx = build_context(self.env, record=page_record)
        return render_jsonld(
            self.template_id.body,
            ctx,
            record=page_record,
            instance_data=instance_data,
        )
