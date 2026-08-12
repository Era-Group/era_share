# -*- coding: utf-8 -*-
"""Payloads captured from the real Sembly MCP server and its published
automation schema, so the tests exercise the actual shapes rather than shapes
we imagined.

``TOOLS_LIST_SSE`` and the error frames below are VERBATIM responses recorded
from ``https://mcp.sembly.ai/mcp`` during this build (server version 2.13.3).
The ``get_meeting`` body follows that tool's declared ``outputSchema``.
"""
import json

# --- verbatim: tools/list, trimmed to the fields the client reads -------------
TOOLS_LIST_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":2,"result":{"tools":['
    '{"name":"list_meetings","description":"List processed meetings"},'
    '{"name":"get_meeting","description":"Get a meeting details and contents"},'
    '{"name":"list_tasks","description":"List tasks accessible by the user"}'
    ']}}\n\n'
)

# --- verbatim: initialize ----------------------------------------------------
INITIALIZE_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18",'
    '"capabilities":{"tools":{"listChanged":true}},'
    '"serverInfo":{"name":"Sembly MCP","version":"2.13.3"}}}\n\n'
)

# --- verbatim: tools/call with no Authorization header -----------------------
AUTH_ERROR_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text",'
    '"text":"Authentication error: No authorization header provided"}],'
    '"isError":true}}\n\n'
)

# --- verbatim: tools/call with an invalid token ------------------------------
BAD_TOKEN_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text",'
    '"text":"Authentication error: Provided authorization token is invalid"}],'
    '"isError":true}}\n\n'
)

MEETING_ID = 987654

# --- verbatim shapes the LIVE server actually returned (2026-08-10) ----------
# Two things here were wrong in this file until production proved otherwise,
# and both silently broke the whole import:
#   1. the server appends a timezone LABEL to its datetimes — "… 12:30 (UTC)";
#   2. a long list_meetings result is split across SEVERAL `data:` lines, which
#      a client must rejoin before parsing.
# Shaped like a real minutes[] entry: Sembly sends HTML, not plain text, and
# nests it loosely (an <h2> inside a <p>). Escaping this is what made the form
# show the tags instead of the summary.
HTML_SUMMARY = (
    "<p><h2><b>✨ Summary</b></h2></p>"
    "<p>راجع فريق المبيعات حالة سبع فرص مرتبطة بمنح للمصانع، وتم تأكيد تحصيل "
    "العمولات الخاصة بالصفقات المكتملة.</p>"
    "<p><h2><b>📋 Outline</b></h2></p>"
    "<h3><b>1. التعارف وهدف الاجتماع</b></h3>"
    "<ul><li>افتُتح الاجتماع بتحيات وتعريفات قصيرة.</li></ul>"
)

LIVE_STARTED_AT = "2026-08-10 12:30 (UTC)"
LIVE_FINISHED_AT = "2026-08-10 12:55 (UTC)"

MULTILINE_LIST_MEETINGS_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":3,"result":{"structuredContent":{"result":\n'
    'data: [{"id":14456479,"title":"MEETING TARGET REACG ",\n'
    'data: "started_at":"2026-08-10 12:30 (UTC)",\n'
    'data: "finished_at":"2026-08-10 12:55 (UTC)",\n'
    'data: "duration_seconds":1501,"platform":"GOOGLE_MEET",\n'
    'data: "owner_name":"Yasser Ali"}]}}}\n'
    '\n'
)

# --- the frame that actually broke production -------------------------------
# Recorded from mcp.sembly.ai: CRLF terminators, ONE long `data:` line, and
# Arabic titles. Decoded as ISO-8859-1 (requests' fallback for a charset-less
# text/*), the UTF-8 continuation byte 0x85 inside Arabic text becomes U+0085
# NEL — which str.splitlines() treats as a line break, cutting the payload up.
ARABIC_TITLE = "ميتنج شركه الحلول الهندسيه"          # contains the 0x85 byte
ARABIC_LIST_MEETINGS_SSE = (
    'event: message\r\n'
    'data: {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":%s}],'
    '"isError":false}}\r\n'
    '\r\n'
) % json.dumps(json.dumps([{
    'id': MEETING_ID,
    'title': ARABIC_TITLE,
    'started_at': "2026-08-10 12:30 (UTC)",
    'finished_at': "2026-08-10 12:55 (UTC)",
    'duration_seconds': 1501,
    'platform': "GOOGLE_MEET",
    'owner_name': "Yasser Ali",
    'participants': ["Montana", "Mohammed Mustafa"],
}], ensure_ascii=False), ensure_ascii=False)

# U+2028 LINE SEPARATOR is legal unescaped inside a JSON string and is another
# character str.splitlines() breaks on — same failure, different trigger.
LINE_SEPARATOR_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":3,"result":{"structuredContent":{"result":'
    '[{"id":%d,"title":"Kickoff second line","platform":"Zoom"}]}}}\n'
    '\n'
) % MEETING_ID


