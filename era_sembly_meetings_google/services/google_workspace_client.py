# -*- coding: utf-8 -*-
"""Google Workspace client: Drive + Meet, via a service account.

WHY A SERVICE ACCOUNT WITH DOMAIN-WIDE DELEGATION, and not per-user OAuth:

* Meet recordings land in the **organiser's My Drive**, not in a shared folder,
  so finding "every meeting" means reading a different person's Drive each
  time. Impersonation does that with one admin authorisation; per-user OAuth
  would need a consent flow per employee and a refresh token that can be
  revoked from a phone.
* It has to run in a cron with no browser.
* Issuing the open share link needs ``permissions.create``, which Google grants
  only to the FULL ``auth/drive`` scope — ``drive.file`` covers files our own
  app created, and Meet created these. That is a real widening of access and is
  called out in the README rather than buried here.

``google-auth`` is already in this venv (2.56.2), so this adds no dependency
(CLAUDE.md rules 05/06). Everything else is plain ``requests`` against the REST
APIs, mirroring how ``sembly_mcp_client`` stays dependency-light.

This module is a plain class, NOT an Odoo model: it imports nothing from odoo
and is unit-testable on its own.
"""
import json
import logging

import requests

_logger = logging.getLogger(__name__)

DRIVE_API = 'https://www.googleapis.com/drive/v3'
MEET_API = 'https://meet.googleapis.com/v2'
TOKEN_URI = 'https://oauth2.googleapis.com/token'

# The full drive scope is required by permissions.create; see the module
# docstring. meetings.space.readonly is what conferenceRecords needs.
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/meetings.space.readonly',
]

# Meet drops recordings here, and names the Gemini document after the meeting.
RECORDINGS_FOLDER = 'Meet Recordings'
GEMINI_DOC_MARKERS = ('Notes by Gemini', 'Gemini', 'ملاحظات')

DEFAULT_TIMEOUT = 60


