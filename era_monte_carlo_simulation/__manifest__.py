# -*- coding: utf-8 -*-
{
    "name": "ERA Monte Carlo Simulation",
    "version": "19.0.1.26.0",
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
- Input variables with fixed / uniform / normal / triangular / discrete
  distributions
- Vectorised NumPy simulation engine (10,000+ iterations)
- Summary statistics, percentiles (P5..P95) and threshold probabilities
- Business-language interpretation of the results
- Stored result rows with graph and pivot analysis

First business question answered:

    Given uncertain leads, conversion rate and deal value,
    what is the probable revenue range?
""",
    "author": "Era Group",
    "website": "https://www.era.net.sa",
    "license": "LGPL-3",
    "depends": ["base", "web", "crm"],
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
    "images": ["static/description/icon.png"],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
    "auto_install": False,
}
