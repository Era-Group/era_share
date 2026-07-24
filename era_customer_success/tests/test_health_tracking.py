from odoo.tests.common import TransactionCase


class TestHealthTracking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.account = cls.env['cs.account'].sudo().with_context(cs_skip_recompute=True).create({
            'partner_id': cls.env['res.partner'].create({
                'name': 'Health Tracking Customer',
                'is_company': True,
            }).id,
            'company_id': cls.env.company.id,
            'csm_user_id': False,
            'health_score': 70,
            'health_status': 'watch',
        })

    def _track_health_from(self, score, status='watch', churn_risk=False):
        tracked_fields = self.account.fields_get([
            'health_score', 'health_status', 'churn_risk',
        ])
        return self.account._mail_track(tracked_fields, {
            'health_score': score,
            'health_status': status,
            'churn_risk': churn_risk,
        })

    def test_small_health_change_is_not_tracked(self):
        changes, tracking_values = self._track_health_from(67)

        self.assertFalse(changes)
        self.assertFalse(tracking_values)

    def test_large_health_change_is_tracked(self):
        changes, tracking_values = self._track_health_from(
            40, status='critical', churn_risk=True,
        )

        self.assertEqual(changes, {'health_score', 'health_status', 'churn_risk'})
        self.assertEqual(len(tracking_values), 3)
