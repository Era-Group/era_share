# -*- coding: utf-8 -*-
"""A dependency-free streamable-HTTP MCP client for Sembly AI.

The ``mcp`` Python SDK is not installed in this venv and adding a package to a
shared production-grade environment is a bigger risk than the ~200 lines here
(CLAUDE.md rules 05/06). Streamable-HTTP MCP is, in practice, a single JSON-RPC
POST whose response body is one SSE frame — which is exactly what the live
server does:

    POST https://mcp.sembly.ai/mcp
    Accept: application/json, text/event-stream
    -> 200, Content-Type: text/event-stream
       event: message
       data: {"jsonrpc":"2.0","id":1,"result":{...}}

The server returns no ``mcp-session-id`` header today, so this client is
stateless; it still captures and echoes one if a future version starts sending
it.

This module is a plain class, NOT an Odoo model — the models instantiate it.
It therefore imports nothing from ``odoo`` and is unit-testable on its own.
"""
import json
import logging
import re
import time

import requests

_logger = logging.getLogger(__name__)

# SSE recognises exactly three line terminators (CRLF, CR, LF). ``str.splitlines``
# additionally breaks on VT/FF/FS/GS/RS/NEL/U+2028/U+2029, which are ordinary
# payload characters here — see ``_parse_sse``.
_SSE_LINE_SPLIT = re.compile(r'\r\n|\r|\n')

PROTOCOL_VERSION = '2025-06-18'
CLIENT_NAME = 'odoo-era-sembly'
CLIENT_VERSION = '1.0.0'

REGION_URLS = {
    'us': 'https://mcp.sembly.ai/mcp',
    'eu': 'https://mcp-eu.sembly.ai/mcp',
}
DEFAULT_REGION = 'us'

# Connection/5xx retries. Auth failures are NOT retried — a bad token stays bad.
RETRY_BACKOFF = (2, 6)


class SemblyMcpError(Exception):
    """Raised for any MCP-level failure.

    ``message`` carries the server's own text verbatim (e.g. "Authentication
    error: Provided authorization token is invalid") so it can be surfaced in
    the UI and stored in ``sembly.sync.log`` without translation loss.
    """

    def __init__(self, message, is_auth=False):
        super().__init__(message)
        self.message = message
        self.is_auth = is_auth


def _looks_like_auth_error(text):
    return 'authentication error' in (text or '').lower() or 'authorization' in (text or '').lower()


# The live server's throttle message: "Rate limit exceeded. Retry after ~2s."
RATE_LIMIT_RETRIES = 3
_RATE_LIMIT_DELAY_RE = re.compile(r'(\d+(?:\.\d+)?)\s*s')


def _looks_like_rate_limit(text):
    return 'rate limit' in (text or '').lower()


