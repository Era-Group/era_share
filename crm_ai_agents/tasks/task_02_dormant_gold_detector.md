# Task 02 — Dormant Gold Detector (كاشف الكنوز النائمة)

## Context

**Phase:** 2 | **Priority:** Highest — Foundation of strategy  
**Duration:** ~1 week  
**Dependencies:** Agent #3 (Waterfall Enrichment)  
**Module name:** `era_crm_dormant_gold`  
**Path:** `submodules/era_share_latest/era_crm_dormant_gold/`

## Business Value

Existing customer records (`res.partner`) are a paid-for asset with zero acquisition cost. Activating even a small percentage yields massive ROI. This agent finds customers with purchasing capacity + new activity signals who haven't been contacted recently.

## What This Agent Does

1. **Analyzes existing customer records** in `res.partner` to find who has:
   - Sufficient balance/purchasing capacity (from invoicing/payment history)
   - New activity signals (website visits, email opens, support tickets, new contacts added)
   - No recent salesperson contact (configurable dormancy threshold)
2. **Re-enriches stale records** via Waterfall Enrichment (Agent #3) — LinkedIn, Wathiq, Maroof
3. **Promotes qualified customers** to new CRM opportunities with:
   - Recommended salesperson (based on language, region, portfolio)
   - Reason for nomination (which signals triggered)

## Technical Requirements

### Odoo Models

```python
# New model: era.dormant.gold.result
class DormantGoldResult(models.Model):
    _name = 'era.dormant.gold.result'
    _description = 'Dormant Gold Detection Result'

    partner_id = fields.Many2one('res.partner', required=True)
    signals = fields.Text('Detected Signals (JSON)')  # [{type, detail, score}]
    total_score = fields.Float('Gold Score', digits=(4, 2))
    purchasing_capacity = fields.Selection([
        ('high', 'High'), ('medium', 'Medium'), ('low', 'Low')
    ])
    last_contact_date = fields.Date('Last Contact')
    dormancy_days = fields.Integer('Days Dormant')
    enrichment_status = fields.Selection([
        ('pending', 'Pending'), ('enriched', 'Enriched'), ('failed', 'Failed')
    ])
    recommended_salesperson_id = fields.Many2one('res.users')
    nomination_reason = fields.Text('Nomination Reason (Arabic)')
    lead_id = fields.Many2one('crm.lead', 'Created Opportunity')
    status = fields.Selection([
        ('detected', 'Detected'),
        ('enriching', 'Enriching'),
        ('nominated', 'Nominated'),
        ('accepted', 'Accepted'),
        ('dismissed', 'Dismissed'),
    ], default='detected')
```

### Scoring Logic

```python
SIGNAL_WEIGHTS = {
    'recent_invoice_paid': 30,       # Paid invoice in last 6 months
    'website_visit': 15,             # Visited website in last 30 days
    'email_opened': 10,              # Opened marketing email recently
    'support_ticket': 20,            # Filed support ticket (active user)
    'new_contact_added': 10,         # New contact person on partner
    'company_growth': 25,            # Enrichment shows growth signals
    'competitor_mention': 20,        # Mentioned competitor in tickets/emails
    'budget_cycle': 15,              # Approaching fiscal year start
}
# Threshold: score >= 50 → nominate for review
```

### Cron Job

- **Name:** `Dormant Gold Detector — Weekly Scan`
- **Interval:** Weekly (Sunday night AST)
- **Action:** Scan `res.partner` where `customer_rank > 0`, no `crm.lead` in active pipeline, last activity older than threshold

### LLM Integration

- **Model:** Cheap model for analysis/classification
- **Use cases:**
  - Classify purchasing capacity from invoice patterns
  - Generate Arabic nomination reason for salesperson
  - Suggest best salesperson match based on language/region
- **Pay-on-success:** LLM called only when enrichment succeeds (Waterfall model)

### Integration with Agent #3

- Call Enrichment Engine to refresh stale partner data before scoring
- If enrichment reveals growth signals → bonus score
- Enrichment cost tracked per-partner

### UI Components

1. **List + Form views** for `era.dormant.gold.result`
2. **Smart button** on `res.partner` form: "Gold Score: XX"
3. **Action:** "Promote to Opportunity" → creates `crm.lead` linked to result
4. **Dashboard:** Weekly gold mining stats

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.dormant.min_dormancy_days` | 60 | Min days without contact |
| `era.dormant.score_threshold` | 50 | Min score to nominate |
| `era.dormant.weekly_limit` | 500 | Max partners to scan per week |
| `era.dormant.auto_enrich` | `True` | Auto-trigger enrichment |

## Security & Compliance

- Operates under CRM manager permissions
- No external messages sent — internal nomination only (no Compliance gate needed)
- All nominations logged (Rule 20)
- Enrichment costs tracked and capped (Rule 14)

## File Structure

```
era_crm_dormant_gold/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── dormant_gold_result.py
│   ├── dormant_gold_scanner.py    # Scanning logic
│   ├── dormant_gold_scoring.py    # Signal detection + scoring
│   └── res_partner_inherit.py     # Smart button on partner
├── data/
│   ├── cron.xml
│   └── system_parameters.xml
├── views/
│   ├── dormant_gold_views.xml
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

- [ ] Weekly scan identifies dormant partners correctly
- [ ] Scoring weights produce sensible rankings
- [ ] Enrichment triggers via Agent #3 and updates score
- [ ] Nomination creates CRM opportunity with correct salesperson
- [ ] Salesperson can accept/dismiss nominations
- [ ] Smart button on partner shows gold score
- [ ] Cost tracking for enrichment calls works
- [ ] Permissions: CRM managers only

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Weekly cron runs without errors
- [ ] At least 5 signal types scored
- [ ] Enrichment integration with Agent #3 works
- [ ] Nomination → Opportunity flow complete
- [ ] Dashboard shows weekly stats
- [ ] Arabic translations complete
