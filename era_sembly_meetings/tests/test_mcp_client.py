# -*- coding: utf-8 -*-
"""MCP client unit tests — no network, driven off recorded frames."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from ..services.sembly_mcp_client import SemblyMcpClient, SemblyMcpError
from . import fixtures


@tagged('post_install', '-at_install', 'sembly')
class TestSemblyMcpClient(TransactionCase):

    def _client(self, token='tok-abc123'):
        client = SemblyMcpClient(token=token)
        client._initialized = True  # skip the handshake in unit tests
        return client

    def test_parse_sse_frame(self):
        """The server answers text/event-stream; the envelope is in 'data:'."""
        payload = SemblyMcpClient._parse_sse(fixtures.TOOLS_LIST_SSE)
        self.assertEqual(payload['jsonrpc'], '2.0')
        self.assertEqual(len(payload['result']['tools']), 3)

    def test_parse_plain_json_body(self):
        """A future server that stops streaming must not break the client."""
        payload = SemblyMcpClient._parse_sse('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')
        self.assertTrue(payload['result']['ok'])

    def test_parse_unparseable_raises(self):
        with self.assertRaises(SemblyMcpError):
            SemblyMcpClient._parse_sse('not an sse frame at all')

    def test_parse_sse_payload_split_across_data_lines(self):
        """The live server splits a long result over several `data:` lines; the
        client must rejoin them and parse ONCE. Parsing line by line fails on
        exactly the responses that carry real meetings."""
        payload = SemblyMcpClient._parse_sse(fixtures.MULTILINE_LIST_MEETINGS_SSE)
        meetings = payload['result']['structuredContent']['result']
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]['id'], 14456479)
        self.assertEqual(meetings[0]['platform'], 'GOOGLE_MEET')

    def test_list_meetings_survives_a_split_frame(self):
        """End to end, through call_tool's unwrapping."""
        client = self._client()
        with patch('requests.post', return_value=fixtures.FakeResponse(
                fixtures.MULTILINE_LIST_MEETINGS_SSE)):
            meetings = client.list_meetings(limit=10)
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]['title'], "MEETING TARGET REACG ")

    def test_arabic_body_is_decoded_as_utf8_not_latin1(self):
        """The regression: `text/event-stream` carries no charset, so requests
        decodes `.text` as ISO-8859-1. Arabic then arrives as mojibake AND the
        UTF-8 byte 0x85 becomes U+0085 NEL, which splits the single `data:`
        line — the client must read `.content` as UTF-8 instead."""
        client = self._client()
        response = fixtures.FakeResponse(fixtures.ARABIC_LIST_MEETINGS_SSE)
        # Guard: the fake really does reproduce the mangling we are fixing.
        self.assertNotIn(fixtures.ARABIC_TITLE, response.text)
        with patch('requests.post', return_value=response):
            meetings = client.list_meetings(limit=10)
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]['title'], fixtures.ARABIC_TITLE)

    def test_charset_header_is_honoured_when_present(self):
        """A server that does declare its charset must still be believed."""
        client = self._client()
        with patch('requests.post', return_value=fixtures.FakeResponse(
                fixtures.ARABIC_LIST_MEETINGS_SSE,
                headers={'Content-Type': 'text/event-stream; charset=utf-8'})):
            meetings = client.list_meetings(limit=10)
        self.assertEqual(meetings[0]['title'], fixtures.ARABIC_TITLE)

    def test_parse_sse_keeps_unicode_line_separators(self):
        """U+2028 is legal unescaped in a JSON string; only CR/LF/CRLF end an
        SSE line. Splitting on Python's wider set truncates the payload."""
        payload = SemblyMcpClient._parse_sse(fixtures.LINE_SEPARATOR_SSE)
        meetings = payload['result']['structuredContent']['result']
        self.assertEqual(len(meetings), 1)
        self.assertIn(' ', meetings[0]['title'])

    def test_parse_sse_handles_crlf_terminators(self):
        payload = SemblyMcpClient._parse_sse(
            fixtures.TOOLS_LIST_SSE.replace('\n', '\r\n'))
        self.assertEqual(len(payload['result']['tools']), 3)

    def test_list_tools(self):
        client = self._client()
        with patch('requests.post', return_value=fixtures.FakeResponse(fixtures.TOOLS_LIST_SSE)):
            names = [t['name'] for t in client.list_tools()]
        self.assertEqual(names, ['list_meetings', 'get_meeting', 'list_tasks'])

    def test_structured_content_unwrapping(self):
        """Sembly's schemas set x-fastmcp-wrap-result, so the payload is under
        structuredContent['result'] — not the text block."""
        client = self._client()
        with patch('requests.post',
                   return_value=fixtures.FakeResponse(fixtures.LIST_MEETINGS_SSE)):
            meetings = client.list_meetings(limit=1)
        self.assertEqual(len(meetings), 1)
        self.assertEqual(meetings[0]['id'], fixtures.MEETING_ID)
        self.assertEqual(meetings[0]['platform'], 'Zoom')

    def test_get_meeting_returns_details(self):
        client = self._client()
        with patch('requests.post',
                   return_value=fixtures.FakeResponse(fixtures.get_meeting_sse())):
            details = client.get_meeting(fixtures.MEETING_ID)
        self.assertEqual(len(details['minutes']), 2)
        self.assertEqual(len(details['decisions']), 1)

    def test_is_error_becomes_exception_with_verbatim_text(self):
        client = self._client(token=None)
        with patch('requests.post', return_value=fixtures.FakeResponse(fixtures.AUTH_ERROR_SSE)):
            with self.assertRaises(SemblyMcpError) as ctx:
                client.list_meetings(limit=1)
        self.assertEqual(ctx.exception.message,
                         "Authentication error: No authorization header provided")
        self.assertTrue(ctx.exception.is_auth)

    def test_invalid_token_flagged_as_auth(self):
        client = self._client(token='wrong')
        with patch('requests.post', return_value=fixtures.FakeResponse(fixtures.BAD_TOKEN_SSE)):
            with self.assertRaises(SemblyMcpError) as ctx:
                client.list_meetings(limit=1)
        self.assertTrue(ctx.exception.is_auth)

    def test_http_401_is_not_retried(self):
        """A rejected credential will be rejected again — retrying only burns time."""
        client = self._client(token='wrong')
        with patch('requests.post',
                   return_value=fixtures.FakeResponse('', status_code=401)) as posted:
            with self.assertRaises(SemblyMcpError) as ctx:
                client.list_meetings(limit=1)
        self.assertEqual(posted.call_count, 1)
        self.assertTrue(ctx.exception.is_auth)

    def test_token_hint_never_leaks_the_secret(self):
        client = SemblyMcpClient(token='super-secret-value-1a2b3c')
        self.assertEqual(client.token_hint, '…1a2b3c')
        self.assertNotIn('super-secret', client.token_hint)

    def test_region_selects_host(self):
        self.assertEqual(SemblyMcpClient(region='us').url, 'https://mcp.sembly.ai/mcp')
        self.assertEqual(SemblyMcpClient(region='eu').url, 'https://mcp-eu.sembly.ai/mcp')
        self.assertEqual(SemblyMcpClient(url='https://x/mcp').url, 'https://x/mcp')

    def test_limit_capped_at_200(self):
        """The server rejects limit > 200, so the client clamps it."""
        client = self._client()
        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            import json
            captured.update(json.loads(data))
            return fixtures.FakeResponse(fixtures.LIST_MEETINGS_SSE)

        with patch('requests.post', side_effect=fake_post):
            client.list_meetings(limit=9999)
        self.assertEqual(captured['params']['arguments']['limit'], 200)

    def test_rate_limit_is_waited_out_and_retried(self):
        """The live server answers 'Rate limit exceeded. Retry after ~2s.' as a
        tool error. Surfacing that as a failure made the backfill skip the
        meeting FOREVER — the client must sleep the advertised delay and retry."""
        client = self._client()
        rate_limited = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":9,"result":{"content":[{"type":"text",'
            '"text":"Rate limit exceeded. Retry after ~1s."}],"isError":true}}\n\n'
        )
        responses = [fixtures.FakeResponse(rate_limited),
                     fixtures.FakeResponse(fixtures.LIST_MEETINGS_SSE)]
        naps = []
        with patch('requests.post', side_effect=lambda *a, **kw: responses.pop(0)), \
                patch('odoo.addons.era_sembly_meetings.services.sembly_mcp_client'
                      '.time.sleep', side_effect=naps.append):
            meetings = client.list_meetings(limit=1)
        self.assertEqual(len(meetings), 1)
        self.assertTrue(naps and naps[0] >= 1, "must honour the advertised delay")

    def test_sustained_rate_limit_finally_raises(self):
        client = self._client()
        rate_limited = (
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":9,"result":{"content":[{"type":"text",'
            '"text":"Rate limit exceeded. Retry after ~1s."}],"isError":true}}\n\n'
        )
        with patch('requests.post',
                   return_value=fixtures.FakeResponse(rate_limited)), \
                patch('odoo.addons.era_sembly_meetings.services.sembly_mcp_client'
                      '.time.sleep'):
            with self.assertRaises(SemblyMcpError) as ctx:
                client.list_meetings(limit=1)
        self.assertIn('Rate limit', ctx.exception.message)
