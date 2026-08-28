"""Unfreeze the shipped playbooks so corrections can actually reach an install.

The skills were first shipped inside a noupdate block. Moving them out of it in
the data file is not enough: Odoo stores the flag per record in ir_model_data
at creation time, so an existing installation keeps ignoring updates to them
for ever — a fixed playbook would sit in the repository and never arrive.

Clearing the flag here, in a pre-migration, means the data load later in this
same upgrade picks the skills up. The persona keeps its flag deliberately: it
is the owner's to edit and an upgrade must never overwrite it.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_data
           SET noupdate = false
         WHERE module = 'era_ai_manager'
           AND model = 'aidoo.skill'
    """)
