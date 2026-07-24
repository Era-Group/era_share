def migrate(cr, version):
    cr.execute("""
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY cs_account_id, week
                       ORDER BY id DESC
                   ) AS row_number
              FROM cs_weekly_suggestion
             WHERE state = 'open'
        )
        UPDATE cs_weekly_suggestion suggestion
           SET state = 'dismissed',
               outcome = 'not_relevant',
               outcome_note = 'Superseded while enforcing one open work item per customer and week.',
               completed_on = NOW()
          FROM ranked
         WHERE suggestion.id = ranked.id
           AND ranked.row_number > 1
    """)
