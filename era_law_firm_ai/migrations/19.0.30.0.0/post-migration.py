"""Tell the citing agents that retrieval always hands them something.

Odoo's RAG has no relevance floor: `ORDER BY similarity DESC LIMIT 5` returns
the five nearest chunks whatever the question, so an agent asked about a
traffic fine is handed articles on custody and told to cite its sources.

A numeric threshold was measured and rejected. Across sixteen questions the
in-corpus scores ran 0.807–0.873 and the out-of-corpus ones 0.789–0.822 —
overlapping. A floor at 0.82 would have refused a legitimate question about
end-of-service pay (0.807) while admitting a trademark question the corpus
cannot answer (0.822). E5 embeddings compress everything into a narrow band,
and topical relevance is not separable inside it.

So the guard is given to the only thing that can read the chunk and see it is
about something else: the model. Coverage remains the real defence — a corpus
that holds the statute puts the right article first — but no corpus will ever
hold every one, and this is what stands in for the rest.
"""
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

GUARD = ("- النصوص التي تصلك مُسترجَعة بالتقارب لا بالمطابقة، فيصلك دائماً أقرب ما وُجد "
         "حتى لو لم يكن للسؤال علاقة به. اقرأها قبل أن تستند إليها: إن لم تجد فيها ما "
         "يجيب السؤال فقل «لا يوجد في المصادر المرفقة ما يغطي هذه المسألة» ولا تستشهد "
         "بنص لا يجيبها.")

ANCHORS = {
    'era_law_firm_ai.agent_research':
        "- ثم بيّن ما يقيّد الجواب أو يستثنيه إن وُجد.",
    'era_law_firm_ai.agent_drafting':
        "- لا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل."
        " وما لم يرد فيها فاذكر النظام دون رقم — ولا تستند إلى ما تتذكره.",
    'era_law_firm_ai.agent_contract_review':
        "   ولا تذكر رقم مادة إلا إذا ورد نصها في النصوص النظامية المرفقة بهذا الوكيل.",
}


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid, anchor in ANCHORS.items():
        agent = env.ref(xmlid, raise_if_not_found=False)
        if not agent:
            continue
        prompt = agent.system_prompt or ''
        if GUARD in prompt:
            continue
        if anchor not in prompt:
            _logger.warning(
                "%s: prompt was edited locally, so the retrieval guard was not "
                "added. Retrieval returns the nearest chunks even when nothing "
                "is relevant — add the guard by hand.", xmlid)
            continue
        agent.system_prompt = prompt.replace(anchor, anchor + "\\n" + GUARD)
        _logger.info("%s: told that retrieval is nearest-match, not exact", xmlid)
