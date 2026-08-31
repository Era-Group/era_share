"""Drop the caveat now that نظام الشركات and نظام العمل are published.

When contract review was first grounded, those two were not Ministry of
Justice publications and the corpus did not carry them, so the prompt named
them without article numbers and told the lawyer to check them personally.
They are on the feed now — 281 and 250 articles — so the caveat is no longer
true, and a prompt that understates what it can cite wastes the grounding.

The agent records are noupdate, so the data file alone never reaches an
existing database. Replaces the exact shipped line; a locally edited prompt
is left alone and logged, because overwriting someone's wording is worse than
asking them to look.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD = ("٤. مدى الاتساق مع الأنظمة السعودية ذات الصلة، وبخاصة نظام المعاملات المدنية.\n"
       "   ولا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل."
       " ونظام الشركات ونظام العمل ليسا ضمنها — إن لزم ذكرهما فاذكرهما بالاسم دون"
       " رقم مادة، ونبّه المحامي إلى مراجعتهما بنفسه.")
NEW = ("٤. مدى الاتساق مع الأنظمة السعودية ذات الصلة، وبخاصة نظام المعاملات المدنية"
       " ونظام الشركات ونظام العمل.\n"
       "   ولا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل.")


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    agent = env.ref('era_law_firm_ai.agent_contract_review', raise_if_not_found=False)
    if agent:
        prompt = agent.system_prompt or ''
        if NEW in prompt:
            pass
        elif OLD in prompt:
            agent.system_prompt = prompt.replace(OLD, NEW)
            _logger.info('agent_contract_review: caveat removed, both statutes now cited')
        else:
            _logger.warning(
                'agent_contract_review: the shipped line is not present, so the prompt '
                'was edited locally. نظام الشركات and نظام العمل are in the corpus now — '
                'review the wording, it may still say otherwise.')

    # Pull the two new statutes and attach them to every carrying agent.
    env['moj.law']._run_corpus_sync(force=True)
