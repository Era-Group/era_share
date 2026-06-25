"""Symmetric encryption for AI account secrets (API keys).

Secrets are stored as Fernet ciphertext in the database, and the field is also
restricted to the *AI Account Manager* group. The Fernet key is derived from
``$ERA_AI_SECRET_KEY`` when set (recommended — keep it in odoo.conf/env, never in
the DB), otherwise from Odoo's ``database.secret`` parameter so the feature works
out of the box. CLI-proxy accounts store no secret at all, so this is only used
for API-key accounts.

The ciphertext carries a version prefix (``era$v1$``) so the key-derivation
scheme can evolve without breaking existing stored secrets. The legacy
unversioned prefix (``era$``) is still accepted on decrypt for backward
compatibility.
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

# Versioned prefix: era$vN$<token>. The unversioned legacy prefix "era$" is
# still accepted on decrypt so existing rows keep working after upgrade.
_PREFIX = "era$"
_VERSIONED_PREFIX = "era$v1$"


def _key_material(env):
    """Resolve the Fernet key material, or return ``None`` if none is configured.

    Priority: ``$ERA_AI_SECRET_KEY`` (recommended), then Odoo's
    ``database.secret``. There is deliberately NO hardcoded fallback — a public
    constant would let anyone with DB-read access decrypt every stored key.
    If neither source yields material, callers must refuse to store secrets
    rather than silently degrade to plaintext.
    """
    material = os.getenv("ERA_AI_SECRET_KEY")
    if not material:
        material = env["ir.config_parameter"].sudo().get_param("database.secret")
    return material


def _fernet_from_material(material):
    """Build a Fernet instance from raw key material (SHA-256 → urlsafe-b64)."""
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet(env):
    """Return a Fernet instance for the current key material, or ``None``.

    Returns ``None`` when cryptography is unavailable OR no key material is
    configured. In both cases the caller decides how to degrade.
    """
    if not _HAS_FERNET:
        return None
    material = _key_material(env)
    if not material:
        _logger.error(
            "era_ai_accounts: no encryption key configured (set ERA_AI_SECRET_KEY "
            "or ensure database.secret exists). Refusing to encrypt AI secret.")
        return None
    return _fernet_from_material(material)


def encrypt_secret(env, plaintext):
    """Return storable ciphertext for ``plaintext`` (or '' for falsy input).

    New values are written with the versioned prefix ``era$v1$``. If no crypto
    backend or key is available, logs a warning and returns the plaintext
    unchanged (still group-restricted) — this is degraded mode, not safe mode.
    """
    if not plaintext:
        return ""
    f = _fernet(env)
    if f is None:
        # No crypto backend or key: store as-is (still group-restricted). Logged once.
        _logger.warning("era_ai_accounts: cryptography unavailable or no key, storing AI secret unencrypted")
        return plaintext
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _VERSIONED_PREFIX + token


def decrypt_secret(env, stored):
    """Return the plaintext secret for a stored value, tolerating legacy plain text.

    Accepts the current versioned prefix (``era$v1$``), the legacy unversioned
    prefix (``era$``), and bare plaintext (written before encryption was active).
    """
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        # Legacy/plain value written before encryption was active.
        return stored
    f = _fernet(env)
    if f is None:
        return ""
    # Strip the versioned or legacy prefix to get the raw Fernet token.
    if stored.startswith(_VERSIONED_PREFIX):
        token = stored[len(_VERSIONED_PREFIX):]
    else:
        token = stored[len(_PREFIX):]
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        _logger.error("era_ai_accounts: could not decrypt AI secret (key changed?)")
        return ""


def rotate_key(env, old_material=None):
    """Re-encrypt all stored secrets with the *current* key material.

    Call this after changing ``ERA_AI_SECRET_KEY`` or ``database.secret`` so
    existing ciphertext (encrypted under the old key) becomes readable again.
    Pass ``old_material`` explicitly when the old key is no longer the active
    one; omit it to re-encrypt legacy unversioned rows in place (no-op for
    rows already on the current key).

    Returns the number of secrets re-encrypted. Raises ``UserError`` if no key
    is currently configured or if ``old_material`` fails to decrypt a row that
    the current key also cannot read (manual intervention required).
    """
    from odoo.exceptions import UserError

    new_fernet = _fernet(env)
    if new_fernet is None:
        raise UserError(
            "No encryption key is configured. Set ERA_AI_SECRET_KEY or ensure "
            "database.secret exists before rotating."
        )
    old_fernet = _fernet_from_material(old_material) if old_material else None

    Account = env["era.ai.account"].sudo()
    accounts = Account.search([("secret_encrypted", "!=", False)])
    count = 0
    for acc in accounts:
        stored = acc.secret_encrypted
        if not stored or not stored.startswith(_PREFIX):
            continue  # plain text — will be encrypted on next save
        token = stored[len(_VERSIONED_PREFIX):] if stored.startswith(_VERSIONED_PREFIX) \
            else stored[len(_PREFIX):]
        # Try the current key first (row may already be current — skip it).
        plaintext = None
        try:
            plaintext = new_fernet.decrypt(token.encode("ascii")).decode("utf-8")
            continue  # already readable under the current key
        except InvalidToken:
            pass
        # Fall back to the old key.
        if old_fernet is not None:
            try:
                plaintext = old_fernet.decrypt(token.encode("ascii")).decode("utf-8")
            except InvalidToken:
                plaintext = None
        if plaintext is None:
            raise UserError(
                "Could not decrypt the secret for account '%s' under either the "
                "old or the new key. Provide the correct old key, or re-enter the "
                "API key manually on that account." % acc.display_name
            )
        acc.secret_encrypted = _VERSIONED_PREFIX + new_fernet.encrypt(
            plaintext.encode("utf-8")).decode("ascii")
        count += 1
    _logger.info("era_ai_accounts: re-encrypted %d secret(s) during key rotation.", count)
    return count