class SemblyMcpClient:
    """Minimal MCP client exposing Sembly's three tools."""

    def __init__(self, token=None, region=DEFAULT_REGION, url=None, timeout=60):
        self.token = (token or '').strip() or None
        self.region = (region or DEFAULT_REGION).lower()
        self.url = url or REGION_URLS.get(self.region, REGION_URLS[DEFAULT_REGION])
        self.timeout = timeout
        self._session_id = None
        self._initialized = False
        self._msg_id = 0

    # ------------------------------------------------------------------ plumbing
    @property
    def token_hint(self):
        """Last 6 characters only — never the token itself (rule 03/10)."""
        if not self.token:
            return '(none)'
        return '…' + self.token[-6:] if len(self.token) > 6 else '…'

    def _headers(self):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': PROTOCOL_VERSION,
        }
        if self.token:
            headers['Authorization'] = 'Bearer %s' % self.token
        if self._session_id:
            headers['mcp-session-id'] = self._session_id
        return headers

    @staticmethod
    def _body_text(response):
        """Decode the body as UTF-8 regardless of what ``requests`` guessed.

        Sembly answers ``Content-Type: text/event-stream`` with NO charset, and
        for a charset-less ``text/*`` requests falls back to ISO-8859-1. Every
        Arabic meeting title then comes back as mojibake — and worse, the UTF-8
        continuation byte 0x85 decodes to U+0085 (NEL), a character
        ``str.splitlines()`` treats as a line break. A single 132 KB ``data:``
        line shattered into 480 fragments, of which only the first was kept:
        that is the "Could not parse Sembly MCP response" failure. SSE is UTF-8
        by specification, so decoding it ourselves is both correct and required.
        """
        content_type = (response.headers.get('Content-Type') or '').lower()
        if 'charset=' not in content_type:
            try:
                return response.content.decode('utf-8')
            except UnicodeDecodeError:
                _logger.warning(
                    "Sembly MCP body is not valid UTF-8; falling back to %s",
                    response.encoding)
        return response.text

    @staticmethod
    def _parse_sse(text):
        """Pull the JSON-RPC envelope out of an SSE body.

        One SSE event may carry its payload across SEVERAL ``data:`` lines: the
        server splits on newlines and the client is required to JOIN them back
        and parse ONCE. Parsing each line on its own works for the small frames
        (initialize, tools/list) and fails on exactly the ones that matter — a
        real ``list_meetings`` result is long enough to be split, which is how
        this reached production looking fine.

        Also accepts a bare ``application/json`` body, so a future server that
        stops streaming does not break us.
        """
        if not text:
            raise SemblyMcpError("Empty response from Sembly MCP server")
        stripped = text.strip()
        if stripped.startswith('{'):
            try:
                return json.loads(stripped)
            except ValueError:
                pass

        def parse(chunks):
            """A spec-compliant split rejoins with '\\n'. A server that split
            mid-token instead would need '' — try that too rather than lose an
            otherwise good response."""
            if not chunks:
                return None
            for glue in ("\n", ""):
                blob = glue.join(chunks).strip()
                if not blob or blob == '[DONE]':
                    return None
                try:
                    return json.loads(blob)
                except ValueError:
                    continue
            return None

        payload, chunks = None, []
        # NOT ``text.splitlines()``: it also breaks on NEL/U+2028/U+2029, which
        # are legal unescaped characters inside a JSON string and would silently
        # cut the payload in half.
        for line in _SSE_LINE_SPLIT.split(text):
            if line.startswith('data:'):
                # The spec strips ONE optional space after the colon; anything
                # further is part of the payload.
                chunk = line[5:]
                chunks.append(chunk[1:] if chunk.startswith(' ') else chunk)
            elif not line.strip():
                # A blank line ends the event.
                payload = parse(chunks) or payload
                chunks = []
        payload = parse(chunks) or payload

        if payload is None:
            raise SemblyMcpError("Could not parse Sembly MCP response: %s" % text[:300])
        return payload

    def _post(self, method, params=None, notify=False):
        """One JSON-RPC POST. Returns the ``result`` dict (None for notifications)."""
        self._msg_id += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if not notify:
            body['id'] = self._msg_id
        if params is not None:
            body['params'] = params

        last_exc = None
        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                response = requests.post(
                    self.url, headers=self._headers(), data=json.dumps(body),
                    timeout=self.timeout)
            except requests.RequestException as exc:
                last_exc = SemblyMcpError("Sembly MCP connection failed: %s" % exc)
            else:
                session_id = response.headers.get('mcp-session-id')
                if session_id:
                    self._session_id = session_id
                if response.status_code in (401, 403):
                    # Never retried: a rejected credential will be rejected again.
                    raise SemblyMcpError(
                        "Sembly MCP authentication failed (HTTP %s, token %s)"
                        % (response.status_code, self.token_hint), is_auth=True)
                if response.status_code >= 500:
                    last_exc = SemblyMcpError(
                        "Sembly MCP server error HTTP %s" % response.status_code)
                elif response.status_code >= 400:
                    raise SemblyMcpError(
                        "Sembly MCP HTTP %s: %s"
                        % (response.status_code, self._body_text(response)[:300]))
                else:
                    if notify:
                        return None
                    payload = self._parse_sse(self._body_text(response))
                    if payload.get('error'):
                        err = payload['error']
                        message = err.get('message') or json.dumps(err)
                        raise SemblyMcpError(
                            "Sembly MCP error %s: %s" % (err.get('code'), message),
                            is_auth=_looks_like_auth_error(message))
                    return payload.get('result') or {}

            if attempt < len(RETRY_BACKOFF):
                time.sleep(RETRY_BACKOFF[attempt])

        raise last_exc

    # ------------------------------------------------------------------ handshake
    def initialize(self):
        """Sent once per client instance, before the first tool call."""
        if self._initialized:
            return
        self._post('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': {'name': CLIENT_NAME, 'version': CLIENT_VERSION},
        })
        try:
            self._post('notifications/initialized', {}, notify=True)
        except SemblyMcpError:
            # The server accepts tool calls without the notification; do not
            # let a strict-proxy hiccup here block the actual work.
            _logger.debug("Sembly MCP: initialized notification not accepted, continuing")
        self._initialized = True

    # ------------------------------------------------------------------ tools
    def list_tools(self):
        """Tool catalogue. Succeeds WITHOUT a token — used by 'Test Connection'
        to prove network reachability separately from credential validity."""
        self.initialize()
        return (self._post('tools/list', {}) or {}).get('tools') or []

    def call_tool(self, name, arguments=None):
        """``tools/call``, unwrapped to the payload the outputSchema declares.

        Sembly's schemas set ``x-fastmcp-wrap-result``, so the real value sits
        under ``structuredContent['result']``; the text block is the fallback.

        Rate limiting is handled HERE, once for every caller: the live server
        answers "Rate limit exceeded. Retry after ~2s" as a tool error, and it
        tells us exactly how long to wait — so wait that long and retry, a few
        times, instead of surfacing a failure the backfill would then skip a
        meeting over.
        """
        self.initialize()
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            result = self._post('tools/call', {
                'name': name,
                'arguments': arguments or {},
            }) or {}
            if not result.get('isError'):
                break
            text = self._content_text(result)
            if _looks_like_rate_limit(text) and attempt < RATE_LIMIT_RETRIES:
                match = _RATE_LIMIT_DELAY_RE.search(text or '')
                delay = float(match.group(1)) if match else 2.0
                time.sleep(min(delay, 10.0) + 0.5)
                continue
            raise SemblyMcpError(text or "Sembly MCP tool %s failed" % name,
                                 is_auth=_looks_like_auth_error(text))

        structured = result.get('structuredContent')
        if isinstance(structured, dict):
            if 'result' in structured:
                return structured['result']
            return structured

        text = self._content_text(result)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    @staticmethod
    def _content_text(result):
        parts = []
        for block in result.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text') or '')
        return "\n".join(p for p in parts if p).strip()

    # ------------------------------------------------------------------ sugar
    def list_meetings(self, from_date=None, to_date=None, title=None, limit=200):
        """Metadata only, newest first. ``limit`` is capped at 200 by the server."""
        args = {'limit': max(1, min(int(limit or 10), 200))}
        if from_date:
            args['from_date'] = from_date
        if to_date:
            args['to_date'] = to_date
        if title:
            args['title'] = title
        result = self.call_tool('list_meetings', args)
        # The schema allows a bare string (e.g. "no meetings found").
        return result if isinstance(result, list) else []

    def get_meeting(self, meeting_id):
        """Full details: metadata + minutes + tasks/decisions/issues/risks/…"""
        result = self.call_tool('get_meeting', {'meeting_id': int(meeting_id)})
        return result if isinstance(result, dict) else None

    def list_tasks(self, **filters):
        """Exposed for completeness; the sync loop does not call it because
        ``get_meeting`` already returns each meeting's tasks."""
        args = {k: v for k, v in filters.items() if v not in (None, '', [])}
        result = self.call_tool('list_tasks', args)
        return result if isinstance(result, list) else []
