# -*- coding: utf-8 -*-
"""De-duplication + tagging/stamping (16.5)."""
from odoo.tests import tagged

from .common import LeadGenCase, COMPANY_JSON


@tagged("post_install", "-at_install")
class TestDedup(LeadGenCase):

    def test_existing_partner_not_duplicated(self):
        """A row matching an existing partner (by email) creates NO duplicate."""
        self.Partner.create({
            "name": "Acme Corp", "is_company": True, "email": "info@acme.example"})
        before = self.Partner.search_count([("email", "=", "info@acme.example")])
        self._provider("Search A", 10, "ERA_LEADGEN_SERPAPI_A")
        result, _ = self._run("<<raw>>", llm_json=COMPANY_JSON)
        after = self.Partner.search_count([("email", "=", "info@acme.example")])
        self.assertEqual(after, before, "must not duplicate an existing match")
        self.assertEqual(result["companies_created"], 0)
        self.assertEqual(result["companies_matched"], 1)

    def test_new_record_created_tagged_and_stamped(self):
        """A non-matching row creates a partner with the tag + source stamp."""
        self._provider("SerpAPI Demo", 10, "ERA_LEADGEN_SERPAPI_A")
        result, _ = self._run("<<raw>>", llm_json=COMPANY_JSON)
        self.assertEqual(result["companies_created"], 1)
        partner = self.Partner.search([("name", "=", "Acme Corp")], limit=1)
        self.assertTrue(partner)
        self.assertEqual(partner.x_lead_gen_source, "SerpAPI Demo",
                         "source provider must be stamped")
        tag = self.env.ref(
            "era_crm_ai_agents_lead_gen.tag_by_lead_generator_agent")
        self.assertIn(tag, partner.category_id, "created record must carry the tag")
