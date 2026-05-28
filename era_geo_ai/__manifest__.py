{
    'name': 'ERA GEO — AI',
    'summary': 'AI Suggest/Apply for GEO audit findings (geo_no_answer_summary)',
    'description': """
ERA GEO — AI bridge
===================
Glue between ``era_geo`` and ``era_seo_ai``. **Auto-installs** only when
both are present, so neither parent takes a hard dependency on the other.

- Adds **`geo_no_answer_summary`** to the AI auto-fix code set: the
  *Suggest Fix (AI)* / *Apply Fix* buttons now appear on this finding and
  the agent generates a 1-2 sentence quotable answer (per installed language)
  into ``geo_answer_summary``, using the existing field-fix workflow.
- No new agent, no new prompt machinery — just an extension hook.
    """,
    'author': 'ERA — Excellence Resources Arabia',
    'website': 'https://era.net.sa',
    'license': 'OPL-1',
    'category': 'Website/SEO',
    'version': '19.0.1.0.0',
    'depends': [
        'era_geo',
        'era_seo_ai',
    ],
    'data': [],
    'installable': False,
    'application': False,
    'auto_install': False,
}
