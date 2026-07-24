from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    agent = env.ref('era_customer_success.cs_service_extract_agent', raise_if_not_found=False)
    if not agent:
        return
    prompt = agent.system_prompt or ''
    if '"suggested_ticket_tags"' not in prompt:
        prompt = prompt.replace(
            '\n}\n\nRules:',
            ',\n  "suggested_ticket_tags": "Arabic or English bullet list of likely Helpdesk tag concepts, only if clearly supported"\n}\n\nRules:',
            1,
        )
    prompt = prompt.replace(
        '- Do not return ticket tags. Managers map catalog services to their own Helpdesk tags manually because tag names differ between databases.',
        '- Suggested ticket tags are concepts for manager review only. Never assume that a suggested tag already exists in the database.',
    )
    prompt = prompt.replace(
        '- Never return ticket tags: each database uses its own tags and managers map them manually.',
        '- Suggested ticket tags are concepts for manager review only. Never assume that a suggested tag already exists in the database.',
    )
    agent.write({'system_prompt': prompt})
