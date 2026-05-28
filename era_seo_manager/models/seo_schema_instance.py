import json
import logging

from markupsafe import Markup

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
        ondelete='cascade',
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

    # --- Preload API ----------------------------------------------------------

    @api.model
    def _get_for_render(self, main_object=None, website=None):
        """Return the active schema instances to render on the current page.

        Combines two sources in a single SQL query:
          - Site-wide instances bound to ``website`` (res_model='website').
          - Page-specific instances bound to ``main_object`` if it carries
            ``era.seo.mixin`` (detected by the ``seo_title`` field).

        Results are ordered with site-wide schemas first, then page-specific
        schemas; within each group records keep their ``sequence`` order.

        Preferred over ad-hoc searches in QWeb (CLAUDE.md §9: no queries
        inside templates). Called once per page render from the
        ``era_seo_schema_ld`` template.

        :param main_object: The record the page is rendering (may be None on
                            controller routes like ``/``).
        :param website:     The active website recordset.
        :returns:           A recordset of ``era.seo.schema.instance``.
        """
        Instance = self.sudo()
        clauses = []

        if website:
            clauses.append([
                ('res_model', '=', 'website'),
                ('res_id', '=', website.id),
            ])

        if (main_object
                and hasattr(main_object, '_name')
                and 'seo_title' in main_object._fields
                and main_object.id):
            clauses.append([
                ('res_model', '=', main_object._name),
                ('res_id', '=', main_object.id),
            ])

        if not clauses:
            return Instance

        # Build domain: active=True AND (clause_1 OR clause_2 OR ...)
        target = []
        for _i in range(len(clauses) - 1):
            target.append('|')
        for clause in clauses:
            target.append('&')
            target.extend(clause)
        domain = [('active', '=', True)] + target

        instances = Instance.search(domain)

        # Stable order: website-bound first (sort key 0), then page-bound
        # (sort key 1); within each group sequence ascending then id.
        def _sort_key(rec):
            return (0 if rec.res_model == 'website' else 1, rec.sequence, rec.id)

        return instances.sorted(key=_sort_key)

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
        json_str = render_jsonld(
            self.template_id.body,
            ctx,
            record=page_record,
            instance_data=instance_data,
        )
        # Wrap in Markup so QWeb's t-out does not HTML-entity-encode the JSON.
        # JSON inside <script type="application/ld+json"> must be literal, not
        # &quot;-encoded, or JSON parsers and rich-result validators will fail.
        return Markup(json_str)
