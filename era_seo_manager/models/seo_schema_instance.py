import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EraSeoSchemaInstance(models.Model):
    """Minimal stub for Phase 1. Full implementation in Phase 2.

    Exists so that era.seo.mixin.seo_schema_instance_ids One2many resolves
    and the DB table is created with the required (res_model, res_id) columns.
    """

    _name = 'era.seo.schema.instance'
    _description = 'SEO Schema Instance'
    _order = 'sequence, id'

    name = fields.Char(string='Name', compute='_compute_name')
    res_model = fields.Char(string='Record Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            'res_model_res_id_idx',
            'CHECK(res_model IS NOT NULL AND res_id IS NOT NULL)',
            'res_model and res_id are required.',
        ),
    ]

    @api.depends('res_model', 'res_id')
    def _compute_name(self):
        for rec in self:
            rec.name = '{} #{}'.format(rec.res_model or '', rec.res_id or '')
