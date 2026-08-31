"""Ground the two agents that were asking for article numbers they could not read.

Contract review and drafting both instruct the model to cite the statutory
basis, and both had no sources attached: a prompt that demands article numbers
without the texts to read them from gets them recalled from memory. A wrong
article number in a filed memo is the most expensive output this module has.

Two halves. The corpus flag, so the sync attaches the statute texts; and the
prompt line, so citation is bounded by what is actually attached. Contract
review additionally names نظام الشركات and نظام العمل, which are not Ministry
of Justice publications and so are not in the corpus at all — it now says so
rather than implying they are grounded.

The agent records are noupdate, so the data file alone would never reach an
existing database. Prompt edits are applied by replacing the exact shipped
line; an agent whose prompt was customised locally keeps it and is logged, on
the grounds that overwriting someone's deliberate wording is worse than
leaving one agent to be reviewed by hand.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

REPLACEMENTS = {
    'era_law_firm_ai.agent_contract_review': (
        "٤. مدى الاتساق مع الأنظمة السعودية ذات الصلة، وبخاصة نظام المعاملات المدنية"
        " ونظام الشركات ونظام العمل، مع الإشارة إلى المادة كلما أمكن.",
        "٤. مدى الاتساق مع الأنظمة السعودية ذات الصلة، وبخاصة نظام المعاملات المدنية.\n"
        "   ولا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل."
        " ونظام الشركات ونظام العمل ليسا ضمنها — إن لزم ذكرهما فاذكرهما بالاسم دون"
        " رقم مادة، ونبّه المحامي إلى مراجعتهما بنفسه.",
    ),
    'era_law_firm_ai.agent_drafting': (
        "- لا تستشهد بمادة نظامية لست واثقاً من رقمها ونصها؛ اذكر النظام دون الرقم"
        " بدلاً من ذلك.",
        "- لا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل."
        " وما لم يرد فيها فاذكر النظام دون رقم — ولا تستند إلى ما تتذكره.",
    ),
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, (old, new) in REPLACEMENTS.items():
        agent = env.ref(xmlid, raise_if_not_found=False)
        if not agent:
            continue
        agent.moj_corpus_target = True
        prompt = agent.system_prompt or ''
        if new in prompt:
            continue
        if old not in prompt:
            _logger.warning(
                "%s: the shipped citation line is not present, so the prompt was "
                "edited locally. The corpus is now attached, but review the "
                "wording by hand: it may still ask for article numbers without "
                "bounding them to the attached texts.", xmlid)
            continue
        agent.system_prompt = prompt.replace(old, new)
        _logger.info("%s: citation bounded to the attached statutes", xmlid)

    # Attach the texts now rather than waiting for the weekly run.
    env['moj.law']._run_corpus_sync(force=True)
