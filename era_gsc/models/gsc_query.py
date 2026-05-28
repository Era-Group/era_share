"""ERA GSC — one row per (site, date, query) of search analytics."""
from odoo import fields, models


class EraGscQuery(models.Model):
    _name = 'era.gsc.query'
    _description = 'GSC Search Query'
    _order = 'date desc, clicks desc'

    site_id = fields.Many2one(
        'era.gsc.site', string='Site',
        required=True, ondelete='cascade', index=True,
    )
    account_id = fields.Many2one(
        related='site_id.account_id', string='Account',
        store=True, readonly=True,
    )
    date = fields.Date(string='Date', required=True, index=True)
    query = fields.Char(string='Query', required=True, index=True)
    clicks = fields.Integer(string='Clicks')
    impressions = fields.Integer(string='Impressions')
    ctr = fields.Float(string='CTR', digits=(8, 4))
    position = fields.Float(string='Position', digits=(8, 2))

    _row_unique = models.Constraint(
        'UNIQUE(site_id, date, query)',
        'Duplicate GSC row for the same site/date/query.',
    )
