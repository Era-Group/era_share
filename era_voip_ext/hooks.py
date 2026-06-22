def _ensure_dedicated_partner(env, agent_xmlid):
    agent = env.ref(agent_xmlid, raise_if_not_found=False)
    if not agent:
        return

    partner = agent.partner_id
    needs_new_partner = False
    if not partner:
        needs_new_partner = True
    elif partner == env.ref("base.partner_root", raise_if_not_found=False):
        needs_new_partner = True
    else:
        shared_count = env["ai.agent"].sudo().search_count(
            [("partner_id", "=", partner.id), ("id", "!=", agent.id)]
        )
        needs_new_partner = shared_count > 0

    if not needs_new_partner:
        return

    new_partner = env["res.partner"].sudo().create(
        {
            "name": agent.name,
            "active": False,
        }
    )
    agent.sudo().write({"partner_id": new_partner.id})


def post_init_hook(env):
    # Odoo 17+ passes the Environment directly (was (cr, registry) pre-17).
    _ensure_dedicated_partner(env, "era_voip_ext.voip_call_formatting_agent")
