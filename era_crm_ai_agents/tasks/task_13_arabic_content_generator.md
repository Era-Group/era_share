# Task 13 — Arabic Content Generator (مولّد المحتوى العربي)

## Context

**Phase:** 4 | **Priority:** Medium  
**Duration:** 1–2 weeks  
**Dependencies:** None  
**Module name:** `era_crm_arabic_content`  
**Path:** `submodules/era_share_latest/era_crm_arabic_content/`

## Business Value

Reusable across multiple agents. Arabic content in the right dialect (formal Fusha for proposals, Najdi/Hijazi for SME outreach) dramatically improves engagement. The RFP auto-responder alone saves massive time on government tenders — a major revenue stream in Saudi Arabia.

## What This Agent Does

1. **Generates authentic Arabic content** in configurable dialect (Fusha formal / Najdi / Hijazi for SME)
2. **Auto-responds to RFPs** from a library of previous answers + AI synthesis

## Technical Requirements

### Odoo Models

```python
class ArabicContentRequest(models.Model):
    _name = 'era.arabic.content'
    _description = 'Arabic Content Generation Request'

    content_type = fields.Selection([
        ('email', 'Sales Email'),
        ('whatsapp', 'WhatsApp Message'),
        ('proposal_section', 'Proposal Section'),
        ('rfp_response', 'RFP Response'),
        ('social_post', 'Social Media Post'),
        ('product_description', 'Product Description'),
    ], required=True)
    dialect = fields.Selection([
        ('fusha', 'فصحى — Formal Standard Arabic'),
        ('najdi', 'نجدي — Najdi Dialect'),
        ('hijazi', 'حجازي — Hijazi Dialect'),
        ('gulf', 'خليجي — General Gulf'),
    ], default='fusha')
    tone = fields.Selection([
        ('formal', 'Formal / رسمي'),
        ('professional', 'Professional / مهني'),
        ('friendly', 'Friendly / ودي'),
        ('casual', 'Casual / عامي'),
    ], default='professional')
    context = fields.Text('Context / Brief')
    target_audience = fields.Char()
    keywords = fields.Text('Keywords to Include')

    # Output
    generated_content = fields.Html('Generated Content')
    alternative_versions = fields.Text('Alternative Versions (JSON)')
    quality_score = fields.Float('Quality Score')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('generated', 'Generated'),
        ('approved', 'Approved'),
        ('used', 'Used'),
    ], default='draft')

class RFPResponseLibrary(models.Model):
    _name = 'era.rfp.library'
    _description = 'RFP Response Library'

    question_category = fields.Selection([
        ('company_profile', 'Company Profile'),
        ('technical', 'Technical Capabilities'),
        ('implementation', 'Implementation Approach'),
        ('support', 'Support & SLA'),
        ('pricing', 'Pricing Model'),
        ('references', 'References & Case Studies'),
        ('compliance', 'Compliance & Certifications'),
        ('team', 'Team & Qualifications'),
    ])
    question_pattern = fields.Text('Question Pattern')
    answer_ar = fields.Html('Standard Answer (Arabic)')
    answer_en = fields.Html('Standard Answer (English)')
    last_used = fields.Date()
    use_count = fields.Integer()
    tags = fields.Many2many('era.rfp.tag')
```

### Content Generation Pipeline

```python
def generate_content(self, request):
    """
    1. Select appropriate LLM based on dialect:
       - Fusha: Claude / GPT-4o (best formal Arabic)
       - Najdi/Hijazi: ALLaM (Saudi dialect specialist)
    2. Build prompt with dialect instructions + context
    3. Generate 2-3 alternative versions
    4. Score each version for quality
    5. Return best version as primary, others as alternatives
    """
    pass
```

### RFP Auto-Responder

```python
def respond_to_rfp(self, rfp_document):
    """
    1. Parse RFP document (PDF/Word) into questions
    2. Match each question to library entries (semantic search)
    3. For matched questions: use library answer as base, customize with AI
    4. For unmatched questions: generate fresh answer with AI
    5. Compile into formatted response document
    """
    pass
```

### Dialect-Aware Prompts

```python
DIALECT_PROMPTS = {
    'fusha': """
        اكتب بالعربية الفصحى الرسمية. استخدم أسلوب الخطابات الرسمية
        والمراسلات التجارية. تجنب العامية تماماً.
    """,
    'najdi': """
        اكتب بلهجة نجدية طبيعية مناسبة لبيئة الأعمال.
        استخدم تعابير مثل: وش رايك، إيه والله، ما قصرت.
        حافظ على المهنية مع الود.
    """,
    'hijazi': """
        اكتب بلهجة حجازية طبيعية مناسبة لبيئة الأعمال.
        استخدم تعابير مثل: كيفك، يا هلا، الله يعطيك العافية.
        حافظ على المهنية مع الود.
    """,
}
```

### LLM Integration

| Content Type | Dialect | Recommended Model |
|-------------|---------|-------------------|
| Formal emails/proposals | Fusha | Claude / GPT-4o |
| WhatsApp messages | Najdi/Hijazi | ALLaM / Claude |
| RFP responses | Fusha | Claude / GPT-4o |
| Product descriptions | Fusha | GPT-4o-mini (template-based) |

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.content.default_dialect` | `fusha` | Default Arabic dialect |
| `era.content.fusha_model` | `claude-sonnet-4-6` | Model for formal Arabic |
| `era.content.dialect_model` | `allam` | Model for Saudi dialects |
| `era.content.alternatives_count` | 3 | Number of alternative versions |
| `era.content.rfp_similarity_threshold` | 0.7 | Min similarity for library match |

## File Structure

```
era_crm_arabic_content/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── arabic_content.py
│   ├── content_generator.py        # Main generation pipeline
│   ├── rfp_library.py
│   ├── rfp_responder.py            # RFP auto-response
│   └── dialect_engine.py           # Dialect-aware generation
├── data/
│   ├── system_parameters.xml
│   └── rfp_library_demo.xml        # Sample library entries
├── views/
│   ├── arabic_content_views.xml
│   ├── rfp_library_views.xml
│   └── dashboard.xml
├── wizard/
│   ├── __init__.py
│   ├── content_wizard.py           # Quick generate wizard
│   └── rfp_import_wizard.py        # Import RFP document
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Content generates in Fusha correctly
- [ ] Najdi dialect output sounds natural
- [ ] Hijazi dialect output sounds natural
- [ ] Multiple alternative versions produced
- [ ] RFP library matches questions correctly
- [ ] RFP responses combine library + AI generation
- [ ] Quality scoring differentiates good/bad output
- [ ] Content wizard works for quick generation

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] 3 Arabic dialects supported (Fusha, Najdi, Hijazi)
- [ ] RFP auto-responder with library matching
- [ ] Multiple LLM routing based on dialect
- [ ] Content reusable by other agents
- [ ] Arabic translations complete