# --- list_meetings, shaped by the declared MeetingMetadata schema ------------
LIST_MEETINGS_SSE = (
    'event: message\n'
    'data: {"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text","text":"[]"}],'
    '"structuredContent":{"result":[{'
    '"id":%d,'
    '"title":"Acme ERP rollout - kickoff",'
    '"started_at":"2026-08-10 09:00",'
    '"finished_at":"2026-08-10 09:45",'
    '"duration_seconds":2700,'
    '"platform":"Zoom",'
    '"owner_name":"Yasser Ali",'
    '"participants":["Yasser Ali","Sara Mansour"]'
    '}]}}}\n\n'
) % MEETING_ID

LIST_MEETINGS_META = {
    'id': MEETING_ID,
    'title': "Acme ERP rollout - kickoff",
    'started_at': "2026-08-10 09:00",
    'finished_at': "2026-08-10 09:45",
    'duration_seconds': 2700,
    'platform': "Zoom",
    'owner_name': "Yasser Ali",
    'participants': ["Yasser Ali", "Sara Mansour"],
}

# --- get_meeting, shaped by the declared MeetingDetails schema ---------------
GET_MEETING_DETAILS = {
    'metadata': LIST_MEETINGS_META,
    'minutes': [
        {'type': "General", 'text': "The team agreed to start the ERP rollout in September."},
        {'type': "Project Meeting", 'text': "Phase 1 covers finance; phase 2 covers inventory."},
    ],
    'tasks': [
        {'title': "Prepare the migration plan", 'description': "Draft the data migration plan",
         'timing': "next week", 'assigned_by': "Yasser Ali", 'assigned_to': "Sara Mansour",
         'task_type': "action_item"},
    ],
    'decisions': [{'text': "Rollout starts in September", 'details': "Agreed by both sides"}],
    'issues': [{'text': "Legacy data quality is poor", 'details': "Many duplicate customers"}],
    'risks': [{'text': "Timeline may slip", 'details': "Dependent on data cleanup"}],
    'requirements': [{'text': "Arabic invoice layout", 'details': "ZATCA compliant"}],
    'highlights': [{'text': "Customer is highly engaged", 'details': ""}],
    'noteworthy_details': [{'text': "Budget approved", 'details': "SAR 400k"}],
}


def get_meeting_sse():
    import json
    return (
        'event: message\n'
        'data: %s\n\n'
    ) % json.dumps({
        'jsonrpc': '2.0', 'id': 5,
        'result': {
            'content': [{'type': 'text', 'text': json.dumps(GET_MEETING_DETAILS)}],
            'structuredContent': {'result': GET_MEETING_DETAILS},
        },
    })


# --- webhook payloads, per Sembly's published Custom Automation schema -------
WEBHOOK_TRANSCRIPTION = {
    'meeting_id': MEETING_ID,
    'meeting_title': "Acme ERP rollout - kickoff",
    'meeting_started_at': "2026-08-10T09:00:00",
    'meeting_finished_at': "2026-08-10T09:45:00",
    'meeting_duration': 2700,
    'meeting_owner_email': "yasser@era.net.sa",
    'participants': ["yasser@era.net.sa", "sara@acme-test.com"],
    'meeting_link': "https://webapp.sembly.ai/meeting/%d" % MEETING_ID,
    'meeting_transcription': "Yasser: Welcome everyone.\nSara: Thanks, glad to start.",
}

WEBHOOK_NOTES = {
    'meeting_id': MEETING_ID,
    'meeting_title': "Acme ERP rollout - kickoff",
    'meeting_started_at': "2026-08-10T09:00:00",
    'meeting_link': "https://webapp.sembly.ai/meeting/%d" % MEETING_ID,
    'meeting_notes': "The team agreed to start the ERP rollout in September.",
}

WEBHOOK_TASK = {
    'meeting_id': MEETING_ID,
    'meeting_title': "Acme ERP rollout - kickoff",
    'item_id': 5551,
    'item_header_text': "Prepare the migration plan",
    'item_text': "Draft the data migration plan before the next call",
    'item_assignee': "Sara Mansour",
    'item_link': "https://webapp.sembly.ai/meeting/%d#task-5551" % MEETING_ID,
}


class FakeResponse:
    """Stand-in for a ``requests.Response`` so the client can be driven offline.

    It reproduces the trait that broke production: the live server sends
    ``text/event-stream`` with no charset, so ``requests`` decodes ``.text`` as
    ISO-8859-1 and mangles non-ASCII. ``.content`` carries the true UTF-8 bytes,
    exactly as the real object does.
    """

    def __init__(self, text, status_code=200, headers=None, encoding=None):
        body = text.encode('utf-8') if isinstance(text, str) else text
        self.content = body
        self.status_code = status_code
        self.headers = headers if headers is not None else {
            'Content-Type': 'text/event-stream',
        }
        charset = 'charset=' in (self.headers.get('Content-Type') or '').lower()
        self.encoding = encoding or ('utf-8' if charset else 'ISO-8859-1')

    @property
    def text(self):
        return self.content.decode(self.encoding, errors='replace')
