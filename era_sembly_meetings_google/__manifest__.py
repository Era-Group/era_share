{
    'name': "ERA Sembly.ai Meetings — Google Meet & Gemini",
    'summary': "Adds Google as a second meeting provider: the real recording, an open share link, and Gemini's notes translated into Arabic",
    'description': """
Google Meet alongside Sembly, on the same meeting record
========================================================

Sembly gives us the summary and the structured items but, confirmed by its own
support team, **no public API, no media file and no link a colleague without a
Sembly account can open**. Google gives exactly those, for the 98% of our
meetings that run on Google Meet.

So this module adds Google as a SECOND provider rather than a replacement. All
four combinations work: Sembly alone, Google alone, both together, or neither.

مزوّد ثانٍ إلى جانب Sembly على السجل نفسه: التسجيل الحقيقي، ورابط مشاركة
يفتحه من لا يملك حساباً، وملاحظات Gemini مترجمة إلى العربية.

What it contributes
-------------------

- the Drive fileId, the recording link and an **open share link that can also
  be revoked** — Sembly's guest links never expire and cannot be withdrawn;
- Gemini's meeting notes, and an Arabic translation of them;
- matching of a Drive recording onto the Sembly meeting it belongs to, by time
  and title, since Google exposes no Sembly id to key on.

What it deliberately does not promise
-------------------------------------

Google's note-taker supports English, French, German, Italian, Japanese,
Korean, Portuguese and Spanish — **Arabic is not among them**. On an Arabic
meeting there is simply no Gemini document to fetch, and the sync treats that
as the normal case. The recording is language-neutral and still arrives, which
is the part that closes Sembly's biggest gap.
""",
    'author': "Era Group",
    'website': "https://era.net.sa",
    'category': "Productivity",
    'version': "19.0.1.4.0",
    'license': "LGPL-3",
    'depends': ['era_sembly_meetings'],
    # NOT declared as an external_dependency: Odoo needs the `packaging`
    # package to parse that field and it is absent from this venv, so
    # declaring it makes the module UNINSTALLABLE. google-auth is present
    # (2.56.2) and the client raises a clear GoogleWorkspaceError if it ever
    # is not — which is better anyway: the module stays installable and
    # configurable, and only the actual Google call fails, with a message
    # that says why.
    'data': [
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
        'views/res_config_settings_views.xml',
        'views/sembly_meeting_views.xml',
    ],
    # auto_install: Google is part of the meetings app on this
    # deployment. Safe because the provider ships OFF with no key: the
    # hourly sync gates itself on _google_enabled(), which needs both the
    # flag and a service account, so installing it reaches nobody.
    'auto_install': True,
    'installable': True,
    'application': False,
}
