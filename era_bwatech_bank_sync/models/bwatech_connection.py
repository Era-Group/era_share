import base64
import json
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .bwatech_status_codes import describe as describe_status

_logger = logging.getLogger(__name__)

# Refresh the cached token this many seconds before BWATECH expires it.
TOKEN_SAFETY_MARGIN = 120
REDACTED = "***"


class BwatechConnection(models.Model):
    _name = "bwatech.connection"
    _description = "BWATECH Connection"
    _rec_name = "name"

    name = fields.Char(required=True, default="BWATECH")
    active = fields.Boolean(default=True)
    environment = fields.Selection(
        [("test", "Testing"), ("production", "Production")],
        required=True,
        default="test",
    )

    client_id = fields.Char(required=True)
    certificate_file = fields.Binary(
        string="Client Certificate (PEM)",
        required=True,
        attachment=False,
        copy=False,
        help="Upload the mTLS client certificate in PEM format. The file is stored "
             "in the database, not on the server filesystem.",
    )
    certificate_filename = fields.Char(string="Certificate Filename", copy=False)
    private_key_file = fields.Binary(
        string="Private Key (PEM)",
        attachment=False,
        copy=False,
        help="Upload the private key in PEM format. Leave empty if the certificate "
             "file already contains the key.",
    )
    private_key_filename = fields.Char(string="Private Key Filename", copy=False)
    verify_ssl = fields.Boolean(default=True)

    include_child_entities = fields.Boolean(default=True)
    auto_sync = fields.Boolean(default=True)
    sync_interval_note = fields.Char(default="Executed by Odoo scheduled action")

    log_mode = fields.Selection(
        [("all", "All calls"), ("error", "Errors only"), ("none", "Disabled")],
        default="all",
        required=True,
        string="API Logging",
        help="Errors are always worth keeping; 'All calls' also stores successful "
             "requests and responses, which is what you want during UAT.",
    )
    log_retention_days = fields.Integer(
        default=30,
        string="Log Retention (days)",
        help="Logs older than this are deleted by the BWATECH log cleanup scheduled action. 0 disables cleanup.",
    )
    log_ids = fields.One2many("bwatech.api.log", "connection_id", readonly=True)
    log_count = fields.Integer(compute="_compute_log_count")
    log_error_count = fields.Integer(compute="_compute_log_count")

    access_token = fields.Char(
        readonly=True,
        copy=False,
        groups="base.group_system",
        help="Cached bearer token. Reused until shortly before it expires.",
    )
    token_scope = fields.Char(readonly=True, copy=False, groups="base.group_system")

    last_token_at = fields.Datetime(readonly=True)
    token_expires_at = fields.Datetime(readonly=True)
    last_accounts_sync_at = fields.Datetime(readonly=True)
    last_transactions_sync_at = fields.Datetime(readonly=True)

    account_ids = fields.One2many("bwatech.bank.account", "connection_id")

    def _compute_log_count(self):
        Log = self.env["bwatech.api.log"].sudo()
        totals = dict(Log._read_group(
            [("connection_id", "in", self.ids)], ["connection_id"], ["__count"]
        ))
        errors = dict(Log._read_group(
            [("connection_id", "in", self.ids), ("state", "=", "error")],
            ["connection_id"], ["__count"],
        ))
        for connection in self:
            connection.log_count = totals.get(connection, 0)
            connection.log_error_count = errors.get(connection, 0)

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    @api.model
    def _timestamp(self):
        # BWATECH examples use an ISO-8601 UTC timestamp ending in Z.
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @api.model
    def _message_id(self):
        return uuid.uuid4().hex.upper()

    @api.model
    def _write_pem_tempfile(self, content, kind):
        """Dump one stored PEM blob into a private temporary file, return its path."""
        try:
            data = base64.b64decode(content or b"")
        except (TypeError, ValueError) as exc:
            raise UserError(
                _("تعذّر قراءة الملف المرفوع (%s). أعد رفعه بصيغة PEM.") % kind
            ) from exc
        if b"-----BEGIN" not in data:
            raise UserError(
                _("الملف المرفوع (%s) ليس بصيغة PEM. يجب أن يبدأ المحتوى بـ -----BEGIN.") % kind
            )
        # mkstemp creates the file with 0600, so the key is never world readable.
        fd, path = tempfile.mkstemp(prefix="bwatech_%s_" % kind, suffix=".pem")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        except Exception:
            os.remove(path)
            raise
        return path

    @contextmanager
    def _cert_files(self):
        """Materialise the stored certificate/key as short-lived files.

        ``requests`` only accepts filesystem paths for mTLS, so the PEM blobs
        kept in the database are written to 0600 temporary files for the
        duration of a single call and removed right afterwards. Yields the
        ``cert=`` argument plus the temporary paths, which the caller feeds to
        ``_sanitize`` so they never leak into logs.
        """
        self.ensure_one()
        connection = self.sudo()
        if not connection.certificate_file:
            raise UserError(_("لم يتم رفع ملف شهادة mTLS لهذا الاتصال."))
        paths = []
        try:
            paths.append(self._write_pem_tempfile(connection.certificate_file, "cert"))
            if connection.private_key_file:
                paths.append(self._write_pem_tempfile(connection.private_key_file, "key"))
                cert_arg = (paths[0], paths[1])
            else:
                cert_arg = paths[0]
            yield cert_arg, tuple(paths)
        finally:
            for path in paths:
                try:
                    os.remove(path)
                except OSError:
                    _logger.warning(
                        "BWATECH: temporary PEM file %s could not be removed.", path
                    )

    @api.constrains("certificate_file", "private_key_file")
    def _check_pem_files(self):
        """Reject a non-PEM upload at save time rather than mid-synchronization."""
        for connection in self:
            for content, label in (
                (connection.certificate_file, _("الشهادة")),
                (connection.private_key_file, _("المفتاح الخاص")),
            ):
                if not content:
                    continue
                try:
                    data = base64.b64decode(content)
                except (TypeError, ValueError):
                    data = b""
                if b"-----BEGIN" not in data:
                    raise ValidationError(
                        _("ملف %s المرفوع ليس بصيغة PEM. يجب أن يبدأ المحتوى بـ -----BEGIN.")
                        % label
                    )

    def _token_url(self):
        self.ensure_one()
        if self.environment == "production":
            return "https://Identity.bwatech.sa:44301/connect/mtls/token"
        return "https://b2b.ob.test.btech.ink:44301/connect/mtls/token"

    def _account_base_url(self):
        self.ensure_one()
        if self.environment == "production":
            return "https://b2b.bwatech.sa/B2B.AccountInformation.Api"
        return "https://b2b.test.btech.ink/B2B.AccountInformation.Api"

    def _sanitize(self, text, extra_secrets=()):
        """Strip credentials and temporary paths out of anything shown or stored.

        Transport exceptions routinely embed the path of the temporary PEM file
        (passed in through ``extra_secrets``), and the request headers embed the
        bearer token.
        """
        if text is None:
            return ""
        text = str(text)
        connection = self.sudo()
        secrets = [
            connection.client_id,
            connection.access_token,
        ]
        secrets.extend(extra_secrets)
        for secret in secrets:
            if secret and len(str(secret)) > 3:
                text = text.replace(str(secret), REDACTED)
        return text

    def _describe_transport_error(self, exc, extra_secrets=()):
        """Map a requests/OS level failure to (error_type, user-facing message)."""
        detail = self._sanitize(exc, extra_secrets)
        if isinstance(exc, requests.exceptions.SSLError):
            return "ssl", _(
                "فشل التحقق من شهادة mTLS مع بواتك.\n"
                "تحقق من أن ملف الشهادة والمفتاح الخاص المرفوعين صحيحان وساريان.\n"
                "التفاصيل: %s"
            ) % detail
        if isinstance(exc, requests.exceptions.Timeout):
            return "timeout", _(
                "انتهت مهلة الاتصال ببواتك.\n"
                "الخدمة قد تكون بطيئة حاليًا — أعد المحاولة، وإذا تكرر أبلغ بواتك."
            )
        if isinstance(exc, requests.exceptions.ConnectionError):
            return "connection", _(
                "تعذّر الوصول إلى خادم بواتك.\n"
                "تأكد من أن عنوان IP العام للخادم مُضاف في القائمة البيضاء لدى بواتك، "
                "ومن أن المنفذ غير محجوب.\n"
                "التفاصيل: %s"
            ) % detail
        if isinstance(exc, (FileNotFoundError, OSError)):
            return "ssl", _(
                "تعذّر تجهيز ملف الشهادة أو المفتاح الخاص مؤقتًا على الخادم.\n"
                "تأكد من صلاحية الكتابة في المجلد المؤقت لمستخدم أودو."
            )
        return "connection", _("فشل الاتصال ببواتك.\nالتفاصيل: %s") % detail

    def _describe_http_error(self, status_code, body):
        """Map an HTTP status to (error_type, user-facing message)."""
        if status_code in (401, 403):
            return "auth", _(
                "رفضت بواتك الطلب (HTTP %s): مشكلة في المصادقة أو الصلاحيات.\n"
                "تأكد أن Client ID صحيح وأن نطاق CM مُفعَّل لحسابك لدى بواتك."
            ) % status_code
        if status_code == 429:
            return "http", _(
                "تم تجاوز الحد المسموح من الطلبات لدى بواتك (HTTP 429).\n"
                "قلّل تكرار المهمة المجدولة أو أعد المحاولة لاحقًا."
            )
        if status_code >= 500:
            return "http", _(
                "خطأ في خادم بواتك (HTTP %s).\nأعد المحاولة لاحقًا."
            ) % status_code
        return "http", _(
            "رفضت بواتك الطلب (HTTP %(status)s).\nالتفاصيل: %(detail)s"
        ) % {"status": status_code, "detail": self._sanitize(body)[:500]}

    def _should_log(self, state):
        self.ensure_one()
        if self.log_mode == "none":
            return False
        if self.log_mode == "error":
            return state == "error"
        return True

    def _log_call(self, vals):
        """Persist one call. Errors are written on a separate cursor so they
        survive the rollback triggered by the UserError raised afterwards."""
        self.ensure_one()
        state = vals.get("state")
        if not self._should_log(state):
            return
        vals["connection_id"] = self.id
        self.env["bwatech.api.log"]._record(vals, isolated=(state == "error"))

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _invalidate_token(self):
        self.sudo().write({"access_token": False, "token_scope": False, "token_expires_at": False})

    def _get_access_token(self, scope="CM", force_refresh=False):
        self.ensure_one()
        connection = self.sudo()

        if not force_refresh and connection.access_token and connection.token_scope == scope:
            expiry = connection.token_expires_at
            if expiry and expiry > fields.Datetime.now() + timedelta(seconds=TOKEN_SAFETY_MARGIN):
                return connection.access_token

        url = self._token_url()
        redacted_request = json.dumps(
            {"grant_type": "client_credentials", "client_id": REDACTED, "scope": scope}
        )
        started = time.monotonic()
        temp_paths = ()
        try:
            with self._cert_files() as (cert, temp_paths):
                response = requests.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "scope": scope,
                    },
                    cert=cert,
                    verify=self.verify_ssl,
                    timeout=45,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except UserError:
            raise
        except Exception as exc:
            error_type, message = self._describe_transport_error(exc, temp_paths)
            self._log_call({
                "service": "Token",
                "endpoint": url,
                "state": "error",
                "error_type": error_type,
                "error_message": message,
                "request_body": redacted_request,
                "duration_ms": int((time.monotonic() - started) * 1000),
            })
            raise UserError(message) from exc

        base_vals = {
            "service": "Token",
            "endpoint": url,
            "http_status": response.status_code,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "request_body": redacted_request,
        }

        if response.status_code >= 400:
            error_type, message = self._describe_http_error(response.status_code, response.text)
            self._log_call({**base_vals, "state": "error", "error_type": error_type,
                            "error_message": message,
                            "response_body": self._sanitize(response.text)})
            raise UserError(message)

        try:
            payload = response.json()
        except ValueError as exc:
            message = _("رد بواتك على طلب التوكن ليس بصيغة JSON صالحة.")
            self._log_call({**base_vals, "state": "error", "error_type": "payload",
                            "error_message": message,
                            "response_body": self._sanitize(response.text)})
            raise UserError(message) from exc

        token = payload.get("access_token")
        if not token:
            message = _("رد بواتك لا يحتوي على access_token.")
            self._log_call({**base_vals, "state": "error", "error_type": "auth",
                            "error_message": message,
                            "response_body": self._sanitize(json.dumps(payload))})
            raise UserError(message)

        now = fields.Datetime.now()
        expires_in = int(payload.get("expires_in") or 3600)
        connection.write({
            "access_token": token,
            "token_scope": scope,
            "last_token_at": now,
            "token_expires_at": fields.Datetime.add(now, seconds=expires_in),
        })
        # The token itself is never stored in the log.
        self._log_call({**base_vals, "state": "success",
                        "response_body": json.dumps(dict(payload, access_token=REDACTED))})
        return token

    # ------------------------------------------------------------------
    # Account Information API
    # ------------------------------------------------------------------

    def _post_cm(self, path, request_body, service=None):
        self.ensure_one()
        url = f"{self._account_base_url()}{path}"
        service = service or path
        payload = {
            "requestHeader": {
                "messageID": self._message_id(),
                "timestamp": self._timestamp(),
            },
            **request_body,
        }
        base_vals = {
            "service": service,
            "endpoint": url,
            "message_id": payload["requestHeader"]["messageID"],
            "request_body": json.dumps(payload, ensure_ascii=False),
        }

        response = None
        for attempt in (1, 2):
            token = self._get_access_token("CM", force_refresh=(attempt == 2))
            started = time.monotonic()
            temp_paths = ()
            try:
                with self._cert_files() as (cert, temp_paths):
                    response = requests.post(
                        url,
                        json=payload,
                        cert=cert,
                        verify=self.verify_ssl,
                        timeout=60,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                    )
            except UserError:
                raise
            except Exception as exc:
                error_type, message = self._describe_transport_error(exc, temp_paths)
                self._log_call({**base_vals, "state": "error", "error_type": error_type,
                                "error_message": message,
                                "duration_ms": int((time.monotonic() - started) * 1000)})
                raise UserError(message) from exc

            base_vals["duration_ms"] = int((time.monotonic() - started) * 1000)
            base_vals["http_status"] = response.status_code

            # A cached token can be rejected early by BWATECH; refresh once.
            if response.status_code == 401 and attempt == 1:
                self._log_call({**base_vals, "state": "error", "error_type": "auth",
                                "error_message": _("انتهت صلاحية التوكن — تُعاد المحاولة بتوكن جديد."),
                                "response_body": self._sanitize(response.text)})
                self._invalidate_token()
                continue
            break

        if response.status_code >= 400:
            error_type, message = self._describe_http_error(response.status_code, response.text)
            self._log_call({**base_vals, "state": "error", "error_type": error_type,
                            "error_message": message,
                            "response_body": self._sanitize(response.text)})
            raise UserError(message)

        try:
            data = response.json()
        except ValueError as exc:
            message = _("رد بواتك ليس بصيغة JSON صالحة.")
            self._log_call({**base_vals, "state": "error", "error_type": "payload",
                            "error_message": message,
                            "response_body": self._sanitize(response.text)})
            raise UserError(message) from exc

        response_header = data.get("responseHeader") or {}
        status = response_header.get("status") or {}
        correlation_id = response_header.get("correlationID")
        status_code = str(status.get("statusCode") or "")
        base_vals.update({
            "correlation_id": correlation_id,
            "status_code": status_code,
            "status_description": status.get("statusDescription"),
            "response_body": self._sanitize(json.dumps(data, ensure_ascii=False)),
        })

        if status_code != "0":
            reason, action = describe_status(status_code, status.get("statusDescription"))
            message = _(
                "%(service)s: %(reason)s\n"
                "الإجراء المقترح: %(action)s\n"
                "رمز بواتك: %(code)s\n"
                "رقم التتبع (correlationID): %(correlation)s"
            ) % {
                "service": service,
                "reason": reason,
                "action": action,
                "code": status_code or _("غير محدد"),
                "correlation": correlation_id or _("غير متوفر"),
            }
            self._log_call({**base_vals, "state": "error", "error_type": "business",
                            "error_message": message})
            raise UserError(message)

        self._log_call({**base_vals, "state": "success"})
        return data

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_test_connection(self):
        self.ensure_one()
        self._get_access_token("CM", force_refresh=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("BWATECH"),
                "message": _("نجح الاتصال: mTLS + OAuth token."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_open_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("BWATECH API Logs"),
            "res_model": "bwatech.api.log",
            "view_mode": "list,form",
            "domain": [("connection_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_sync_accounts(self):
        for connection in self:
            data = connection._post_cm(
                "/V1/Account/AccountList",
                {"accountListRequest": {
                    "includeChildEntities": bool(connection.include_child_entities)
                }},
                service="AccountList",
            )
            accounts = (data.get("accountListResponse") or {}).get("accounts") or []
            Account = self.env["bwatech.bank.account"].sudo()
            for item in accounts:
                iban = (item.get("iban") or "").strip()
                bban = (item.get("bban") or "").strip()
                bank_code = (item.get("bankCode") or "").strip()
                if not iban and not bban:
                    _logger.warning(
                        "BWATECH returned an account without IBAN/BBAN for connection %s, skipped.",
                        connection.display_name,
                    )
                    continue
                domain = [("connection_id", "=", connection.id)]
                if iban:
                    domain.append(("iban", "=", iban))
                else:
                    domain += [("bban", "=", bban), ("bank_code", "=", bank_code)]
                rec = Account.search(domain, limit=1)
                vals = {
                    "connection_id": connection.id,
                    "account_name": item.get("accountName"),
                    "iban": iban,
                    "bban": bban,
                    "bank_name": item.get("bankName"),
                    "bank_code": bank_code,
                    "currency_code": item.get("currencyCode"),
                    "entity_name": item.get("entityName"),
                    "active": True,
                }
                if rec:
                    rec.write(vals)
                else:
                    Account.create(vals)
            connection.last_accounts_sync_at = fields.Datetime.now()
        return True

    def _run_isolated(self, operation, label, account=None):
        """Run one synchronization step inside its own savepoint.

        Without this, a ``UserError`` raised while synchronizing the third
        account would abort the loop *and* discard the two accounts already
        written in the same transaction. The savepoint confines the damage to
        the step that failed.

        The failed call is already stored in ``bwatech.api.log`` on a separate
        cursor, so rolling back here does not erase the evidence.

        :return: ``True`` when the step succeeded.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                operation()
        except Exception:
            _logger.exception(
                "BWATECH %s failed for %s on connection %s",
                label,
                account.display_account if account else _("connection level"),
                self.display_name,
            )
            return False
        return True

    def action_sync_all(self):
        """Synchronize every connection, isolating each step from the others.

        A refreshed account list is not a precondition for refreshing balances:
        when ``AccountList`` fails, the accounts already known to Odoo are still
        worth synchronizing, so the run continues instead of stopping.

        Nothing is raised when a step fails. Aborting would roll back the whole
        transaction and throw away the accounts that did synchronize, so the
        outcome is reported as a notification instead and the partial results
        are kept.
        """
        synced = failed = 0
        for connection in self:
            connection._run_isolated(
                connection.action_sync_accounts, "AccountList"
            )

            for account in connection.account_ids.filtered("active"):
                ok = connection._run_isolated(
                    account.action_sync_balance, "GetBalance", account
                )
                if account.sync_transactions:
                    ok &= connection._run_isolated(
                        account.action_sync_transactions, "TransactionsInquiry", account
                    )
                # Posting is opt-in per account and isolated on its own: a
                # journal that is misconfigured must not make a successful
                # synchronization look like a failure.
                if account.auto_post and account.unposted_count:
                    connection._run_isolated(
                        account.action_post_transactions, "PostToJournal", account
                    )
                if ok:
                    synced += 1
                else:
                    failed += 1

            connection.last_transactions_sync_at = fields.Datetime.now()

        return self._sync_notification(synced, failed)

    def _sync_notification(self, synced, failed):
        """Report the outcome of a run without discarding what succeeded."""
        if not failed:
            level, message = "success", _(
                "اكتملت المزامنة: %s حساب."
            ) % synced
        elif not synced:
            level, message = "danger", _(
                "فشلت المزامنة لكل الحسابات (%s).\n"
                "افتحي Accounting ← BWATECH ← API Logs لمعرفة السبب."
            ) % failed
        else:
            level, message = "warning", _(
                "اكتملت المزامنة جزئيًا: نجح %(ok)s حساب وفشل %(ko)s.\n"
                "الحسابات الناجحة محفوظة. التفاصيل في API Logs."
            ) % {"ok": synced, "ko": failed}
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("BWATECH"),
                "message": message,
                "type": level,
                "sticky": failed > 0,
            },
        }

    @api.model
    def cron_sync_all(self):
        for connection in self.search([("active", "=", True), ("auto_sync", "=", True)]):
            try:
                connection.action_sync_all()
            except Exception:
                # Per-account failures are already isolated and logged; this is
                # the backstop for anything that escapes a savepoint, so that one
                # broken connection cannot stop the remaining ones.
                _logger.exception(
                    "BWATECH synchronization failed for connection %s", connection.display_name
                )

    @api.model
    def cron_gc_logs(self):
        Log = self.env["bwatech.api.log"].sudo()
        for connection in self.with_context(active_test=False).search([]):
            if connection.log_retention_days <= 0:
                continue
            limit_date = fields.Datetime.subtract(
                fields.Datetime.now(), days=connection.log_retention_days
            )
            stale = Log.search([
                ("connection_id", "=", connection.id),
                ("create_date", "<", limit_date),
            ])
            if stale:
                _logger.info(
                    "BWATECH: removing %d expired API logs for connection %s",
                    len(stale), connection.display_name,
                )
                stale.unlink()
