import logging

from odoo import models

_logger = logging.getLogger(__name__)

try:
    from num2words import num2words
except ImportError:
    num2words = None
    _logger.warning("num2words is not available: the Arabic 'amount in words' "
                    "line of the Era payment voucher will fall back to English.")


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def _era_amount_in_words(self):
        """Return ``{'ar': ..., 'en': ...}`` for the payment amount.

        Saudi receipt and payment vouchers are expected to spell the amount
        out. Odoo's own ``currency.amount_to_text`` only produces English, so
        the Arabic line goes through ``num2words`` directly -- which QWeb
        cannot import itself. Any currency ``num2words`` does not know falls
        back to the English wording rather than breaking the document.
        """
        self.ensure_one()
        english = self.currency_id.amount_to_text(self.amount)
        arabic = english
        if num2words:
            try:
                arabic = num2words(self.amount, lang='ar', to='currency',
                                   currency=self.currency_id.name)
            except (NotImplementedError, TypeError, ValueError):
                _logger.info("num2words has no Arabic currency wording for %s.",
                             self.currency_id.name)
        return {'ar': arabic, 'en': english}
