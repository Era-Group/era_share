# -*- coding: utf-8 -*-
from odoo import models
from odoo.addons.mail.tools.discuss import Store


class MailThread(models.AbstractModel):
    _inherit = 'mail.thread'

    def _thread_to_store(self, store: Store, fields, *, request_list=None):
        """Add WhatsApp capability to thread store data."""
        super()._thread_to_store(store, fields, request_list=request_list)
        can_send = self._name in [
            'res.partner',
            'crm.lead',
            'sale.order',
            'account.move',
            'res.users',
            'purchase.order',
            'project.project',
            'project.task',
            'helpdesk.ticket',
        ]
        for record in self:
            store.add_model_values(
                "mail.thread",
                {"id": record.id, "model": record._name, "canSendWahaWhatsapp": can_send},
            )
