from datetime import datetime

from odoo import api, fields, models


class EraReportHelper(models.AbstractModel):
    """Formatting helpers shared by the Era bilingual PDF documents.

    QWeb expressions cannot import python modules and do not accept ``lambda``,
    so the handful of formatting rules the three documents share live here and
    are reached from the templates with ``env['era.report.helper']``.

    Every helper returns a plain string, already laid out the way the approved
    designs show it: latin digits, no thousands separator and no currency
    symbol inside the tables (the currency is stated once in the meta bar and
    once in the grand-total label).
    """
    _name = 'era.report.helper'
    _description = 'Era Document Design Helpers'

    @api.model
    def doc_date(self, value):
        """Render a Date/Datetime value as ``YYYY-MM-DD`` in the user timezone."""
        if not value:
            return '-'
        if isinstance(value, datetime):
            value = fields.Datetime.context_timestamp(self, value).date()
        return value.strftime('%Y-%m-%d')

    @api.model
    def address_line(self, partner):
        """One-line postal address: street, street2, city, state, country."""
        if not partner:
            return '-'
        parts = [partner.street, partner.street2, partner.city,
                 partner.state_id.name, partner.country_id.name]
        return ', '.join(part for part in parts if part) or '-'

    @api.model
    def qty(self, value):
        """Quantity without trailing zeros: 20, 50, 20.5."""
        text = '%.3f' % (value or 0.0)
        return text.rstrip('0').rstrip('.') or '0'

    @api.model
    def money(self, value, currency=None):
        """Amount with the currency's own number of decimals, no symbol."""
        digits = currency.decimal_places if currency else 2
        return '%.*f' % (digits, value or 0.0)
