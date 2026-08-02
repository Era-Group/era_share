"""Re-link seeded configuration parameters that lost their XML-ID.

``data/ir_config_parameter_data.xml`` seeds each setting as a record with an
XML-ID. Odoo's own ``ir.config_parameter.set_param`` DELETES the row when it
is handed a falsy value, which also takes the ``ir.model.data`` link with it —
so unticking a boolean setting in the UI orphaned that XML-ID. The parameter
was then written back as a plain row with no XML-ID (see
``ResConfigSettings._ev_persist_boolean_params``).

On the next upgrade the data file finds no record for its XML-ID and tries to
INSERT, which trips the unique index on ``key``::

    psycopg2.errors.UniqueViolation: duplicate key value violates unique
    constraint "ir_config_parameter_key_uniq"

That aborts the whole module load, so the module can no longer be upgraded at
all. Restore the missing links before the data file is parsed; the file is
``noupdate="1"``, so a re-linked record is then simply left alone and the
administrator's value is preserved.

This runs pre-migration precisely because it has to happen before the data
files are loaded.
"""
import logging

_logger = logging.getLogger(__name__)

MODULE = "era_email_verification"

# XML-ID (without the module prefix) -> parameter key suffix.
_SEEDED = {
    "ev_param_verify_tls": "verify_tls",
    "ev_param_timeout_connect": "timeout_connect",
    "ev_param_timeout_read": "timeout_read",
    "ev_param_batch_size": "batch_size",
    "ev_param_check_smtp": "default_check_smtp",
    "ev_param_check_catch_all": "default_check_catch_all",
    "ev_param_min_score": "min_eligible_score",
    "ev_param_stale_days": "stale_days",
    "ev_param_auto_recheck": "auto_recheck",
    "ev_param_push_enabled": "push_enabled",
    "ev_param_reconcile_stale_minutes": "reconcile_stale_minutes",
    "ev_param_stuck_batch_hours": "stuck_batch_hours",
    "ev_param_blacklist_undeliverable": "blacklist_undeliverable",
    "ev_param_blacklist_risky": "blacklist_risky",
    "ev_param_blacklist_disposable": "blacklist_disposable",
    "ev_param_blacklist_unknown": "blacklist_unknown",
    "ev_param_blacklist_catch_all": "blacklist_catch_all",
}


def migrate(cr, version):
    relinked = []
    for xml_id, suffix in _SEEDED.items():
        key = "%s.%s" % (MODULE, suffix)
        cr.execute("SELECT id FROM ir_config_parameter WHERE key = %s", (key,))
        param = cr.fetchone()
        if not param:
            continue  # nothing to link; the data file will create it normally
        cr.execute(
            "SELECT id FROM ir_model_data WHERE module = %s AND name = %s",
            (MODULE, xml_id))
        if cr.fetchone():
            continue  # link intact
        cr.execute(
            """
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate)
            VALUES (%s, %s, 'ir.config_parameter', %s, TRUE)
            ON CONFLICT (module, name) DO NOTHING
            """,
            (MODULE, xml_id, param[0]))
        relinked.append(key)

    if relinked:
        _logger.info(
            "era_email_verification: re-linked %d orphaned configuration "
            "parameter(s) so the data file does not re-insert them: %s",
            len(relinked), ", ".join(sorted(relinked)))
