"""Rename ``amount`` and re-spell the plan basis before the ORM reads them.

``era.commission.line.amount`` becomes ``commission_amount`` and stops being
written by the engine: it is computed from the base, the tax, the target and the
rate. Renaming the column here rather than letting Odoo add a new one keeps the
amounts already earned exactly as they were -- a stored computed field is not
recomputed when its column already exists.

``basis`` is re-spelled to the words the specification uses: ``invoice`` is
``sales`` and ``payment`` is ``collection``.
"""


def _has_column(cr, table, column):
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = %s AND column_name = %s
    """, (table, column))
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    if _has_column(cr, 'era_commission_line', 'amount') \
            and not _has_column(cr, 'era_commission_line', 'commission_amount'):
        cr.execute("""
            ALTER TABLE era_commission_line
            RENAME COLUMN amount TO commission_amount
        """)

    if _has_column(cr, 'era_commission_plan', 'basis'):
        cr.execute("""
            UPDATE era_commission_plan SET basis = 'sales'
             WHERE basis = 'invoice'
        """)
        cr.execute("""
            UPDATE era_commission_plan SET basis = 'collection'
             WHERE basis = 'payment'
        """)

    # Every line that exists predates the commission types, and every one of
    # them was earned on an amount. The engine re-stamps the collection ones on
    # its next run; this only makes the column safe to declare required.
    if not _has_column(cr, 'era_commission_line', 'commission_type'):
        cr.execute("""
            ALTER TABLE era_commission_line
            ADD COLUMN commission_type VARCHAR
        """)
    cr.execute("""
        UPDATE era_commission_line SET commission_type = CASE
            WHEN line_type IN ('override', 'reversal', 'adjustment') THEN 'adjustment'
            ELSE 'sales' END
         WHERE commission_type IS NULL
    """)
