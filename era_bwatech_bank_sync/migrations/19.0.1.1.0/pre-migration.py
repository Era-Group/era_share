"""Move the mTLS material from server paths into the database.

Runs before the ORM updates the schema so the new columns already hold the
certificates when Odoo tries to apply the NOT NULL constraint on
``certificate_file``.
"""
import base64
import logging
import os

_logger = logging.getLogger(__name__)


def _column_exists(cr, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'bwatech_connection' AND column_name = %s
        """,
        (column,),
    )
    return bool(cr.fetchone())


def _load(path):
    if not path:
        return None, None
    try:
        with open(path, "rb") as handle:
            return base64.b64encode(handle.read()), os.path.basename(path)
    except OSError:
        _logger.warning(
            "BWATECH: could not read %s during migration; upload the file manually "
            "on the connection form.", path,
        )
        return None, None


def migrate(cr, version):
    if not _column_exists(cr, "certificate_path"):
        return

    cr.execute(
        """
        ALTER TABLE bwatech_connection
            ADD COLUMN IF NOT EXISTS certificate_file bytea,
            ADD COLUMN IF NOT EXISTS certificate_filename varchar,
            ADD COLUMN IF NOT EXISTS private_key_file bytea,
            ADD COLUMN IF NOT EXISTS private_key_filename varchar
        """
    )
    cr.execute(
        "SELECT id, certificate_path, private_key_path FROM bwatech_connection"
    )
    for connection_id, cert_path, key_path in cr.fetchall():
        cert_content, cert_name = _load(cert_path)
        key_content, key_name = _load(key_path)
        if not cert_content and not key_content:
            continue
        cr.execute(
            """
            UPDATE bwatech_connection
               SET certificate_file = COALESCE(%s, certificate_file),
                   certificate_filename = COALESCE(%s, certificate_filename),
                   private_key_file = COALESCE(%s, private_key_file),
                   private_key_filename = COALESCE(%s, private_key_filename)
             WHERE id = %s
            """,
            (cert_content, cert_name, key_content, key_name, connection_id),
        )
        _logger.info(
            "BWATECH: stored the PEM material of connection %s in the database.",
            connection_id,
        )
