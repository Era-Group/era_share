from odoo import SUPERUSER_ID, api


SERVICE_EXTRACTOR_PROMPT = """You receive the URL and plain text of a web page describing a product or service offered by ERA.
Extract only facts supported by the page text that help a Customer Success engineer explain, qualify, and recommend the service.

Return STRICT JSON only, with exactly these keys:
{
  "description": "Arabic concise description",
  "features": "Arabic bullet list, each item starts with - ",
  "product_details": "Arabic concrete details, integrations, and requirements",
  "target_audience": "Arabic one-line audience",
  "decision_points": "Arabic decision notes",
  "suggested_pitch": "Arabic ready-to-review customer message",
  "need_signals": "Arabic observable need signals as bullets",
  "discovery_questions": "3-5 Arabic need-validation questions",
  "value_outcomes": "Arabic expected customer outcomes as bullets",
  "not_suitable_when": "Arabic cases where the service should not be presented as bullets",
  "recommend_on_low_adoption": true|false,
  "recommend_on_support_pressure": true|false,
  "recommend_on_sla_failure": true|false,
  "recommendation_rationale": "Arabic short explanation of enabled recommendation rules"
}

Rules:
- Use only supplied page facts. Do not invent prices, integrations, commitments, or outcomes.
- Enable low adoption only for explicit onboarding, training, enablement, or usage-improvement services.
- Enable support pressure only for explicit support operations, capacity, or support-continuity services.
- Enable failed SLA only for explicit support recovery, escalation, monitoring, or response-performance services.
- If evidence is not explicit, return false for the rule.
- Never return ticket tags: each database uses its own tags and managers map them manually.
- Write every narrative value in Arabic. Return JSON only."""


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    agent = env.ref('era_customer_success.cs_service_extract_agent', raise_if_not_found=False)
    if agent:
        agent.write({'system_prompt': SERVICE_EXTRACTOR_PROMPT})
