import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

# Private-use codepoint U+E900: the glyph shipped in static/src/fonts/sar-symbol.*
# and rendered by static/src/css/style.css. Do not swap it for U+FDFC (the legacy
# Rial sign) -- the bundled font does not map that codepoint.
SAR_SYMBOL = "\ue900"


class ResCurrency(models.Model):
    _inherit = "res.currency"

    @api.model
    def _am_apply_sar_symbol(self):
        """Set the Saudi Riyal symbol to the new SAR glyph.

        Called from data/res_currency_data.xml, so it runs on install *and* on
        every upgrade of this module. A plain <record> cannot be used here:
        ``base.SAR`` is declared by ``base`` under ``noupdate="1"``, so the ORM
        skips writes to it while updating a module. ``@api.model`` is required:
        <function> passes the first argument as record ids for non-model methods.
        """
        currencies = self.with_context(active_test=False).search([("name", "=", "SAR")])
        sar = self.env.ref("base.SAR", raise_if_not_found=False)
        if sar:
            currencies |= sar
        todo = currencies.filtered(lambda c: c.symbol != SAR_SYMBOL)
        if todo:
            todo.write({"symbol": SAR_SYMBOL})
            _logger.info("am_sar_symbol: set SAR symbol on %s currency record(s)", len(todo))
        return True
