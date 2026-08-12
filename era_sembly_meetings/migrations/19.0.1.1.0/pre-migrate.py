# -*- coding: utf-8 -*-
"""Carry a PRE-SPLIT database across the four-way split.

Before this release `era_sembly_meetings` was one module that owned `lead_id`,
`project_id` and `task_id`, and `era_sembly_meetings_integration` owned
`ticket_id`. Those things now live in `era_sembly_meetings_crm`,
`_tasks` and `_tickets`.

A plain `-u` cannot survive that on its own, and fails SILENTLY — every failure
below was reproduced end to end, and every one of them exits 0:

1. **The link columns are dropped.** `auto_install` is only evaluated in
   `ir.module.module.button_install`, which `-u` never calls, so the satellites
   do not install. `lead_id` / `project_id` / `task_id` are then orphaned
   xmlids of this module, `_process_end` unlinks them, and
   `ir.model.fields.unlink()` runs `ALTER TABLE … DROP COLUMN … CASCADE`.
   Every link a human ever made is gone, and nothing re-creates it: the cron
   only looks at `link_state = 'unlinked'` and the matcher skips `'manual'`.

2. **The old record rule survives and breaks the app for every employee.**
   The rule ships in a `noupdate="1"` file, and `convert.py` skips noupdate
   records unless the module is being INSTALLED. So the pre-split domain — which
   references `lead_id.user_id` — stays behind while step 1 removes the column,
   and every non-manager gets `ValueError: Invalid field sembly.meeting.lead_id`
   on any search. Managers are unaffected, so it is invisible to whoever tests
   the deploy as admin.

3. **The renamed helpdesk bridge leaves live views behind.** `update_list()`
   only walks manifests that exist on disk, so the vanished
   `era_sembly_meetings_integration` row is never reaped and its views stay
   active. They xpath `//field[@name='task_id']`, which the rewritten base arch
   no longer offers, so the meeting form and list stop opening entirely.

4. **`_tasks` can never be installed.** Its two `ir.config_parameter` records
   moved to new xmlids while the old noupdate rows survive, so the data load
   hits `ir_config_parameter_key_uniq` and rolls the install back.

Re-owning an xmlid rather than deleting the record is what makes this safe:
a reassigned row is no longer an orphan, so nothing is ever dropped, and a
value the customer tuned is preserved.

Idempotent: every statement is a no-op on a database that has already moved.
"""
import logging

_logger = logging.getLogger(__name__)

# xmlid name -> module that owns it after the split.
MOVED_XMLIDS = {
    'field_sembly_meeting__lead_id': 'era_sembly_meetings_crm',
    'field_sembly_meeting__project_id': 'era_sembly_meetings_tasks',
    'field_sembly_meeting__task_id': 'era_sembly_meetings_tasks',
    'param_sembly_meetings_task_name': 'era_sembly_meetings_tasks',
    'param_sembly_auto_create_meetings_task': 'era_sembly_meetings_tasks',
}

# The satellites' own xmlids are identical to the pre-split ones, so renaming
# the module row lets their data load OVERWRITE the stale views instead of
# colliding with them.
RENAMED_MODULES = {
    'era_sembly_meetings_integration': 'era_sembly_meetings_tickets',
}

# The employee rule is the one thing that must be force-written: it ships
# noupdate, and the pre-split domain is actively broken after the split.
NEW_USER_RULE_DOMAIN = "[(1, '=', 1)]"
NEW_USER_RULE_NAME = "Sembly Meeting: employees read every meeting"