class GoogleWorkspaceError(Exception):
    """Any Google-side failure, carrying the API's own message verbatim."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        self.status = status


class GoogleWorkspaceClient:
    """Drive + Meet, acting as one impersonated user at a time."""

    def __init__(self, service_account_info, subject=None, timeout=DEFAULT_TIMEOUT):
        """``subject`` is the user being impersonated; None = the SA itself,
        which sees no My Drive and is only useful for a credentials check."""
        self.info = service_account_info or {}
        self.subject = subject
        self.timeout = timeout
        self._token = None

    # ------------------------------------------------------------------ auth
    @property
    def client_email(self):
        return self.info.get('client_email') or '(none)'

    def for_user(self, subject):
        """A second client impersonating someone else, same credentials."""
        return type(self)(self.info, subject=subject, timeout=self.timeout)

    def _access_token(self):
        if self._token:
            return self._token
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover - the venv has it
            raise GoogleWorkspaceError(
                "google-auth is not available in this environment: %s" % exc) from exc

        if not self.info.get('client_email') or not self.info.get('private_key'):
            raise GoogleWorkspaceError(
                "The service account JSON is missing client_email/private_key.")
        try:
            creds = service_account.Credentials.from_service_account_info(
                self.info, scopes=SCOPES)
            if self.subject:
                creds = creds.with_subject(self.subject)
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the UI
            raise GoogleWorkspaceError(
                "Google refused the service account%s: %s"
                % (" impersonating %s" % self.subject if self.subject else "", exc)) from exc
        self._token = creds.token
        return self._token

    def _headers(self):
        return {'Authorization': 'Bearer %s' % self._access_token(),
                'Content-Type': 'application/json'}

    def _call(self, method, url, params=None, payload=None):
        try:
            response = requests.request(
                method, url, headers=self._headers(), params=params,
                data=json.dumps(payload) if payload is not None else None,
                timeout=self.timeout)
        except requests.RequestException as exc:
            raise GoogleWorkspaceError("Google API connection failed: %s" % exc) from exc

        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            # Google's own error text is the most useful thing we can show.
            try:
                detail = response.json().get('error', {}).get('message') or response.text
            except ValueError:
                detail = response.text
            raise GoogleWorkspaceError(
                "Google API %s: %s" % (response.status_code, str(detail)[:400]),
                status=response.status_code)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GoogleWorkspaceError("Unparseable Google response: %s" % exc) from exc

    # ----------------------------------------------------------------- drive
    def _list_files(self, query, fields, page_limit=10):
        """Paged files.list. ``page_limit`` bounds a runaway sweep."""
        out, token, pages = [], None, 0
        while pages < page_limit:
            params = {
                'q': query,
                'fields': 'nextPageToken, files(%s)' % fields,
                'pageSize': 100,
                'spaces': 'drive',
                'supportsAllDrives': 'true',
                'includeItemsFromAllDrives': 'true',
                'orderBy': 'createdTime desc',
            }
            if token:
                params['pageToken'] = token
            data = self._call('GET', '%s/files' % DRIVE_API, params=params) or {}
            out.extend(data.get('files') or [])
            token = data.get('nextPageToken')
            pages += 1
            if not token:
                break
        return out

    def find_recordings_folder(self):
        folders = self._list_files(
            "mimeType='application/vnd.google-apps.folder' and name='%s' and trashed=false"
            % RECORDINGS_FOLDER, 'id, name')
        return folders[0]['id'] if folders else None

    def list_recordings(self, after=None, page_limit=10):
        """Meet recordings in this user's Drive, newest first.

        Matched by mime type rather than by folder alone: a recording moved out
        of "Meet Recordings" is still a recording, and a folder that was renamed
        (or localised) would otherwise hide everything inside it.
        """
        clauses = ["mimeType='video/mp4'", "trashed=false"]
        if after:
            clauses.append("createdTime > '%s'" % after)
        return self._list_files(
            " and ".join(clauses),
            'id, name, createdTime, modifiedTime, size, webViewLink, '
            'webContentLink, videoMediaMetadata, owners(emailAddress), parents',
            page_limit=page_limit)

    def list_gemini_notes(self, after=None, page_limit=10):
        """Gemini meeting-notes documents.

        NOTE: Google only produces these for the eight languages its note-taker
        supports, and ARABIC IS NOT ONE OF THEM — so on an Arabic-speaking
        workspace this legitimately returns nothing. That is not an error, and
        callers must not treat it as one.
        """
        clauses = ["mimeType='application/vnd.google-apps.document'", "trashed=false"]
        if after:
            clauses.append("createdTime > '%s'" % after)
        name_clause = " or ".join("name contains '%s'" % m for m in GEMINI_DOC_MARKERS)
        clauses.append("(%s)" % name_clause)
        return self._list_files(
            " and ".join(clauses),
            'id, name, createdTime, modifiedTime, webViewLink, owners(emailAddress)',
            page_limit=page_limit)

    def export_document_text(self, file_id):
        """A Google Doc as plain text, for the notes body."""
        try:
            response = requests.get(
                '%s/files/%s/export' % (DRIVE_API, file_id),
                headers={'Authorization': 'Bearer %s' % self._access_token()},
                params={'mimeType': 'text/plain'}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GoogleWorkspaceError("Google export failed: %s" % exc) from exc
        if response.status_code >= 400:
            raise GoogleWorkspaceError(
                "Google export %s: %s" % (response.status_code, response.text[:300]),
                status=response.status_code)
        # Drive sends UTF-8 without always saying so — the same trap Sembly set.
        return response.content.decode('utf-8', 'replace')

    def download_file_to(self, file_id, destination, max_bytes=None):
        """Stream one private Drive blob into a seekable binary destination.

        This is intentionally separate from ``_call``: media responses are
        binary and can be gigabytes, while that helper parses small JSON bodies.
        It never changes the file's sharing permissions.
        """
        metadata = self._call(
            'GET', '%s/files/%s' % (DRIVE_API, file_id),
            params={'fields': 'id,name,mimeType,size,capabilities(canDownload)',
                    'supportsAllDrives': 'true'}) or {}
        if metadata.get('mimeType') != 'video/mp4':
            raise GoogleWorkspaceError("The Drive recording is not an MP4 file")
        if not (metadata.get('capabilities') or {}).get('canDownload', True):
            raise GoogleWorkspaceError("Google Drive has disabled this download")
        try:
            expected = int(metadata.get('size') or 0)
        except (TypeError, ValueError):
            expected = 0
        if max_bytes and expected and expected > max_bytes:
            raise GoogleWorkspaceError("The Drive recording exceeds the upload limit")
        try:
            response = requests.get(
                '%s/files/%s' % (DRIVE_API, file_id),
                headers={'Authorization': 'Bearer %s' % self._access_token()},
                params={'alt': 'media', 'supportsAllDrives': 'true'},
                stream=True, timeout=(10, max(self.timeout, 600)))
        except requests.RequestException as exc:
            raise GoogleWorkspaceError("Google download failed: %s" % exc) from exc
        try:
            if response.status_code >= 400:
                raise GoogleWorkspaceError(
                    "Google download %s: %s"
                    % (response.status_code, response.text[:300]),
                    status=response.status_code)
            written = 0
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if max_bytes and written > max_bytes:
                    raise GoogleWorkspaceError(
                        "The Drive recording exceeds the upload limit")
                destination.write(chunk)
        finally:
            response.close()
        if expected and written != expected:
            raise GoogleWorkspaceError(
                "Google Drive download was incomplete (%s of %s bytes)"
                % (written, expected))
        destination.flush()
        destination.seek(0)
        return {'size': written, 'name': metadata.get('name') or ''}

    def share_anyone_with_link(self, file_id):
        """Grant "anyone with the link can view" and return the view URL.

        ``allowFileDiscovery=False`` is the difference between "anyone with the
        link" and "public and indexable". It must stay False.
        """
        self._call('POST', '%s/files/%s/permissions' % (DRIVE_API, file_id),
                   params={'supportsAllDrives': 'true'},
                   payload={'role': 'reader', 'type': 'anyone',
                            'allowFileDiscovery': False})
        data = self._call('GET', '%s/files/%s' % (DRIVE_API, file_id),
                          params={'fields': 'webViewLink, webContentLink',
                                  'supportsAllDrives': 'true'}) or {}
        return data.get('webViewLink')

    def revoke_link_sharing(self, file_id):
        """Remove the anyone-with-link permission again.

        Sembly cannot do this at all — its guest links "never expire and cannot
        be revoked" — so keeping the ability here is part of the point.
        """
        data = self._call(
            'GET', '%s/files/%s/permissions' % (DRIVE_API, file_id),
            params={'fields': 'permissions(id, type)', 'supportsAllDrives': 'true'}) or {}
        removed = 0
        for permission in data.get('permissions') or []:
            if permission.get('type') == 'anyone':
                self._call('DELETE', '%s/files/%s/permissions/%s'
                           % (DRIVE_API, file_id, permission['id']),
                           params={'supportsAllDrives': 'true'})
                removed += 1
        return removed

    # ------------------------------------------------------------------ meet
    def list_conference_records(self, after=None, page_size=100):
        """conferenceRecords, newest first.

        Google keeps these for 30 DAYS ONLY, so this can never rebuild history
        — the Drive sweep is what covers anything older.
        """
        params = {'pageSize': page_size}
        if after:
            params['filter'] = 'start_time>="%s"' % after
        data = self._call('GET', '%s/conferenceRecords' % MEET_API, params=params) or {}
        return data.get('conferenceRecords') or []

    def check(self):
        """Prove the credentials, and say WHICH half failed if one does."""
        self._access_token()
        probe = self._call('GET', '%s/about' % DRIVE_API,
                           params={'fields': 'user(emailAddress, displayName)'}) or {}
        return probe.get('user') or {}
