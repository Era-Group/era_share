import json
from unittest.mock import Mock, patch

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestServiceEnrichment(TransactionCase):

    def test_url_enrichment_populates_customer_fit_and_safe_rules(self):
        service = self.env['cs.service'].create({
            'name': 'Enablement Service',
            'url': 'https://example.com/enablement',
        })
        response = Mock()
        response.text = '<html><body>Training and onboarding improve product adoption.</body></html>'
        response.raise_for_status = Mock()
        data = {
            'description': 'خدمة تمكين وتدريب.',
            'features': '- تدريب\n- تهيئة',
            'product_details': 'جلسات تمكين.',
            'target_audience': 'العملاء الجدد.',
            'decision_points': 'عند انخفاض الاستخدام.',
            'suggested_pitch': 'نساعدكم على رفع الاستخدام.',
            'need_signals': '- انخفاض الاستخدام',
            'discovery_questions': '- ما الذي يمنع الاستخدام؟',
            'value_outcomes': '- رفع التبنّي',
            'not_suitable_when': '- لا يوجد مستخدمون مستهدفون',
            'recommend_on_low_adoption': True,
            'recommend_on_support_pressure': 'false',
            'recommend_on_sla_failure': False,
            'recommendation_rationale': 'الخدمة تعالج التهيئة والتبنّي.',
            'suggested_ticket_tags': '- New Requirement\n- missing tag',
        }
        agent_model = type(self.env.ref('era_customer_success.cs_service_extract_agent'))
        with patch('odoo.addons.era_customer_success.models.cs_service.requests.get', return_value=response), \
                patch.object(agent_model, 'get_direct_response', return_value=[json.dumps(data)]):
            service.action_enrich_from_url()

        self.assertEqual(service.need_signals, data['need_signals'])
        self.assertEqual(service.discovery_questions, data['discovery_questions'])
        self.assertEqual(service.value_outcomes, data['value_outcomes'])
        self.assertEqual(service.not_suitable_when, data['not_suitable_when'])
        self.assertTrue(service.recommend_on_low_adoption)
        self.assertFalse(service.recommend_on_support_pressure)
        self.assertEqual(service.suggested_ticket_tags, data['suggested_ticket_tags'])
        self.assertIn('الخدمة تعالج التهيئة والتبنّي.', service.decision_points)

        existing = self.env['helpdesk.tag'].create({'name': 'New Requirement'})
        service.action_apply_suggested_ticket_tags()
        self.assertEqual(service.recommendation_ticket_tag_ids, existing)
