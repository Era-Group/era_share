{
    'name': 'Era AI Extensions',
    'summary': 'Overrides for the AI provider to allow configuring longer request timeouts.',
    'description': """
        Adds a global timeout setting plus per-provider overrides so OpenAI requests keep trying
        longer than the default 30 seconds before failing with ReadTimeout.
    """,
    'author': 'Era Group',
    'website': 'https://era.net.sa',
    'category': 'Technical/Tools',
    'version': '19.0.0.1',
    'license': 'LGPL-3',
    'installable': True,
    'application': False,
    'auto_install': False,
    'depends': [
        'base',
        'ai',
    ],
    'data': [],
}
