# Task 11 — Bilingual Proposal Generator (مولّد العروض ثنائي اللغة)

## Context

**Phase:** 4 | **Priority:** Medium  
**Duration:** 1–2 weeks  
**Dependencies:** None  
**Module name:** `era_crm_proposal_generator`  
**Path:** `submodules/era_share_latest/era_crm_proposal_generator/`

## Business Value

Cuts proposal prep time from hours to minutes while ensuring consistent branding. Bilingual (Arabic RTL + English LTR) proposals with Saudi industry-specific case studies raise acceptance rates significantly.

## What This Agent Does

1. **Generates bilingual proposals** (Arabic RTL / English LTR) from `sale.order` templates
2. **Builds a customized one-pager** per deal with Saudi case studies matched by industry

## Technical Requirements

### Odoo Models

```python
class ProposalTemplate(models.Model):
    _name = 'era.proposal.template'
    _description = 'Proposal Template'

    name = fields.Char(required=True)
    industry_ids = fields.Many2many('res.partner.industry')
    template_ar = fields.Html('Arabic Template (RTL)')
    template_en = fields.Html('English Template (LTR)')
    case_studies = fields.One2many('era.case.study', 'template_id')
    is_active = fields.Boolean(default=True)

class CaseStudy(models.Model):
    _name = 'era.case.study'
    _description = 'Saudi Case Study'

    template_id = fields.Many2one('era.proposal.template')
    title_ar = fields.Char('Title (Arabic)')
    title_en = fields.Char('Title (English)')
    industry_id = fields.Many2one('res.partner.industry')
    content_ar = fields.Html('Content (Arabic)')
    content_en = fields.Html('Content (English)')
    metrics = fields.Text('Key Metrics (JSON)')  # {"roi": "35%", "time_saved": "60%"}
    region = fields.Selection([
        ('riyadh', 'Riyadh'), ('jeddah', 'Jeddah'),
        ('eastern', 'Eastern'), ('national', 'National')
    ])

class GeneratedProposal(models.Model):
    _name = 'era.generated.proposal'
    _description = 'Generated Proposal'

    sale_order_id = fields.Many2one('sale.order', required=True)
    lead_id = fields.Many2one('crm.lead')
    partner_id = fields.Many2one('res.partner')
    template_id = fields.Many2one('era.proposal.template')

    # Generated content
    executive_summary_ar = fields.Html()
    executive_summary_en = fields.Html()
    value_proposition_ar = fields.Html()
    value_proposition_en = fields.Html()
    selected_case_studies = fields.Many2many('era.case.study')
    one_pager_ar = fields.Html()
    one_pager_en = fields.Html()

    # PDF outputs
    proposal_pdf = fields.Binary('Full Proposal PDF')
    proposal_pdf_filename = fields.Char()
    one_pager_pdf = fields.Binary('One-Pager PDF')
    one_pager_pdf_filename = fields.Char()

    status = fields.Selection([
        ('draft', 'Draft'),
        ('review', 'Under Review'),
        ('approved', 'Approved'),
        ('sent', 'Sent to Customer'),
    ], default='draft')
    generated_at = fields.Datetime()
```

### Proposal Generation Pipeline

```python
def generate_proposal(self, sale_order):
    """
    1. Select template by partner industry
    2. Match 2-3 case studies by industry + region
    3. LLM generates:
       - Executive summary (AR + EN) from sale.order lines
       - Value proposition tailored to partner's needs
       - One-pager with key metrics
    4. Merge into bilingual PDF (RTL Arabic pages + LTR English pages)
    5. Send for salesperson review
    """
    pass
```

### Bilingual PDF Layout

```
Page 1 (Arabic RTL):
┌─────────────────────────┐
│     شعار الشركة          │
│  ──────────────────────  │
│  الملخص التنفيذي         │
│  [AI-generated content]  │
│  ──────────────────────  │
│  عرض القيمة              │
│  [AI-generated content]  │
└─────────────────────────┘

Page 2 (English LTR):
┌─────────────────────────┐
│  Company Logo            │
│  ──────────────────────  │
│  Executive Summary       │
│  [AI-generated content]  │
│  ──────────────────────  │
│  Value Proposition       │
│  [AI-generated content]  │
└─────────────────────────┘

Page 3+: Product details from sale.order
Page N: Case Studies (bilingual)
```

### LLM Integration

- One LLM call per proposal for content generation
- **Model:** Advanced model for Arabic quality (Claude / GPT-4o)
- **Prompt:** Includes sale.order data, partner info, industry context, case study references

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.proposal.default_language` | `both` | both/ar/en |
| `era.proposal.llm_model` | `claude-sonnet-4-6` | Model for content generation |
| `era.proposal.auto_case_studies` | `True` | Auto-select case studies |
| `era.proposal.max_case_studies` | 3 | Max case studies to include |

## File Structure

```
era_crm_proposal_generator/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── proposal_template.py
│   ├── case_study.py
│   ├── generated_proposal.py
│   ├── proposal_generator.py      # Generation pipeline
│   └── sale_order_inherit.py      # "Generate Proposal" button
├── report/
│   ├── proposal_template_ar.xml   # Arabic RTL QWeb template
│   ├── proposal_template_en.xml   # English LTR QWeb template
│   ├── one_pager_template.xml
│   └── proposal_report.xml
├── data/
│   ├── system_parameters.xml
│   └── demo_case_studies.xml      # Sample Saudi case studies
├── views/
│   ├── proposal_template_views.xml
│   ├── case_study_views.xml
│   ├── generated_proposal_views.xml
│   └── sale_order_views_inherit.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Proposal generates from sale.order data
- [ ] Arabic RTL layout renders correctly in PDF
- [ ] English LTR layout renders correctly in PDF
- [ ] Case studies matched by industry + region
- [ ] Executive summary is relevant to the deal
- [ ] One-pager contains key metrics
- [ ] Review/approval workflow works
- [ ] "Generate Proposal" button on sale.order works

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Bilingual PDF generation working (RTL + LTR)
- [ ] Case study library manageable
- [ ] LLM-generated content is professional quality
- [ ] Salesperson review flow complete
- [ ] Arabic translations complete
