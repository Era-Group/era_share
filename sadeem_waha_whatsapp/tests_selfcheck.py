"""Standalone self-check for the two pieces of non-trivial logic:
ack/status -> tick glyph, and the offset-pagination stop condition.
Run: python3 tests_selfcheck.py   (no Odoo, no framework needed)
Mirrors waha_chat_action.js tick() and the controllers' has_more rule.
"""


def tick(direction, ack=None, status=None):
    if direction != "outgoing":
        return None
    if ack == -1 or status == "failed":
        return "error"
    if ack == 0 or status == "draft":
        return "pending"
    if (ack is not None and ack >= 3) or status == "read":
        return "read"
    if ack == 2 or status == "delivered":
        return "delivered"
    return "sent"


def has_more(page_len, limit):
    # No total count from WAHA: another page may exist only if this one was full.
    return page_len == limit


def next_reaction(current, tapped):
    # Tapping the emoji already set removes it (send ''); otherwise it replaces.
    return "" if current == tapped else tapped


def extract_waha_message_id(result):
    # Mirrors waha_session._extract_waha_message_id (engine-shape normalization).
    if not isinstance(result, dict):
        return result or False
    raw = result.get("id")
    if isinstance(raw, dict):
        return raw.get("_serialized") or raw.get("id") or False
    if isinstance(raw, str) and "@" in raw and "_" in raw:
        return raw
    key = result.get("key") or {}
    bare = key.get("id") or (raw if isinstance(raw, str) else "")
    if not bare:
        return False
    jid = key.get("remoteJid") or ""
    prefix = "true" if key.get("fromMe") else "false"
    return f"{prefix}_{jid}_{bare}" if jid else bare


def id_hash(waha_id):
    return (waha_id or "").rsplit("_", 1)[-1] if waha_id else ""


_RANK = {"failed": -1, "draft": 0, "sent": 1, "delivered": 2, "read": 3}


def should_write_status(current, incoming):
    # Mirrors the ack monotonic guard: write only on real advance or an error.
    if current == incoming:
        return False
    return incoming == "failed" or _RANK.get(incoming, 0) > _RANK.get(current, 0)


def _run():
    # incoming messages never show a tick
    assert tick("incoming", ack=3) is None
    # ack ladder
    assert tick("outgoing", ack=-1) == "error"
    assert tick("outgoing", ack=0) == "pending"
    assert tick("outgoing", ack=1) == "sent"
    assert tick("outgoing", ack=2) == "delivered"
    assert tick("outgoing", ack=3) == "read"
    assert tick("outgoing", ack=4) == "read"  # PLAYED -> read ticks
    # status fallback when ack absent
    assert tick("outgoing", status="failed") == "error"
    assert tick("outgoing", status="read") == "read"
    assert tick("outgoing", status="sent") == "sent"

    # pagination stop: full page -> keep going; short/empty page -> stop
    assert has_more(20, 20) is True
    assert has_more(19, 20) is False
    assert has_more(0, 20) is False

    # reaction toggle: new emoji sets it, same emoji clears it
    assert next_reaction("", "👍") == "👍"
    assert next_reaction("👍", "❤️") == "❤️"
    assert next_reaction("👍", "👍") == ""

    # WAHA send-id extraction across engine shapes; the trailing hash is what
    # ack events match on, so it must survive every shape.
    H = "3EB036ADBD877F6D884AE5"
    noweb = {"key": {"remoteJid": "201068686468@s.whatsapp.net", "fromMe": True, "id": H}}
    webjs = {"id": {"_serialized": f"true_201068686468@c.us_{H}", "id": H}}
    plain = {"id": f"true_201068686468@c.us_{H}"}
    assert id_hash(extract_waha_message_id(noweb)) == H
    assert id_hash(extract_waha_message_id(webjs)) == H
    assert id_hash(extract_waha_message_id(plain)) == H
    # the ack id (addressed @lid) shares that same hash -> match works
    ack_id = f"true_164729360244980@lid_{H}"
    assert id_hash(ack_id) == id_hash(extract_waha_message_id(noweb))
    # no id -> False (not a crash)
    assert extract_waha_message_id({"status": "PENDING"}) is False

    # status monotonic guard: advance writes, duplicate/regress don't, error does
    assert should_write_status("sent", "delivered") is True
    assert should_write_status("delivered", "read") is True
    assert should_write_status("delivered", "delivered") is False  # duplicate ack
    assert should_write_status("read", "delivered") is False       # out-of-order
    assert should_write_status("read", "sent") is False
    assert should_write_status("sent", "failed") is True           # errors matter

    print("OK")


if __name__ == "__main__":
    _run()
