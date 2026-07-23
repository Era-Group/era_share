from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    default_company = env.company

    accounts = env['cs.account'].with_context(active_test=False).search([
        ('company_id', '=', False),
    ])
    if accounts:
        accounts.write({'company_id': default_company.id})

    snapshots = env['csm.kpi.snapshot'].search([])
    for snapshot in snapshots.filtered('cs_account_id'):
        if snapshot.company_id != snapshot.cs_account_id.company_id:
            snapshot.company_id = snapshot.cs_account_id.company_id

    offerings = env['csm.offering'].search([('company_id', '=', False)])
    for offering in offerings:
        offering.company_id = offering.cs_account_id.company_id or default_company
