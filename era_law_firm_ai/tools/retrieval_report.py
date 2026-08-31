"""Score the research agent's retrieval against a fixed question set.

Run it before and after changing the corpus. Adding statutes is supposed to
fill gaps, but it also adds competitors for the five chunks a question gets,
and neither effect is visible from the outside: retrieval never errors and
never returns nothing. This prints the numbers that decide whether an
addition helped.

    ./venv/bin/python ce/odoo-bin shell -c odoo.conf -d <db> --no-http \
        < era_law_firm_ai/tools/retrieval_report.py

COVERED questions name the statute that should answer them; the rank of its
first chunk is what matters. UNCOVERED ones have no answer in the corpus at
all — they exist to show what an unanswerable question scores, because that
is the number a relevance threshold would have to beat, and so far it cannot:
the two ranges overlap.
"""
from odoo.addons.ai.utils.llm_api_service import LLMApiService
from odoo.tools import SQL

AGENT = 'era_law_firm_ai.agent_research'

COVERED = [
    ("ما شروط قيد المحامي في جدول المحامين الممارسين؟", "المحاماة"),
    ("ما مدة الاعتراض على الحكم بالاستئناف؟", "المرافعات"),
    ("متى يجوز طلب التنفيذ الجبري ضد المدين؟", "التنفيذ"),
    ("ما اختصاص ديوان المظالم؟", "المظالم"),
    ("كيف يتم تأسيس شركة مساهمة مقفلة؟", "الشركات"),
    ("ما مكافأة نهاية الخدمة في نظام العمل؟", "العمل"),
    ("ما شروط صحة عقد البيع؟", "المعاملات المدنية"),
    ("متى تسقط الدعوى بالتقادم؟", "المعاملات المدنية"),
    ("ما إجراءات التبليغ القضائي؟", "المرافعات"),
    ("ما حالات رد القاضي عن نظر الدعوى؟", "المرافعات"),
    ("ما نسبة ضريبة القيمة المضافة وكيف تُحتسب؟", "القيمة المضافة"),
    ("ما إجراءات تسجيل علامة تجارية؟", "العلامات التجارية"),
    ("ما عقوبة الرشوة للموظف العام؟", "الرشوة"),
    ("ما اشتراكات التأمينات الاجتماعية على صاحب العمل؟", "التأمينات"),
    ("ما شروط صحة الوكالة التجارية؟", "الوكالات التجارية"),
]

UNCOVERED = [
    "ما اشتراطات رخصة البناء في أمانة الرياض؟",
    "ما غرامة تجاوز السرعة المقررة؟",
    "ما شروط قبول الطالب في الجامعات الحكومية؟",
    "ما ضوابط الإعلان عن المستحضرات الدوائية؟",
]


def run(env):
    agent = env.ref(AGENT)
    model = agent._get_embedding_model()
    service = LLMApiService(env, provider='custom_llm')
    checksums = agent.sources_ids.mapped('attachment_id.checksum')

    def ranked(question, limit=30):
        vector = service.get_embedding(
            input=[service._format_for_embedding(content=question, mode='query')],
            dimensions=1536, model=model)['data'][0]['embedding']
        return env.execute_query(SQL('''
            SELECT 1 - (e.embedding_vector <=> %s::vector) AS similarity, a.name
            FROM ai_embedding e JOIN ir_attachment a ON a.id = e.attachment_id
            WHERE a.checksum = ANY(%s) AND e.embedding_model = %s
              AND e.embedding_vector IS NOT NULL
            ORDER BY similarity DESC LIMIT %s''',
            str(vector), checksums, model, limit))

    print(f'corpus: {env["moj.law"].search_count([])} statutes, '
          f'{len(agent.sources_ids)} sources on {agent.name}')
    env.cr.execute("select count(*) from ai_embedding where embedding_vector is not null")
    print(f'indexed chunks: {env.cr.fetchone()[0]}\n')

    at = {1: 0, 3: 0, 5: 0, 10: 0, 20: 0}
    covered_scores, misses = [], []
    print('COVERED — rank of the first chunk from the expected statute')
    for question, expected in COVERED:
        rows = ranked(question)
        rank = next((i for i, (_s, n) in enumerate(rows, 1) if expected in (n or '')), None)
        covered_scores.append(rows[0][0] if rows else 0)
        for k in at:
            if rank and rank <= k:
                at[k] += 1
        if rank is None or rank > 5:
            misses.append((question, expected, rank))
        print(f'  {(str(rank) if rank else ">30"):>3}  {rows[0][0]:.3f}  '
              f'({expected[:16]:18}) {question[:44]}')

    print('\nUNCOVERED — nothing in the corpus answers these')
    uncovered_scores = []
    for question in UNCOVERED:
        rows = ranked(question, limit=1)
        score = rows[0][0] if rows else 0
        uncovered_scores.append(score)
        print(f'       {score:.3f}  {question[:44]}  -> {(rows[0][1] or "")[:30]}')

    total = len(COVERED)
    print('\nrecall:')
    for k in sorted(at):
        print(f'  @{k:<3} {at[k]}/{total}  {"#" * at[k]}')
    print(f'\nbest-match similarity  covered {min(covered_scores):.3f}-{max(covered_scores):.3f}'
          f'   uncovered {min(uncovered_scores):.3f}-{max(uncovered_scores):.3f}')
    print(f'separation: {min(covered_scores) - max(uncovered_scores):+.3f}'
          '   (positive means a relevance threshold could work)')
    if misses:
        print('\noutside the top 5 — these are what more coverage has to fix:')
        for question, expected, rank in misses:
            print(f'  rank {rank}: {expected} — {question[:50]}')


run(env)  # noqa: F821  (odoo shell provides env)
