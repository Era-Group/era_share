# Task 06 — Deal-Risk Watchdog (حارس صحة الصفقات)

## Context

**Phase:** 3 | **Priority:** Medium-High  
**Duration:** ~1 week  
**Dependencies:** None (independent, but enriches Agent #5 if available)  
**Module name:** `era_crm_deal_watchdog`  
**Path:** `submodules/era_share_latest/era_crm_deal_watchdog/`

## Business Value

Dozens of active opportunities fly under the radar until they're lost. This agent puts every live deal under continuous AI surveillance — computing a health score (RAG: Red/Amber/Green), comparing AI forecast vs salesperson forecast, and detecting stalling patterns early. Prevents silent deal leakage and improves forecast accuracy.

## What This Agent Does

1. **Computes a health score** for every active opportunity (RAG system) with explanation:
   - Slow pipeline movement, 7-day silence, competitor mention, etc.
2. **Compares AI forecast vs salesperson forecast** to surface optimism/pessimism gaps
3. **Detects stalling deals** and suggests one specific corrective action per deal per day

## Technical Requirements

### Odoo Models

```python
class DealHealthScore(models.Model):
    _name = 'era.deal.health'
    _description = 'Deal Health Score'

    lead_id = fields.Many2one('crm.lead', required=True, ondelete='cascade')
    health_status = fields.Selection([
        ('green', 'Green — Healthy'),
        ('amber', 'Amber — At Risk'),
        ('red', 'Red — Critical'),
    ], compute='_compute_health', store=True)
    health_score = fields.Float('Health Score (0-100)')
    risk_signals = fields.Text('Risk Signals (JSON)')
    # [{"signal": "No activity 7 days", "severity": "high", "points": -20}]
    ai_win_probability = fields.Float('AI Win Probability %')
    rep_win_probability = fields.Float('Rep Win Probability %')
    forecast_gap = fields.Float('Forecast Gap %', compute='_compute_gap')
    suggested_action = fields.Text('Suggested Action (Arabic)')
    scored_at = fields.Datetime()

    @api.depends('health_score')
    def _compute_health(self):
        for rec in self:
            if rec.health_score >= 70:
                rec.health_status = 'green'
            elif rec.health_score >= 40:
                rec.health_status = 'amber'
            else:
                rec.health_status = 'red'

    @api.depends('ai_win_probability', 'rep_win_probability')
    def _compute_gap(self):
        for rec in self:
            rec.forecast_gap = rec.rep_win_probability - rec.ai_win_probability
```

### Risk Signals

| Signal | Detection Logic | Severity | Points |
|--------|----------------|----------|--------|
| No activity 7+ days | Last `mail.message` or `mail.activity` | High | -25 |
| Stuck in stage 2x avg | Stage duration vs avg for stage | High | -20 |
| Single contact only | Only 1 contact on deal | Medium | -10 |
| No next activity scheduled | Missing `mail.activity` | Medium | -15 |
| Competitor mentioned | NLP on messages/notes | High | -20 |
| Budget decreased | `expected_revenue` dropped | Medium | -15 |
| Decision maker not engaged | No activity from key contact | High | -20 |
| Positive: Recent meeting | Calendar event this week | Positive | +15 |
| Positive: Proposal sent | Quotation exists | Positive | +10 |
| Positive: Multiple champions | 3+ active contacts | Positive | +10 |

### AI Win Probability

```python
def compute_ai_probability(self, lead):
    """
    Use historical patterns to predict win probability.
    Features: stage, age, activity frequency, deal size, industry, etc.
    Compare with what the rep has entered as probability.
    """
    # Simple logistic model trained on won/lost deals
    # OR use LLM with structured lead data
    pass
```

### Cron Job

- **Name:** `Deal Watchdog — Daily Health Check`
- **Interval:** Daily at 6 AM AST (before Action List builds at 7 AM)
- **Action:** Score all active opportunities, flag red/amber, update suggestions

### LLM Integration

- LLM generates Arabic suggested action for each at-risk deal
- **Prompt:** "Given this deal status [data], suggest ONE specific action the salesperson should take today."
- Model: Cheap model for signal detection, advanced for Arabic suggestions

### UI Components

1. **Health badge** on `crm.lead` Kanban card (colored dot: 🟢🟡🔴)
2. **Health panel** on lead form: score, signals, AI vs Rep forecast, suggested action
3. **Dashboard:** Pipeline health overview — % green/amber/red, trend chart
4. **Alert notifications:** Red deals trigger internal notification to salesperson + manager

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.watchdog.silence_days` | 7 | Days without activity = risk |
| `era.watchdog.stuck_multiplier` | 2.0 | Stage duration multiplier for stuck |
| `era.watchdog.red_threshold` | 40 | Below this = Red |
| `era.watchdog.amber_threshold` | 70 | Below this = Amber |
| `era.watchdog.notify_red` | `True` | Notify manager on Red deals |

## File Structure

```
era_crm_deal_watchdog/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── deal_health.py
│   ├── deal_health_engine.py      # Scoring + signal detection
│   ├── deal_forecast.py           # AI vs Rep probability
│   └── crm_lead_inherit.py        # Health display
├── data/
│   ├── cron.xml
│   ├── system_parameters.xml
│   └── mail_templates.xml         # Alert notifications
├── views/
│   ├── deal_health_views.xml
│   ├── crm_lead_views_inherit.xml
│   └── dashboard.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── js/
        │   └── health_widget.js
        └── xml/
            └── health_widget.xml
```

## Testing Checklist

- [ ] Health score computes correctly for all active deals
- [ ] Risk signals detected (at least 5 signal types)
- [ ] RAG status (Red/Amber/Green) assigned correctly
- [ ] AI probability differs meaningfully from rep's estimate
- [ ] Arabic suggested actions are specific and actionable
- [ ] Red deals trigger notifications
- [ ] Health badges show on Kanban cards
- [ ] Dashboard shows pipeline health breakdown

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Daily cron scores all active deals
- [ ] At least 8 risk/positive signals detected
- [ ] AI forecast vs rep forecast comparison works
- [ ] Notifications for critical deals
- [ ] Feeds into Agent #5 Daily Action List (if available)
- [ ] Arabic translations complete
