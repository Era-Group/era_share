from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    churn_field = env['ir.model.fields'].search([
        ('model', '=', 'cs.account'),
        ('name', '=', 'churn_probability'),
    ], limit=1)
    tracked_messages = env['mail.tracking.value'].search([
        ('field_id', '=', churn_field.id),
    ]).mapped('mail_message_id') if churn_field else env['mail.message']

    overdue_messages = env['mail.message'].search([
        ('model', '=', 'cs.account'),
        '|',
        ('body', 'ilike', 'Follow-up overdue since'),
        ('body', 'ilike', 'المتابعة متأخرة منذ'),
    ])
    (tracked_messages | overdue_messages).unlink()
