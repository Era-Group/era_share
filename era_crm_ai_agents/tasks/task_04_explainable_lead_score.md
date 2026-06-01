# Task 04 — Explainable Lead Score (وكيل التقييم التنبؤي القابل للتفسير)

## Context

**Phase:** 2 | **Priority:** High  
**Duration:** ~1 week (includes model training)  
**Dependencies:** None (uses historical Odoo data)  
**Module name:** `era_crm_lead_score`  
**Path:** `submodules/era_share_latest/era_crm_lead_score/`

## Business Value

Replaces gut-feel prioritization with a trained model that learns from your own win/loss data. The key differentiator: **explainability** — salespeople see the top 3-5 signals that raised the score, so they trust it and act on it. Scores decay over time as signals age.

## What This Agent Does

1. **Scores all active leads** using a model trained on historical won/lost data from Odoo
2. **Separates Fit (how well they match)** from **Intent (how ready they are to buy)**
3. **Shows top 3-5 signals** that contributed most to the score
4. **Decays signal weights** over time (a website visit 30 days ago is worth less than yesterday's)
5. **Optional LLM explanation** in Arabic for complex cases

## Technical Requirements

### Odoo Models

```python
# New model: era.lead.score
class LeadScore(models.Model):
    _name = 'era.lead.score'
    _description = 'AI Lead Score'

    lead_id = fields.Many2one('crm.lead', required=True, ondelete='cascade')
    total_score = fields.Float('Total Score (0-100)', digits=(5, 2))
    fit_score = fields.Float('Fit Score (0-100)', digits=(5, 2))
    intent_score = fields.Float('Intent Score (0-100)', digits=(5, 2))
    grade = fields.Selection([
        ('A', 'A — Hot'), ('B', 'B — Warm'),
        ('C', 'C — Cool'), ('D', 'D — Cold'),
    ], compute='_compute_grade', store=True)
    top_signals = fields.Text('Top Signals (JSON)')
    # [{"signal": "Company size > 50", "weight": 25, "category": "fit", "age_days": 0}]
    explanation_ar = fields.Text('Arabic Explanation')
    scored_at = fields.Datetime('Last Scored')
    model_version = fields.Char('Model Version')

    @api.depends('total_score')
    def _compute_grade(self):
        for rec in self:
            if rec.total_score >= 80: rec.grade = 'A'
            elif rec.total_score >= 60: rec.grade = 'B'
            elif rec.total_score >= 40: rec.grade = 'C'
            else: rec.grade = 'D'
```

### Scoring Features (Fit)

| Feature | Source | Weight Range |
|---------|--------|-------------|
| Company size | `res.partner` / enrichment | 0-15 |
| Industry match | `crm.lead.industry` vs won deals | 0-15 |
| Geography match | Region vs won deal distribution | 0-10 |
| Budget indicated | `expected_revenue` on lead | 0-15 |
| Job title match | Contact role vs decision-maker patterns | 0-10 |
| Existing customer | Has previous `sale.order` | 0-10 |

### Scoring Features (Intent)

| Feature | Source | Weight Range | Decay |
|---------|--------|-------------|-------|
| Recent website visit | Website tracking | 0-15 | 50%/30 days |
| Email opened/clicked | Marketing data | 0-10 | 50%/14 days |
| Responded to outreach | `mail.message` | 0-20 | 50%/7 days |
| Requested demo/quote | Activity log | 0-25 | 50%/14 days |
| Multiple contacts engaged | Contact count | 0-10 | None |
| Frequency of interaction | Activity frequency | 0-10 | 50%/30 days |

### Model Training

```python
class LeadScoreTrainer:
    """
    Train scoring model on historical won/lost data.
    Uses simple gradient boosting — runs locally, no external API needed.
    Retrain monthly or when 50+ new outcomes recorded.
    """
    def train(self):
        # 1. Extract features from won/lost leads (last 12 months)
        # 2. Train sklearn GradientBoostingClassifier
        # 3. Extract feature importances for explainability
        # 4. Save model + version to system storage
        # 5. Score all active leads with new model
        pass
```

### LLM Explanation (Optional)

- Called only when salesperson requests explanation on a specific lead
- Uses cheap model to generate 2-3 sentence Arabic explanation
- Example: "هذا العميل حصل على درجة 78 لأن: شركة كبيرة في قطاع مطابق (15 نقطة)، طلب عرض سعر هذا الأسبوع (25 نقطة)، عميل حالي بمشتريات سابقة (10 نقاط)"

### Cron Job

- **Name:** `Lead Score — Daily Refresh`
- **Interval:** Daily (early morning AST)
- **Action:** Re-score all active leads, apply decay to intent signals

### UI Components

1. **Score badge** on `crm.lead` form and Kanban card (color-coded A/B/C/D)
2. **Expandable signal panel** showing top 5 signals with weights
3. **"Explain" button** → triggers LLM Arabic explanation
4. **Pipeline view sort** by score (highest first)
5. **Score history chart** (sparkline showing score over time)

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.score.retrain_threshold` | 50 | New outcomes before retrain |
| `era.score.decay_enabled` | `True` | Enable signal decay |
| `era.score.grade_a_threshold` | 80 | Min score for grade A |
| `era.score.grade_b_threshold` | 60 | Min score for grade B |
| `era.score.grade_c_threshold` | 40 | Min score for grade C |

## Security & Compliance

- Scoring is internal — no external data sent (except optional LLM explanation)
- Model trained on internal data only
- Scores visible to lead owner + CRM managers
- Model version tracked for auditability

## File Structure

```
era_crm_lead_score/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── lead_score.py              # Score model
│   ├── lead_score_engine.py       # Scoring logic + feature extraction
│   ├── lead_score_trainer.py      # Model training
│   ├── lead_score_decay.py        # Signal decay logic
│   └── crm_lead_inherit.py        # Score display on lead
├── data/
│   ├── cron.xml
│   └── system_parameters.xml
├── views/
│   ├── lead_score_views.xml
│   └── crm_lead_views_inherit.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── js/
        │   └── score_widget.js    # Score badge + signal panel
        └── xml/
            └── score_widget.xml
```

## Testing Checklist

- [ ] Model trains on historical won/lost data
- [ ] Scoring produces sensible 0-100 scores
- [ ] Fit and Intent scores separate correctly
- [ ] Top signals extracted and displayed
- [ ] Signal decay reduces old signals over time
- [ ] Grade (A/B/C/D) computed correctly
- [ ] LLM explanation generates proper Arabic
- [ ] Daily cron re-scores without errors
- [ ] Score badge visible on Kanban and form

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Model trained on at least 100 historical outcomes
- [ ] Scores are explainable (top 5 signals visible)
- [ ] Signal decay working
- [ ] UI badges and panels functional
- [ ] Arabic explanations culturally appropriate
- [ ] Agent #5 can consume scores via API
