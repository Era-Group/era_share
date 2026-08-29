"""Bring the send window up to a working week on installations that predate it.

Until now the window was hours only, so marketing went out on Friday and
Saturday — the weekend here — and the shipped hours were 09:00-18:00, wider
than any office actually answers. The data file that carries the new defaults
is noupdate, which is right (an owner's setting must survive an upgrade) and
which also means an existing installation would never see them.

So they are set here, but only where the old shipped default is still in
place: a site that chose its own hours keeps them. The working week itself is
new, so it is created wherever it is missing and left alone where it is not.
"""

import logging

_logger = logging.getLogger(__name__)

OLD_DEFAULTS = {"era_ai_manager.send_hour_start": "9",
                "era_ai_manager.send_hour_end": "18"}
NEW_VALUES = {"era_ai_manager.send_hour_start": "10",
              "era_ai_manager.send_hour_end": "16"}


def migrate(cr, version):
    for key, old in OLD_DEFAULTS.items():
        cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s", (key,))
        row = cr.fetchone()
        if row and (row[0] or "").strip() == old:
            cr.execute(
                "UPDATE ir_config_parameter SET value = %s WHERE key = %s",
                (NEW_VALUES[key], key))
            _logger.info("era_ai_manager: %s %s -> %s", key, old, NEW_VALUES[key])
        elif row:
            _logger.info(
                "era_ai_manager: %s left at %r, this site chose it", key, row[0])

    cr.execute(
        "SELECT 1 FROM ir_config_parameter WHERE key = 'era_ai_manager.send_days'")
    if not cr.fetchone():
        cr.execute("""
            INSERT INTO ir_config_parameter (key, value, create_uid, write_uid,
                                             create_date, write_date)
            VALUES ('era_ai_manager.send_days', 'sun,mon,tue,wed,thu',
                    1, 1, now(), now())
        """)
        _logger.info("era_ai_manager: send_days set to the Sunday-Thursday week")
