# -*- coding: utf-8 -*-
"""Create the res.partner PDPL consent columns BEFORE data loads.

Adding stored fields to res.partner is special: while Odoo loads this module's
views it reads ``env.user.tz`` (a field related through the user's partner),
which prefetches all stored partner columns — including these new ones. If the
columns do not yet exist at that point the whole upgrade aborts with
``UndefinedColumn``. Creating them here (pre-migration runs before schema init
and data load) breaks that chicken-and-egg cleanly and idempotently.
"""


def migrate(cr, version):
    cr.execute("""
        ALTER TABLE res_partner
            ADD COLUMN IF NOT EXISTS crm_ai_intl_processing_consent boolean,
            ADD COLUMN IF NOT EXISTS crm_ai_consent_date timestamp
    """)
