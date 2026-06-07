# -*- coding: utf-8 -*-
from . import pii_redaction
from . import ai_compliance_guard

# Install the AI Compliance Guard at import time: monkeypatch the native AI
# egress methods + the record-capture seam. Idempotent (safe on registry reload).
ai_compliance_guard.install()
