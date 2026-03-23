import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class IrAttachmentCleanup(models.Model):
    _inherit = 'ir.attachment'

    # Models whose system-generated PDF reports are safe to delete.
    # These reports are re-generated on demand when printed again.
    CLEANUP_MODELS = [
        'account.move',     # Invoices, vendor bills
        'sale.order',       # Quotations, sale orders
        'account.payment',  # Check printing (US/CA)
    ]

    _CHECKPOINT_PARAM = 'filestore_cleanup.last_create_date'

    @api.model
    def _cron_cleanup_report_pdfs(self):
        """Delete system-generated PDF reports for invoices, quotations, and checks.

        Only deletes attachments that:
        - Are linked to account.move, sale.order, or account.payment
        - Are PDF files (mimetype = application/pdf)
        - Are report attachments (res_field is False/NULL)
        - Are older than 1 month
        - Are NOT user-uploaded via mail (no link to mail.message)

        Processes 10000 records per run to avoid performance issues.
        Saves a checkpoint (create_date of the last processed record) so the
        next run continues from where this one left off instead of rescanning
        from the beginning.
        """
        _logger.warning('Filestore Cleanup: Cron started.')

        # Prevent concurrent runs across multiple workers
        self.env.cr.execute("SELECT pg_try_advisory_xact_lock(20260323)")
        if not self.env.cr.fetchone()[0]:
            _logger.warning('Filestore Cleanup: Skipped — another worker is already running.')
            return

        ICP = self.env['ir.config_parameter'].sudo()
        last_checkpoint = ICP.get_param(self._CHECKPOINT_PARAM, False)

        cutoff_date = fields.Datetime.now() - timedelta(days=30)

        domain = [
            ('res_model', 'in', self.CLEANUP_MODELS),
            ('res_field', '=', False),
            ('mimetype', '=', 'application/pdf'),
            ('create_date', '<', cutoff_date),
        ]
        if last_checkpoint:
            domain.append(('create_date', '>', last_checkpoint))

        # Find system-generated report PDFs
        attachments = self.sudo().search(domain, limit=10000, order='create_date asc')

        if not attachments:
            _logger.info('Filestore Cleanup: No report PDFs to clean up.')
            ICP.set_param(self._CHECKPOINT_PARAM, fields.Datetime.to_string(cutoff_date))
            return

        # Save checkpoint before unlink (records won't be readable afterwards)
        new_checkpoint = fields.Datetime.to_string(attachments[-1].create_date)

        # Filter out attachments that are linked to mail messages
        # (user-uploaded files attached via chatter)
        mail_attachment_ids = set()
        if 'mail.message' in self.env:
            self.env.cr.execute("""
                SELECT rel.attachment_id
                FROM message_attachment_rel rel
                JOIN mail_message msg ON msg.id = rel.message_id
                WHERE rel.attachment_id IN %s
                  AND msg.message_type != 'notification'
            """, (tuple(attachments.ids),))
            mail_attachment_ids = {row[0] for row in self.env.cr.fetchall()}

        to_delete = attachments.filtered(
            lambda a: a.id not in mail_attachment_ids
        )

        count = len(to_delete)
        if to_delete:
            to_delete.unlink()

        # If we got fewer records than the limit, the range is exhausted.
        # Set checkpoint to the current cutoff so the next run only picks up
        # records that have newly aged past 30 days, not re-scan from the start.
        if len(attachments) < 10000:
            ICP.set_param(self._CHECKPOINT_PARAM, fields.Datetime.to_string(cutoff_date))
        else:
            ICP.set_param(self._CHECKPOINT_PARAM, new_checkpoint)

        _logger.warning(
            'Filestore Cleanup: Deleted %d system-generated report PDFs '
            '(skipped %d user-uploaded). Checkpoint: %s',
            count, len(attachments) - count, new_checkpoint,
        )
