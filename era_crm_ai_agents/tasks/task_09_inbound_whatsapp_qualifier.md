# Task 09 — Inbound WhatsApp Qualifier (بوت تأهيل واتساب الوارد)

## Context

**Phase:** 2 | **Priority:** High  
**Duration:** ~1 week  
**Dependencies:** Agent #15 (Compliance Guardrail)  
**Module name:** `era_crm_wa_qualifier`  
**Path:** `submodules/era_share_latest/era_crm_wa_qualifier/`

## Business Value

WhatsApp is the dominant channel (90%+ in Gulf). This is the critical entry point. Inbound leads are captured and qualified 24/7 without waiting for salesperson availability. Automatic BANT qualification + intent classification + routing to the right rep.

## What This Agent Does

1. **Arabic chatbot running 24/7** that qualifies inbound WhatsApp contacts automatically (BANT)
2. **Classifies incoming messages** by intent: Sales / Support / Inquiry / Pricing
3. **Routes each qualified lead** to the best-fit salesperson

## Technical Requirements

### Odoo Models

```python
class WAQualificationSession(models.Model):
    _name = 'era.wa.qualification'
    _description = 'WhatsApp Qualification Session'

    waha_chat_id = fields.Char(required=True)
    phone_number = fields.Char()
    partner_id = fields.Many2one('res.partner')
    lead_id = fields.Many2one('crm.lead')

    # Classification
    intent = fields.Selection([
        ('sales', 'Sales Inquiry'),
        ('support', 'Support Request'),
        ('pricing', 'Pricing Request'),
        ('general', 'General Inquiry'),
        ('spam', 'Spam/Irrelevant'),
    ])

    # BANT Qualification
    budget = fields.Selection([
        ('confirmed', 'Budget Confirmed'),
        ('exploring', 'Exploring Options'),
        ('unknown', 'Not Discussed'),
    ])
    authority = fields.Selection([
        ('decision_maker', 'Decision Maker'),
        ('influencer', 'Influencer'),
        ('user', 'End User'),
        ('unknown', 'Unknown'),
    ])
    need = fields.Text('Stated Need')
    timeline = fields.Selection([
        ('immediate', 'Immediate (< 1 month)'),
        ('short', 'Short Term (1-3 months)'),
        ('medium', 'Medium Term (3-6 months)'),
        ('long', 'Long Term (6+ months)'),
        ('unknown', 'Unknown'),
    ])
    qualification_score = fields.Float('BANT Score (0-100)')

    # Routing
    assigned_salesperson_id = fields.Many2one('res.users')
    routing_reason = fields.Text()

    # Session
    status = fields.Selection([
        ('active', 'Active Conversation'),
        ('qualified', 'Qualified'),
        ('handed_off', 'Handed Off to Rep'),
        ('closed', 'Closed'),
    ], default='active')
    conversation_log = fields.Text('Full Conversation (JSON)')
    messages_exchanged = fields.Integer()
    started_at = fields.Datetime()
    qualified_at = fields.Datetime()
    handed_off_at = fields.Datetime()
```

### Bot Conversation Flow

```
1. GREETING (Arabic)
   "أهلاً وسهلاً! 👋 أنا المساعد الذكي لـ [Company].
    كيف أقدر أساعدك اليوم؟"

2. INTENT CLASSIFICATION
   - Analyze first response to classify: Sales / Support / Pricing / General
   - If Support → route to support team immediately
   - If Sales/Pricing → continue BANT qualification

3. BANT QUALIFICATION (conversational, not interrogation)
   - Need: "ممتاز! ممكن تعطيني فكرة عن اللي تحتاجونه؟"
   - Authority: "هل حضرتك المسؤول عن القرار في هذا الموضوع؟"
   - Timeline: "متى تتطلعون تبدؤون تقريباً؟"
   - Budget: (inferred from context, not asked directly in Saudi culture)

4. HAND-OFF
   "ممتاز! رح أحولك الحين على [Rep Name] اللي بيقدر يساعدك أكثر.
    [Rep Name] بيتواصل معك خلال [timeframe]."
```

### Multi-LLM Routing

```python
class QualifierLLMRouter:
    """
    - Common questions (greetings, FAQ): GPT-4o-mini / Haiku (cheap)
    - BANT analysis + Arabic nuance: Claude / GPT-4o (advanced)
    - Intent classification: GPT-4o-mini (cheap, structured output)
    """
    def route_to_model(self, message_type):
        if message_type in ('greeting', 'faq', 'classification'):
            return 'gpt-4o-mini'  # ~$0.00015/message
        elif message_type in ('bant_analysis', 'arabic_response'):
            return 'claude-sonnet-4-6'  # ~$0.003/message
```

### Integration with waha

- Receive inbound messages via waha webhook
- Send bot responses via waha API
- Track conversation state per chat ID
- Handle media messages (images, voice → transcribe if needed)

### Lead Creation & Routing

```python
def qualify_and_route(self, session):
    """
    1. Create crm.lead from qualification data
    2. Route to best salesperson (language, region, load)
    3. Notify salesperson with context summary
    4. Hand off conversation in waha
    """
    lead = self.env['crm.lead'].create({
        'name': f"WA Inbound: {session.need[:50]}",
        'partner_id': session.partner_id.id,
        'phone': session.phone_number,
        'description': session.need,
        'source_id': whatsapp_source.id,
    })
    # Route using Agent #5 routing logic if available
    pass
```

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.wa_qual.greeting_ar` | (template) | Arabic greeting message |
| `era.wa_qual.max_messages` | 10 | Max messages before forced hand-off |
| `era.wa_qual.idle_timeout_minutes` | 15 | Idle timeout |
| `era.wa_qual.cheap_model` | `gpt-4o-mini` | Model for simple tasks |
| `era.wa_qual.advanced_model` | `claude-sonnet-4-6` | Model for BANT/Arabic |
| `era.wa_qual.handoff_message_ar` | (template) | Hand-off message |

## Security & Compliance

- All conversations pass through Agent #15 Compliance before responding
- PDPL consent collection as part of greeting flow
- No marketing messages — qualification only
- Conversation logs stored with retention policy

## File Structure

```
era_crm_wa_qualifier/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── wa_qualification.py
│   ├── wa_qualifier_bot.py        # Conversation state machine
│   ├── wa_intent_classifier.py    # Intent classification
│   ├── wa_bant_analyzer.py        # BANT extraction
│   ├── wa_lead_creator.py         # Lead creation + routing
│   └── wa_llm_router.py           # Multi-LLM routing
├── controllers/
│   ├── __init__.py
│   └── waha_webhook.py            # Inbound message webhook
├── data/
│   ├── system_parameters.xml
│   └── message_templates.xml      # Arabic bot messages
├── views/
│   ├── wa_qualification_views.xml
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

- [ ] Bot responds to inbound WhatsApp messages in Arabic
- [ ] Intent classification sorts messages correctly (4 types)
- [ ] BANT qualification extracts all 4 dimensions
- [ ] Multi-LLM routing sends cheap queries to cheap model
- [ ] Lead created in CRM from qualified conversation
- [ ] Salesperson notified with context
- [ ] Hand-off message sent to customer
- [ ] Compliance check runs before each bot response
- [ ] Idle timeout closes inactive sessions
- [ ] Spam detection filters irrelevant messages

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] 24/7 Arabic chatbot qualification working
- [ ] BANT scoring functional
- [ ] Multi-LLM routing reduces costs
- [ ] Lead creation + routing pipeline complete
- [ ] Compliance gate active
- [ ] Arabic translations complete
