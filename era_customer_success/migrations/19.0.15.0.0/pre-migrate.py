def migrate(cr, version):
    cr.execute("""
        ALTER TABLE cs_weekly_suggestion
        ADD COLUMN IF NOT EXISTS due_date date
    """)
    cr.execute("""
        UPDATE cs_weekly_suggestion
           SET due_date = COALESCE(week, generated_on::date, CURRENT_DATE)
         WHERE due_date IS NULL
    """)
