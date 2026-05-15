# -*- coding: utf-8 -*-
{
    "name": "ERA Data Cleaning Auto Merge (Safe)",
    "summary": "Production-safe auto-merge cron for Odoo Data Cleaning duplicate groups.",
    "version": "1.0.0",
    "category": "Productivity",
    "author": "ERA Group",
    "license": "LGPL-3",
    "depends": ["base", "data_cleaning"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
    ],
    "installable": True,
    "application": False,
}
