# Task 07 — WhatsApp Conversation Intelligence (ذكاء محادثات واتساب)

## Context

**Phase:** 3 | **Priority:** Medium-High  
**Duration:** 2–3 weeks  
**Dependencies:** None (independent)  
**Module name:** `era_crm_whatsapp_intelligence`  
**Path:** `submodules/era_share_latest/era_crm_whatsapp_intelligence/`

## Business Value

Saves hours of admin work and ensures no commitment is lost in conversation. Voice message transcription and bilingual soft-rejection detection are competitive advantages that foreign tools cannot match in the Saudi/Gulf market.

## What This Agent Does

1. **Transcribes voice messages** from WhatsApp conversations automatically (Arabic + English)
2. **Generates conversation summaries** synced to CRM after each conversation closes
3. **Extracts commitments and next steps** and writes them to CRM activities
4. **Tracks objections and sentiment** to detect soft rejection (إن شاء الله, بشوف = polite decline patterns)

## Technical Requirements

### Odoo Models

```python
class WhatsAppConversationSummary(models.Model):
    _name = 'era.wa.conversation.summary'
    _description = 'WhatsApp Conversation Summary'

    lead_id = fields.Many2one('crm.lead')
    partner_id = fields.Many2one('res.partner')
    waha_chat_id = fields.Char('Waha Chat ID')
    conversation_date = fields.Datetime()
    message_count = fields.Integer()
    voice_messages_count = fields.Integer()

    # AI-generated fields
    summary_ar = fields.Text('Summary (Arabic)')
    summary_en = fields.Text('Summary (English)')
    commitments = fields.Text('Extracted Commitments (JSON)')
    # [{"who": "customer", "what": "send requirements doc", "by_when": "Sunday"}]
    next_steps = fields.Text('Next Steps (JSON)')
    objections = fields.Text('Objections Detected (JSON)')
    # [{"text": "الميزانية محدودة", "type": "budget", "severity": "medium"}]
    sentiment = fields.Selection([
        ('very_positive', 'Very Positive'),
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
        ('soft_rejection', 'Soft Rejection Detected'),
    ])
    soft_rejection_indicators = fields.Text('Soft Rejection Indicators (JSON)')
    activities_created = fields.One2many('mail.activity', 'res_id')

class WhatsAppVoiceTranscript(models.Model):
    _name = 'era.wa.voice.transcript'
    _description = 'WhatsApp Voice Message Transcript'

    summary_id = fields.Many2one('era.wa.conversation.summary', ondelete='cascade')
    waha_message_id = fields.Char()
    audio_url = fields.Char()
    duration_seconds = fields.Integer()
    language_detected = fields.Char()
    transcript = fields.Text()
    transcribed_at = fields.Datetime()
    model_used = fields.Char()  # e.g., "whisper-large-v3"
```

### Voice Transcription Pipeline

```python
def transcribe_voice_message(self, audio_data):
    """
    1. Download audio from waha
    2. Detect language (Arabic/English)
    3. Transcribe via Whisper Large-v3 or ALLaM
    4. Store transcript linked to conversation
    """
    # Whisper Large-v3 for Arabic transcription
    # ALLaM as alternative for Saudi dialect
    pass
```

### Soft Rejection Detection (Arabic)

```python
SOFT_REJECTION_PATTERNS = {
    'إن شاء الله': {'confidence': 0.6, 'context_needed': True},
    'بشوف': {'confidence': 0.7, 'context_needed': True},
    'نتواصل لاحقاً': {'confidence': 0.5, 'context_needed': True},
    'الموضوع يحتاج وقت': {'confidence': 0.6, 'context_needed': False},
    'ما عندنا ميزانية حالياً': {'confidence': 0.8, 'context_needed': False},
    'خلنا نشوف': {'confidence': 0.65, 'context_needed': True},
    'أرسل لي واتأكد': {'confidence': 0.5, 'context_needed': True},
}
# LLM analyzes full context to confirm if pattern is genuine soft rejection
```

### Commitment Extraction

```python
def extract_commitments(self, conversation_text):
    """
    LLM extracts structured commitments:
    - Who committed (customer or salesperson)
    - What was committed
    - By when (if mentioned)
    - Creates mail.activity for each commitment
    """
    pass
```

### Integration with waha

- Listen to waha webhook events for new messages
- Batch process: summarize after conversation goes idle for 30 minutes
- Download and transcribe voice messages on arrival

### LLM Integration

| Task | Model | Cost |
|------|-------|------|
| Voice transcription | Whisper Large-v3 / ALLaM | ~$0.006/min |
| Conversation summary | GPT-4o-mini / Haiku | ~$0.01/conv |
| Commitment extraction | GPT-4o-mini / Haiku | ~$0.005/conv |
| Soft rejection analysis | Claude / GPT-4o (needs Arabic nuance) | ~$0.02/conv |

### UI Components

1. **Conversation summary panel** on `crm.lead` form
2. **Voice transcripts** expandable under each summary
3. **Commitment badges** with link to created activities
4. **Sentiment indicator** with soft rejection warning
5. **Dashboard:** Daily conversation stats, objection trends

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.wa_intel.idle_minutes` | 30 | Minutes of silence before summarizing |
| `era.wa_intel.transcribe_voice` | `True` | Auto-transcribe voice messages |
| `era.wa_intel.whisper_model` | `whisper-large-v3` | Transcription model |
| `era.wa_intel.detect_soft_rejection` | `True` | Enable soft rejection detection |

## File Structure

```
era_crm_whatsapp_intelligence/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── wa_conversation_summary.py
│   ├── wa_voice_transcript.py
│   ├── wa_intelligence_engine.py    # Summary + extraction
│   ├── wa_voice_transcriber.py      # Whisper/ALLaM integration
│   ├── wa_soft_rejection.py         # Arabic soft rejection detection
│   ├── wa_commitment_extractor.py   # Commitment → activity
│   └── crm_lead_inherit.py
├── controllers/
│   ├── __init__.py
│   └── waha_webhook.py             # Receive waha events
├── data/
│   └── system_parameters.xml
├── views/
│   ├── wa_summary_views.xml
│   ├── crm_lead_views_inherit.xml
│   └── dashboard.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Voice messages transcribed correctly (Arabic + English)
- [ ] Conversation summaries generated after idle period
- [ ] Commitments extracted and activities created
- [ ] Soft rejection patterns detected with context analysis
- [ ] Sentiment classification works
- [ ] Summaries appear on lead form
- [ ] waha webhook integration works
- [ ] Dashboard shows conversation stats

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Voice transcription for Arabic (Whisper/ALLaM) works
- [ ] Summaries in Arabic and English
- [ ] Commitment → activity pipeline functional
- [ ] Soft rejection detection catches at least 5 patterns
- [ ] Arabic translations complete