def migrate(cr, version):
    if not version:
        return

    # -- 3. Hand the helpdesk bridge's records to its renamed successor ------
    # A row for the NEW name always exists by the time this runs: load_modules
    # calls update_list() first, which scans the addons path and inserts an
    # 'uninstalled' placeholder for every manifest on disk. So this is not a
    # rename of one row — it is: move the old row's records onto the new name,
    # then delete the old row and promote the placeholder.
    #
    # Moving the records is what matters. The successor reuses the IDENTICAL
    # xmlids, so its data load UPDATES the old views in place instead of
    # leaving them behind, active, xpath-ing at anchors the rewritten base no
    # longer has — which is what makes the meeting form unopenable. It also
    # carries `field_sembly_meeting__ticket_id` across, so that column is never
    # orphaned and never dropped.
    for old, new in RENAMED_MODULES.items():
        cr.execute("SELECT id FROM ir_module_module WHERE name = %s", (old,))
        row = cr.fetchone()
        if not row:
            continue
        old_id = row[0]

        cr.execute("UPDATE ir_model_data SET module = %s WHERE module = %s", (new, old))
        moved = cr.rowcount
        # The ir_model_data row that REPRESENTS the old module (module='base',
        # so untouched above) goes with the module row itself.
        cr.execute("DELETE FROM ir_model_data WHERE model = 'ir.module.module' AND res_id = %s",
                   (old_id,))
        cr.execute("DELETE FROM ir_module_module_dependency WHERE module_id = %s", (old_id,))
        cr.execute("DELETE FROM ir_module_module WHERE id = %s", (old_id,))
        cr.execute("""
            UPDATE ir_module_module SET state = 'to install'
             WHERE name = %s AND state = 'uninstalled'
        """, (new,))
        _logger.info("Sembly: %s -> %s (%s records moved)", old, new, moved)

    # -- 1 + 4. Re-own the moved xmlids so _process_end sees no orphan --------
    for name, new_module in MOVED_XMLIDS.items():
        cr.execute("""
            UPDATE ir_model_data SET module = %s
             WHERE module = 'era_sembly_meetings' AND name = %s
        """, (new_module, name))
        if cr.rowcount:
            _logger.info("Sembly: %s now owned by %s", name, new_module)

    # -- 1b. Pull the satellites in during THIS run --------------------------
    # Re-owning the xmlids stops the columns being dropped, but a column whose
    # module is not installed is a leftover with no Python field behind it. The
    # pre-split module linked to whatever was installed alongside it, so the
    # faithful post-split state is: whichever apps are present, its satellite
    # comes too. `auto_install` cannot do this — it is only consulted by
    # button_install, which `-u` never calls — so mark them here and let
    # load_modules pick them up on its next pass.
    for satellite, required in (
            ('era_sembly_meetings_crm', 'crm'),
            ('era_sembly_meetings_tasks', 'project'),
            ('era_sembly_meetings_tickets', 'helpdesk')):
        cr.execute("""
            UPDATE ir_module_module SET state = 'to install'
             WHERE name = %s
               AND state = 'uninstalled'
               AND EXISTS (SELECT 1 FROM ir_module_module
                            WHERE name = %s
                              AND state IN ('installed', 'to upgrade'))
        """, (satellite, required))
        if cr.rowcount:
            _logger.info("Sembly: scheduling %s for install (%s is present)",
                         satellite, required)

    # -- 2. Force the employee rule onto the new domain ----------------------
    # noupdate is stored on the ir_model_data ROW and is never rewritten by the
    # XML upsert, so flipping noupdate="0" in the file would not be enough.
    cr.execute("""
        UPDATE ir_rule r
           SET domain_force = %s, name = %s
          FROM ir_model_data d
         WHERE d.module = 'era_sembly_meetings'
           AND d.name = 'sembly_meeting_user_rule'
           AND d.model = 'ir.rule'
           AND d.res_id = r.id
    """, (NEW_USER_RULE_DOMAIN, NEW_USER_RULE_NAME))
    if cr.rowcount:
        _logger.info("Sembly: employee record rule rewritten to read-all")

    # The per-module rules that briefly existed during development are gone;
    # drop them so an OR-ed leftover cannot widen anything unexpectedly.
    cr.execute("""
        DELETE FROM ir_rule r
         USING ir_model_data d
         WHERE d.model = 'ir.rule' AND d.res_id = r.id
           AND d.module IN ('era_sembly_meetings_crm',
                            'era_sembly_meetings_tasks',
                            'era_sembly_meetings_tickets')
           AND d.name IN ('sembly_meeting_lead_user_rule',
                          'sembly_meeting_task_user_rule',
                          'sembly_meeting_ticket_user_rule')
    """)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE model = 'ir.rule'
           AND module IN ('era_sembly_meetings_crm',
                          'era_sembly_meetings_tasks',
                          'era_sembly_meetings_tickets')
           AND name IN ('sembly_meeting_lead_user_rule',
                        'sembly_meeting_task_user_rule',
                        'sembly_meeting_ticket_user_rule')
    """)
