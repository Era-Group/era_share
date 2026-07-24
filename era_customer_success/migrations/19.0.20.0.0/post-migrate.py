def migrate(cr, version):
    cr.execute("""
        UPDATE csm_offering
           SET rejected_on = CURRENT_TIMESTAMP
         WHERE state = 'rejected'
           AND rejected_on IS NULL
    """)
