"""Carry the Arabic naming into installations that already exist.

The persona, the manager's user and the six agents live in a noupdate block so
that an upgrade never overwrites what an owner has tuned. The playbooks moved
to Arabic and these records did not follow, leaving an Arabic business with
"AI Manager — Business Discovery" in its scheduled actions.

Each record is renamed only while it still holds the exact value that was
shipped, so anything already edited is left alone. The persona's instructions
are replaced only while they are still the untouched English placeholder — once
discovery has written a real brief, or the owner has edited it, this does
nothing.
"""

AGENT_LABELS = {
    "agent_discovery": "دراسة البزنس",
    "agent_inbox": "الوارد والردود",
    "agent_followup": "متابعة العملاء",
    "agent_campaign": "الحملات",
    "agent_watchdog": "المراقبة",
    "agent_weekly": "التقرير الأسبوعي",
}

ROUTINE_PROMPT_AR = (
    "اتبع دليلك في هذا التشغيل. اعمل فقط على ما تغيّر منذ تشغيلك الأخير. "
    "وإن لم يكن هناك ما تفعله فقل ذلك في سطر واحد وتوقّف."
)

DISCOVERY_PROMPT_AR = (
    "ادرس هذا البزنس واكتب التوصيف. اقرأ era.ai.profile: فإن كانت "
    "business_summary و persona_brief و proposed_watchlists فارغة، أو أُعيد "
    "تشغيل المسح منذ آخر مرة كتبتها، فنفّذ الدراسة الكاملة الآن واكتب اقتراحك "
    "على سجل الملف. وتجاهل أي استنتاج سابق لك عن الصلاحيات أو عن أن شيئاً لم "
    "يتغيّر — تحقّق من الحالة الراهنة بنفسك بـ model_introspect و orm_read قبل "
    "أن تقرر أي شيء. ولا تقل إنه لا جديد وتتوقف إلا إن كان اقتراح كامل موجوداً "
    "على السجل فعلاً ولم يتغيّر المسح منذ ذلك الحين."
)


def migrate(cr, version):
    from odoo import SUPERUSER_ID, api

    env = api.Environment(cr, SUPERUSER_ID, {})

    user = env.ref("era_ai_manager.user_ai_manager", raise_if_not_found=False)
    if user and user.name == "AI Business Manager":
        user.name = "المدير الذكي للأعمال"

    persona = env.ref("era_ai_manager.persona_manager", raise_if_not_found=False)
    if persona:
        values = {}
        if persona.name == "AI Business Manager":
            values["name"] = "المدير الذكي للأعمال"
        # Only while it is still the shipped English placeholder: once the
        # discovery agent has written a real brief, this must not touch it.
        if "THIS BRIEF IS A PLACEHOLDER" in (persona.instructions or ""):
            data = env.ref("era_ai_manager.persona_manager")
            shipped = _shipped_persona(env)
            if shipped:
                values["instructions"] = shipped
        if values:
            persona.write(values)

    for xmlid, label in AGENT_LABELS.items():
        agent = env.ref("era_ai_manager.%s" % xmlid, raise_if_not_found=False)
        if not agent:
            continue
        values = {}
        if (agent.name or "").startswith("AI Manager — "):
            values["name"] = "المدير الذكي — %s" % label
        prompt = (agent.prompt or "").strip()
        if prompt.startswith("Follow your playbook"):
            values["prompt"] = ROUTINE_PROMPT_AR
        elif prompt.startswith("Study this business and write the brief"):
            values["prompt"] = DISCOVERY_PROMPT_AR
        if values:
            agent.write(values)


def _shipped_persona(env):
    """Read the Arabic placeholder straight out of the module's data file.

    Duplicating four thousand characters of brief into this migration would
    guarantee the two drift apart, so it is parsed from the source of truth.
    """
    import os
    from lxml import etree

    from odoo.modules.module import get_module_path

    path = get_module_path("era_ai_manager")
    if not path:
        return None
    data_file = os.path.join(path, "data", "ai_staff_data.xml")
    if not os.path.exists(data_file):
        return None
    tree = etree.parse(data_file)
    found = tree.xpath(
        "//record[@id='persona_manager']/field[@name='instructions']/text()")
    return found[0] if found else None
