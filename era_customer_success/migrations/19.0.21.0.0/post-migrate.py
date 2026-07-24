from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Insight = env['cs.voc.insight']
    for review in env['cs.value.review'].search([('state', '=', 'closed')]):
        Insight._capture_value_review(review)
    for assessment in env['cs.adoption.assessment'].search([('state', '=', 'confirmed')]):
        Insight._capture_adoption_assessment(assessment)
