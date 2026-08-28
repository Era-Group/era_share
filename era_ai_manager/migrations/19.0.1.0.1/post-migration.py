"""Re-point the discovery agent at its own prompt.

The agent records live in a noupdate block — deliberately, so an upgrade never
overwrites prompts an owner has tuned. That also means a shipped mistake stays
shipped, and this one mattered: discovery had been given the routine
"work only on what has changed since your last run" prompt together with two
runs of carried context. It duly recalled its own earlier "nothing has changed,
stopping" and stopped again, every run, for ever.

Only the two fields that caused it are touched, and only when they still hold
the shipped values, so a tuned prompt is left alone.
"""

ROUTINE_PROMPT_START = "Follow your playbook for this run."


def migrate(cr, version):
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})
    agent = env.ref("era_ai_manager.agent_discovery", raise_if_not_found=False)
    if not agent:
        return
    values = {}
    if (agent.prompt or "").strip().startswith(ROUTINE_PROMPT_START):
        values["prompt"] = (
            "Study this business and write the brief. Read era.ai.profile: if "
            "business_summary, persona_brief and proposed_watchlists are empty, "
            "or the survey has been re-run since you last wrote them, do the "
            "full study now and write your proposal onto the profile record. "
            "Ignore any earlier conclusion of yours about permissions or about "
            "nothing having changed — check the current state yourself with "
            "model_introspect and orm_read before deciding anything. Only if a "
            "complete proposal is already on the record and the survey has not "
            "changed since should you say so in one line and stop."
        )
    if agent.context_mode == "runs":
        # A study starts from the evidence, not from what it concluded last time.
        values["context_mode"] = "none"
    if values:
        agent.write(values)
