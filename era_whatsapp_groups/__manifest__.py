# Part of Era Group custom addons.
{
    'name': 'Era WhatsApp Groups (Meta Cloud API)',
    'version': '19.0.1.0.0',
    'category': 'Productivity/WhatsApp',
    'summary': 'WhatsApp group conversations over the official Meta Cloud API',
    'description': """
Era WhatsApp Groups
===================

Extends Odoo's standard WhatsApp module so a group conversation on the OFFICIAL
Meta Cloud API appears in Discuss like any other WhatsApp channel.

* Group registry synced from Meta, with an explicit allowlist - discovery never
  enables a group by itself.
* Outbound sends addressed to a group instead of a single number.
* Inbound group messages routed to their channel, attributed to the participant
  who actually wrote them.
* Coexists with era_waha_integration: WAHA accounts and Cloud API accounts are
  routed independently and never cross.

إدارة مجموعات واتساب عبر واجهة ميتا الرسمية
===========================================

تتيح ظهور محادثات المجموعات داخل Discuss كأي قناة واتساب أخرى، مع قائمة سماح
صريحة: المزامنة تكتشف المجموعات ولا تفعّل أياً منها تلقائياً.
""",
    'author': 'Era Group',
    'email': 'info@era.net.sa',
    'website': 'https://era.net.sa',
    'license': 'LGPL-3',
    # 'whatsapp_identifiers' is a MANDATORY dependency, not a convenience.
    #
    # Odoo loads modules by (depth, name). Without this entry we are depth 2 and
    # sort as 'era_whatsapp_groups' < 'whatsapp_identifiers', so identifiers ends
    # up MORE derived than us -- and it replaces
    # `_find_active_channel_from_whatsapp_message_values` without calling super
    # (whatsapp_identifiers/models/whatsapp_account.py:8). Every inbound override
    # here was silently dead until this line existed; the tests caught it.
    # Declaring the dependency makes us depth 3, hence always outermost.
    #
    # It is auto_install anyway, so it arrives on its own once its dependencies
    # are met -- this only makes the timing deterministic. And it is a NET FIX:
    # identifiers overrides discuss.channel._check_whatsapp_number WITHOUT
    # re-declaring @api.constrains, and Odoo collects constraints via
    # getmembers(hasattr '_constrains') on the most-derived attribute
    # (odoo/orm/models.py:519-545) -- so on stock whatsapp+identifiers the
    # phone-number constraint is silently DEAD. Our decorated override sits above
    # it and brings the constraint back. test_coexistence covers exactly that.
    'depends': ['whatsapp', 'whatsapp_identifiers'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron_data.xml',
        'views/whatsapp_cloud_group_views.xml',
        'views/whatsapp_account_views.xml',
    ],
    'installable': True,
    'application': False,
}
