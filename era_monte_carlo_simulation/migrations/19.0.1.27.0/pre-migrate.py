# -*- coding: utf-8 -*-
"""Deduplicate variable codes before the new unique(model_id, code) constraint.

v19.0.1.27.0 adds a database-level uniqueness constraint on
monte.carlo.variable (model_id, code). The Python constraint should have kept
the data clean, but it could be raced (and was not re-checked when a variable
was re-parented), so rename any duplicate codes first — otherwise Odoo logs an
error and silently skips creating the constraint.
"""


def migrate(cr, version):
    cr.execute("""
        SELECT id, model_id, code
          FROM monte_carlo_variable
         WHERE (model_id, code) IN (
                   SELECT model_id, code
                     FROM monte_carlo_variable
                 GROUP BY model_id, code
                   HAVING COUNT(*) > 1)
      ORDER BY model_id, code, id
    """)
    seen, renames = set(), []
    for var_id, model_id, code in cr.fetchall():
        key = (model_id, code)
        if key not in seen:
            seen.add(key)  # the first occurrence keeps its code
            continue
        suffix = 2
        while (model_id, "%s_%s" % (code, suffix)) in seen:
            suffix += 1
        new_code = "%s_%s" % (code, suffix)
        seen.add((model_id, new_code))
        renames.append((new_code, var_id))
    for new_code, var_id in renames:
        cr.execute("UPDATE monte_carlo_variable SET code = %s WHERE id = %s",
                   (new_code, var_id))
