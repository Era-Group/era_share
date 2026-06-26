from odoo import api, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    @api.model
    def get_whatsapp_channel_for_record(self, model, res_id):
        """Return the ID of the most-recent WhatsApp discuss.channel whose
        whatsapp_partner_id matches the partner of the given record, or False."""
        if not res_id or not model:
            return False
        RecordModel = self.env.get(model)
        if RecordModel is None:
            return False
        if model == "res.partner":
            partner_id = res_id
        elif "partner_id" in RecordModel._fields:
            record = RecordModel.browse(res_id)
            partner_id = record.partner_id.id if record.partner_id else False
        else:
            return False
        if not partner_id:
            return False
        channel = self.search(
            [
                ("channel_type", "=", "whatsapp"),
                ("whatsapp_partner_id", "=", partner_id),
            ],
            order="id desc",
            limit=1,
        )
        return channel.id if channel else False
