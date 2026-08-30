# ERA BWATECH Bank Sync — Odoo 17 starter

This is the first implementation stage for BWATECH Cash Management integration.

Implemented:
- BWATECH mTLS + OAuth client_credentials token request.
- Test and Production token URLs from BWATECH specification.
- Account List sync.
- Get Account Balance sync.
- Transactions Inquiry sync.
- Deduplication by account + sequenceNumber.
- Incremental synchronization using lastSequenceNumber.
- Scheduled action (disabled by default).
- Raw staging transactions retained in Odoo.

Intentionally NOT enabled yet:
- Creating `account.bank.statement.line` records.

Reason:
The supplied `Transactions Inquiry` response specifies `amount` but the reviewed section does not define a debit/credit indicator or a guaranteed sign convention for that service. Posting unsigned/incorrectly signed transactions into Odoo reconciliation would be unsafe. Once BWATECH confirms the sign convention (or provides the debit/credit field for this endpoint), the staging records can be posted to the configured Bank Journal.

## BWATECH prerequisites

Provide BWATECH with the public outbound IP of the Odoo/integration server for whitelisting.

From BWATECH obtain:
- `client_id`
- Client certificate / mTLS onboarding details
- Authorization for scope `CM`
- Testing access

Upload the client certificate and the private key (PEM) directly on the BWATECH Connection record. Both are stored in the database, not on the server filesystem, so they follow the database backup and no manual file copy is needed when the server is replaced. Do not commit private keys to Git.

## Install

Copy `era_bwatech_bank_sync` into your custom addons directory, update Apps List, then install **ERA BWATECH Bank Sync**.

Go to:
Accounting -> BWATECH -> Connections

Create a connection:
- Environment: Testing
- Client ID: provided by BWATECH
- Client Certificate (PEM): upload the certificate file
- Private Key (PEM): upload the key file (leave empty if the certificate file already contains it)
- Verify SSL: enabled

Then:
1. Test Connection
2. Sync Accounts
3. Open a bank account
4. Refresh Balance
5. Sync Transactions

Only after successful UAT should the cron be enabled.

## Logging & error handling

Every call to BWATECH is recorded in **Accounting -> BWATECH -> API Logs** (`bwatech.api.log`).

Each record stores the service name, endpoint, our `messageID`, BWATECH's
`correlationID`, HTTP status, BWATECH `statusCode`, duration, and the full
request/response bodies (truncated at 20,000 characters).

**Quote the `correlationID` when raising a ticket with BWATECH** — it is their
end-to-end trace identifier.

### Configuration (per connection)

| Field | Meaning |
|---|---|
| `API Logging` | `All calls` (default, recommended during UAT), `Errors only`, or `Disabled`. |
| `Log Retention (days)` | Default 30. The daily **BWATECH: Cleanup API Logs** scheduled action deletes older records. `0` disables cleanup. |

### Failure-surviving logs

Failed calls are written on a **separate database cursor**, so the log record
survives the transaction rollback caused by the `UserError` shown to the user.
Without this, a failed sync would leave no trace in the database.

### Certificate storage

The PEM material lives in the `bwatech_connection` table (`certificate_file`,
`private_key_file`). Since `requests` only accepts filesystem paths for mTLS,
each call writes the blobs to `0600` temporary files through `_cert_files()` and
removes them in a `finally` block as soon as the request returns.

Upgrading from an earlier build runs `migrations/17.0.1.1.0/pre-migration.py`, which
reads the files still referenced by the old `certificate_path` / `private_key_path`
columns and loads them into the new fields. Any file it cannot read is reported
in the server log and has to be uploaded by hand.

### Secret redaction

`client_id`, the bearer token, and the path of the temporary PEM file of the
call are replaced with `***` in **both** the stored logs and the error messages
shown in the UI. Transport-layer exceptions from `requests` routinely embed that
path, which is why this is applied to every message.

### Error classification

Transport and HTTP failures are mapped to actionable Arabic messages:

| Condition | Message points the user to |
|---|---|
| `SSLError` | Validity of the uploaded certificate / private key |
| `Timeout` | Retry, escalate to BWATECH if persistent |
| `ConnectionError` | **IP whitelisting** with BWATECH, firewall |
| `FileNotFoundError` / `OSError` | Temporary directory not writable by the Odoo user |
| HTTP 401 / 403 | `client_id`, and whether the `CM` scope is enabled |
| HTTP 429 | Reduce the cron frequency |
| HTTP 5xx | BWATECH-side outage, retry later |

BWATECH business codes (`responseHeader.status.statusCode`) are translated in
`models/bwatech_status_codes.py` — e.g. `2185 B2B_ServiceNotAllowed` becomes a
message telling the user to ask BWATECH to enable the CM scope. Unmapped codes
fall back to BWATECH's own `statusDescription`.

## Token management

The access token is cached on the connection (`access_token`, `token_scope`,
`token_expires_at`) and reused until 120 seconds before expiry, instead of
requesting a new token on every call. The field is restricted to
`base.group_system` and is never written to the API log.

If BWATECH rejects a cached token with **HTTP 401**, the call is retried once
with a freshly issued token before surfacing an error.

`Test Connection` always forces a fresh token so it genuinely exercises the
mTLS handshake.
