# Task 12 — Live Compose Quality Score (مؤشر جودة الكتابة الحي)

## Context

**Phase:** 4 | **Priority:** Medium  
**Duration:** 1–2 weeks  
**Dependencies:** None  
**Module name:** `era_crm_compose_quality`  
**Path:** `submodules/era_share_latest/era_crm_compose_quality/`

## Business Value

Raises quality of every message leaving the team. High-quality messages get dramatically better response rates. Arabic cultural guardrails prevent embarrassing mistakes (wrong dialect, missing formal greetings, inappropriate tone). Acts as a real-time writing coach.

## What This Agent Does

1. **Scores 0-100** on email/WhatsApp messages as the salesperson writes them
2. **Shows sub-indicators:** Arabic etiquette, appropriate formality, clear CTA, leak detection

## Technical Requirements

### Quality Dimensions

| Dimension | Weight | What It Checks |
|-----------|--------|---------------|
| **Arabic Etiquette** | 25% | Proper greeting (السلام عليكم), titles (شيخ/أستاذ), closing |
| **Formality Match** | 20% | Tone matches relationship stage (new=formal, existing=warm) |
| **Clear CTA** | 20% | Has a single, clear call-to-action |
| **Grammar & Spelling** | 15% | Arabic language correctness |
| **Leak Detection** | 10% | No pricing leaks, no internal info exposed |
| **Length Appropriateness** | 10% | Not too long, not too short for context |

### Odoo Models

```python
class ComposeQualityScore(models.Model):
    _name = 'era.compose.quality'
    _description = 'Message Quality Score'

    message_type = fields.Selection([
        ('email', 'Email'), ('whatsapp', 'WhatsApp'),
    ])
    lead_id = fields.Many2one('crm.lead')
    partner_id = fields.Many2one('res.partner')
    author_id = fields.Many2one('res.users')

    # Scores
    total_score = fields.Float('Total Score (0-100)')
    etiquette_score = fields.Float('Arabic Etiquette (0-100)')
    formality_score = fields.Float('Formality Match (0-100)')
    cta_score = fields.Float('CTA Clarity (0-100)')
    grammar_score = fields.Float('Grammar (0-100)')
    leak_score = fields.Float('Leak Detection (0-100)')
    length_score = fields.Float('Length (0-100)')

    # Feedback
    suggestions = fields.Text('Improvement Suggestions (JSON)')
    # [{"dimension": "etiquette", "issue": "Missing greeting", "fix": "أضف السلام عليكم"}]
    leak_warnings = fields.Text('Leak Warnings (JSON)')

    scored_at = fields.Datetime()
```

### Arabic Etiquette Rules

```python
ETIQUETTE_RULES = {
    'greeting_required': {
        'patterns': ['السلام عليكم', 'أهلاً', 'مرحباً', 'صباح الخير'],
        'required_for': ['new_contact', 'first_message_of_day'],
        'penalty': -15,
    },
    'title_required': {
        'titles': ['أستاذ', 'أستاذة', 'شيخ', 'دكتور', 'مهندس'],
        'required_for': ['new_contact', 'senior_contact'],
        'penalty': -10,
    },
    'closing_required': {
        'patterns': ['تحياتي', 'مع التحية', 'بارك الله فيك', 'شكراً'],
        'required_for': ['email'],
        'penalty': -10,
    },
    'prayer_reference': {
        'positive_patterns': ['بإذن الله', 'إن شاء الله'],
        'bonus': +5,
    },
}
```

### Leak Detection

```python
LEAK_PATTERNS = {
    'internal_pricing': r'(هامش|margin|cost price|سعر التكلفة)',
    'competitor_intel': r'(عرض المنافس|competitor offer)',
    'internal_discussion': r'(قال المدير|discussed internally|اجتماعنا)',
    'discount_authority': r'(أقصى خصم|max discount|صلاحية)',
}
```

### Real-Time Scoring (Debounced)

```javascript
// Frontend: score after 2 seconds of no typing
class ComposeQualityWidget extends Component {
    setup() {
        this.debounceTimer = null;
    }

    onCompose(text) {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => {
            this.scoreMessage(text);
        }, 2000);  // 2-second debounce
    }

    async scoreMessage(text) {
        const result = await this.rpc('/era/compose/score', {
            text: text,
            context: this.getMessageContext(),
        });
        this.updateScoreDisplay(result);
    }
}
```

### LLM Integration

- **Batched calls:** Score after 2-second idle (debounced), not per keystroke
- **Model:** Cheap model (GPT-4o-mini / Haiku) — structured scoring task
- **Prompt:** "Score this Arabic business message on these 6 dimensions. Return JSON scores + suggestions."

### UI Components

1. **Score gauge** (0-100) next to compose area — color coded (green/yellow/red)
2. **Sub-dimension breakdown** in expandable panel
3. **Inline suggestions:** "أضف تحية في البداية" highlighted in message
4. **Leak warning:** Red alert if internal info detected
5. **Historical:** Average quality score per salesperson over time

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.compose.min_score_to_send` | 0 | Min score to allow send (0=advisory only) |
| `era.compose.debounce_ms` | 2000 | Debounce interval |
| `era.compose.llm_model` | `gpt-4o-mini` | Model for scoring |
| `era.compose.check_leaks` | `True` | Enable leak detection |

## File Structure

```
era_crm_compose_quality/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── compose_quality.py
│   ├── compose_scorer.py          # Scoring engine
│   ├── etiquette_rules.py         # Arabic etiquette checker
│   └── leak_detector.py           # Internal info leak detection
├── controllers/
│   ├── __init__.py
│   └── score_api.py               # /era/compose/score endpoint
├── data/
│   └── system_parameters.xml
├── views/
│   └── compose_quality_views.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── js/
        │   └── compose_quality_widget.js
        ├── xml/
        │   └── compose_quality_widget.xml
        └── css/
            └── compose_quality.css
```

## Testing Checklist

- [ ] Score computed correctly across 6 dimensions
- [ ] Arabic etiquette rules detect missing greetings/titles
- [ ] Leak detection catches internal pricing info
- [ ] Real-time scoring with debounce works
- [ ] Score gauge displays next to compose area
- [ ] Suggestions are actionable and in Arabic
- [ ] Performance: scoring < 1 second response time

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] 6 quality dimensions scored correctly
- [ ] Arabic etiquette rules functional
- [ ] Leak detection prevents info leakage
- [ ] Real-time UI widget integrated in compose
- [ ] Arabic translations complete
