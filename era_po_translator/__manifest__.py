{
    'name': 'ERA PO Translator',
    'version': '19.0.1.0.0',
    'category': 'Technical',
    'summary': 'In-Odoo GUI for managing and editing PO translation files',
    'description': """
ERA PO Translator
=================
Manage and edit PO translation files directly inside Odoo without SSH access
or external PO editors.

Features:
- Quick Translate Wizard to start a translation session
- Load PO entries from any installed module's i18n/ directory
- Inline editing of source (msgid) and translation (msgstr) fields
- Auto-translate all untranslated terms via Google Translate (requires deep-translator)
- Per-term suggestion panel with multiple translation alternatives
- Save translations back to the PO file and import into Odoo in one click
- Progress tracking: total / translated / untranslated / completion %
- Mail thread / activity support on translation sessions

Requirements:
- Developer Mode must be enabled
- pip install deep-translator  (for auto-translate and suggestions)
    """,
    'author': 'Era Group',
    'icon': '/era_po_translator/static/description/icon.png',
    'depends': ['mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/translation_session_views.xml',
        'views/translation_line_views.xml',
        'wizard/quick_translate_wizard_views.xml',
        'wizard/suggest_translation_wizard_views.xml',
        'views/menu.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'era_po_translator/static/src/js/list_new_button_patch.js',
            'era_po_translator/static/src/js/auto_translate_poll.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
