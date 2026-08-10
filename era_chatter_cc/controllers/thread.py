from odoo import Command
from odoo.http import request
from odoo.addons.mail.controllers.thread import ThreadController


class EraChatterCcThreadController(ThreadController):
    """ Resolve the Cc line sent by the chatter composer.

    No new route is declared: Odoo dispatches '/mail/message/post' to the most
    derived controller class, so overriding '_prepare_message_data' is enough.
    """

    def _prepare_message_data(self, post_data, *, thread, from_create=True, **kwargs):
        res = super()._prepare_message_data(
            post_data, thread=thread, from_create=from_create, **kwargs,
        )
        partner_cc_ids = post_data.get('partner_cc_ids')
        partner_cc_emails = post_data.get('partner_cc_emails')
        if partner_cc_ids is None and partner_cc_emails is None:
            return res

        partners = request.env['res.partner'].browse(map(int, partner_cc_ids or []))
        if partner_cc_emails:
            partners |= thread._partner_find_from_emails_single(
                partner_cc_emails,
                no_create=not request.env.user.has_group('base.group_partner_manager'),
            )
        # same access filtering core applies to the To line
        partners = partners.filtered(
            lambda p: not self.env.user.share and p.has_access('read'),
        )
        res['partner_cc_ids'] = [Command.set(partners.ids)]
        # Cc people are real recipients: they must be notified through the
        # standard machinery, which only looks at 'partner_ids'.
        res['partner_ids'] = list(set(res.get('partner_ids') or []) | set(partners.ids))
        return res
