# Task 08 — Account Brief Generator (مولّد إحاطة الحساب)

## Context

**Phase:** 3 | **Priority:** Medium  
**Duration:** ~1 week  
**Dependencies:** None  
**Module name:** `era_crm_account_brief`  
**Path:** `submodules/era_share_latest/era_crm_account_brief/`

## Business Value

Salespeople walk into meetings unprepared because researching a client takes too long. This generates a one-page Arabic/English briefing from all CRM data, updated daily for active accounts. Pre-meeting briefs arrive automatically 1 hour before calendar events. Especially valuable for overlapping teams that share accounts.

## What This Agent Does

1. **Generates a one-page summary** (Arabic/English) on `res.partner` — refreshed daily for active accounts
2. **Pre-meeting brief (PDF)** delivered to salesperson 1 hour before any calendar event with a customer

## Technical Requirements

### Odoo Models

```python
class AccountBrief(models.Model):
    _name = 'era.account.brief'
    _description = 'Account Brief'

    partner_id = fields.Many2one('res.partner', required=True)
    brief_type = fields.Selection([
        ('daily', 'Daily Refresh'),
        ('pre_meeting', 'Pre-Meeting Brief'),
    ])
    language = fields.Selection([('ar', 'Arabic'), ('en', 'English'), ('both', 'Both')])

    # Company overview
    company_summary = fields.Text()
    industry = fields.Char()
    size_category = fields.Char()
    key_contacts = fields.Text('Key Contacts (JSON)')

    # Relationship history
    total_revenue = fields.Float()
    active_deals = fields.Text('Active Deals (JSON)')
    recent_activities = fields.Text('Last 10 Activities (JSON)')
    open_tickets = fields.Integer()
    satisfaction_signals = fields.Text()

    # AI insights
    talking_points = fields.Text('Suggested Talking Points (Arabic)')
    risks = fields.Text('Relationship Risks')
    upsell_opportunities = fields.Text('Upsell Opportunities')

    # PDF
    pdf_file = fields.Binary('Brief PDF')
    pdf_filename = fields.Char()
    generated_at = fields.Datetime()
```

### Brief Content Structure

```markdown
# إحاطة حساب: [اسم الشركة]
التاريخ: [date] | أعده: النظام الذكي

## نظرة عامة
- القطاع: [industry]
- الحجم: [size]
- العلاقة منذ: [first_order_date]
- إجمالي الإيرادات: [total_revenue] ريال

## جهات الاتصال الرئيسية
| الاسم | المنصب | آخر تواصل | ملاحظات |

## الصفقات النشطة
| الفرصة | المرحلة | القيمة | الحالة الصحية |

## آخر 5 أنشطة
[timeline]

## نقاط النقاش المقترحة
1. [talking_point_1]
2. [talking_point_2]
3. [talking_point_3]

## مخاطر العلاقة
- [risk_1]

## فرص البيع الإضافي
- [upsell_1]
```

### Pre-Meeting Brief

- **Trigger:** `calendar.event` with `partner_id` linked, starts in 1 hour
- **Action:** Generate brief PDF → send to event attendee (salesperson) via internal notification
- **Cron:** Runs every 30 minutes, checks upcoming meetings

### LLM Integration

- One LLM call per brief to synthesize talking points, risks, and upsell opportunities
- **Model:** Cheap model (GPT-4o-mini / Haiku) — data is structured, just needs synthesis
- **Prompt:** "Given this account data [structured JSON], generate: 3 talking points in Arabic, relationship risks, upsell opportunities"

### PDF Generation

- Use `reportlab` or Odoo QWeb reports for bilingual PDF (RTL Arabic support)
- Company branding (logo, colors) from `res.company`

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.brief.pre_meeting_minutes` | 60 | Minutes before meeting to send brief |
| `era.brief.llm_model` | `gpt-4o-mini` | Model for synthesis |
| `era.brief.language` | `both` | Default language |

## File Structure

```
era_crm_account_brief/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── account_brief.py
│   ├── account_brief_generator.py  # Data collection + LLM synthesis
│   ├── calendar_event_inherit.py   # Pre-meeting trigger
│   └── res_partner_inherit.py      # Brief button
├── report/
│   ├── account_brief_template.xml  # QWeb PDF template (RTL)
│   └── account_brief_report.xml
├── data/
│   ├── cron.xml                    # Pre-meeting check cron
│   └── system_parameters.xml
├── views/
│   ├── account_brief_views.xml
│   └── res_partner_views_inherit.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Brief generates correctly from partner data
- [ ] Talking points are relevant and in Arabic
- [ ] PDF renders with proper RTL Arabic layout
- [ ] Pre-meeting brief triggers 1 hour before calendar events
- [ ] Brief button works on partner form
- [ ] LLM synthesis produces useful insights
- [ ] Company branding appears in PDF

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] One-page brief with all sections populated
- [ ] Arabic RTL PDF generation works
- [ ] Pre-meeting auto-delivery functional
- [ ] Arabic translations complete
