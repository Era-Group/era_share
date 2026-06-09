"""Symmetric encryption for AI account secrets (API keys).

Secrets are stored as Fernet ciphertext in the database, and the field is also
restricted to the *AI Account Manager* group. The Fernet key is derived from
``$ERA_AI_SECRET_KEY`` when set (recommended — keep it in odoo.conf/env, never in
the DB), otherwise from Odoo's ``database.secret`` parameter so the feature works
out of the box. CLI-proxy accounts store no secret at all, so this is only used
for API-key accounts.
"""
import base64
import hashlib
import logging
import os

_logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
    _HAS_FERNET = True
except Exception:  # noqa: BLE001 - cryptography is an Odoo dep, but degrade safely
    Fernet = None
    InvalidToken = Exception
    _HAS_FERNET = False

# Marker so we can tell encrypted values apart from any legacy/plain value.
_PREFIX = "era$"


def _fernet(env):
    if not _HAS_FERNET:
        return None
    material = os.getenv("ERA_AI_SECRET_KEY")
    if not material:
        material = env["ir.config_parameter"].sudo().get_param("database.secret") or "era-ai-accounts"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(env, plaintext):
    """Return storable ciphertext for ``plaintext`` (or '' for falsy input)."""
    if not plaintext:
        return ""
    f = _fernet(env)
    if f is None:
        # No crypto backend: store as-is (still group-restricted). Logged once.
        _logger.warning("era_ai_accounts: cryptography unavailable, storing AI secret unencrypted")
        return plaintext
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt_secret(env, stored):
    """Return the plaintext secret for a stored value, tolerating legacy plain text."""
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        # Legacy/plain value written before encryption was active.
        return stored
    f = _fernet(env)
    if f is None:
        return ""
    try:
        return f.decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        _logger.error("era_ai_accounts: could not decrypt AI secret (key changed?)")
        return ""
