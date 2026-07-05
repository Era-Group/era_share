from odoo import _, models
from odoo.exceptions import UserError


def _is_voip_manager(env):
    """True for the system/superuser (telephony + analysis crons) and VoIP managers."""
    return env.su or env.user.has_group("voip.group_voip_admin")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def unlink(self):
        """Protect call recordings/attachments: only VoIP managers may delete
        attachments that belong to a voip.call record."""
        if not _is_voip_manager(self.env):
            protected = self.filtered(lambda a: a.res_model == "voip.call")
            if protected:
                raise UserError(_("Only VoIP managers can delete call recordings or attachments."))
        return super().unlink()


class MailMessage(models.Model):
    _inherit = "mail.message"

    def unlink(self):
        """Protect call chatter: only VoIP managers may delete messages logged
        on a voip.call record."""
        if not _is_voip_manager(self.env):
            protected = self.filtered(lambda m: m.model == "voip.call")
            if protected:
                raise UserError(_("Only VoIP managers can delete call chatter."))
        return super().unlink()

    def write(self, vals):
        """Protect call chatter from edits: block body changes on voip.call
        messages for non-managers. Other writes (notification state, tracking,
        stars…) are left untouched so the mail engine keeps working."""
        if "body" in vals and not _is_voip_manager(self.env):
            protected = self.filtered(lambda m: m.model == "voip.call")
            if protected:
                raise UserError(_("Only VoIP managers can edit call chatter."))
        return super().write(vals)
