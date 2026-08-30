{
    "name": "ERA BWATECH Bank Sync",
    "version": "19.0.1.3.0",
    "category": "Accounting/Accounting",
    "summary": "BWATECH bank accounts, balances and transaction synchronization",
    "author": "ERA",
    "website": "https://era.net.sa",
    "license": "LGPL-3",
    # account_accountant carries the bank reconciliation engine this module's
    # workflow relies on: the matching rules, the Bank Matching screen and the
    # scheduled auto-reconciliation. Declaring it here installs it with us
    # instead of leaving the reconciliation half silently missing.
    "depends": ["account", "account_accountant"],
    "data": [
        "security/ir.model.access.csv",
        "views/bwatech_connection_views.xml",
        "views/bwatech_bank_account_views.xml",
        "views/bwatech_transaction_views.xml",
        "views/bwatech_api_log_views.xml",
        "views/menu.xml",
        "data/ir_cron.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
}
