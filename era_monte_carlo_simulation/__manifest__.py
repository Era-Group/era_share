# -*- coding: utf-8 -*-
{
    "name": "ERA Monte Carlo Simulation",
    "version": "19.0.1.27.4",
    "category": "Productivity",
    "summary": "Model uncertainty and run Monte Carlo simulations for "
               "revenue, cost, profit and project risk analysis.",
    "description": """
Monte Carlo Simulation
======================

Define simulation models, describe uncertain inputs with probability
distributions, run thousands of scenarios and read the result in plain
business language instead of single fixed estimates.

Key features
------------
- Simulation models with predefined or safe custom formulas
- Input variables with fixed / uniform / normal / log-normal / triangular /
  discrete distributions, fittable from your own Odoo data or via Ask AI
- Input correlations (Iman-Conover) between variables that move together
- Vectorised NumPy simulation engine (up to 1,000,000 iterations)
- Summary statistics, percentiles (P5..P95), threshold probabilities,
  tail risk (VaR / CVaR) and per-input sensitivity (tornado chart)
- Distribution chart, scenario comparison board and manager risk dashboard
- Business-language interpretation plus an optional AI narrative
- PDF report and Excel export; configurable result-row storage
- One-click "reality check" value forecast on CRM opportunities

First business question answered::

    Given uncertain leads, conversion rate and deal value,
    what is the probable revenue range?
""",
    "author": "Era Group",
    "website": "https://www.era.net.sa",
    "license": "LGPL-3",
    # sale_crm provides crm.lead.order_ids / sale.order, which the one-click
    # CRM value forecast is built on (and transitively brings crm + sale).
    "depends": ["base", "web", "crm", "sale_crm"],
    "external_dependencies": {"python": ["numpy", "xlsxwriter"]},
    "data": [
        "security/monte_carlo_security.xml",
        "security/ir.model.access.csv",
        "data/ai_cron.xml",
        "data/cleanup_cron.xml",
        "views/monte_carlo_model_views.xml",
        "views/monte_carlo_variable_views.xml",
        "views/monte_carlo_run_views.xml",
        "views/monte_carlo_result_views.xml",
        "views/monte_carlo_comparison_views.xml",
        "views/monte_carlo_dashboard_views.xml",
        "views/crm_lead_views.xml",
        "views/monte_carlo_menu.xml",
        "report/monte_carlo_report.xml",
        "data/example_data.xml",
    ],
    "post_init_hook": "post_init_hook",
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
