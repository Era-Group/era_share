# Task 05 — Daily Prioritized Action List (قائمة الإجراءات اليومية المرتبة)

## Context

**Phase:** 3 | **Priority:** Medium-High  
**Duration:** 1–1.5 weeks  
**Dependencies:** Agent #4 (Explainable Lead Score)  
**Module name:** `era_crm_daily_actions`  
**Path:** `submodules/era_share_latest/era_crm_daily_actions/`

## Business Value

Transforms the salesperson's chaotic pipeline into a clear, ranked to-do list. Every morning, each rep opens Odoo and sees exactly what to do first and why (Next-Best-Action). Routes leads to the best-fit salesperson by language, region, and portfolio.

## What This Agent Does

1. **Builds a daily start page** for each salesperson with top 10 AI-ranked actions
2. **Explains each action** with Next-Best-Action reasoning (why this, why now)
3. **Routes new leads** to the best-fit salesperson based on:
   - Language (Arabic/English speaker)
   - Region (Riyadh/Jeddah/Eastern)
   - Current portfolio load

## Technical Requirements

### Odoo Models

```python
class DailyActionItem(models.Model):
    _name = 'era.daily.action'
    _description = 'Daily Prioritized Action'
    _order = 'priority_rank'

    salesperson_id = fields.Many2one('res.users', required=True, index=True)
    date = fields.Date(required=True, default=fields.Date.today)
    priority_rank = fields.Integer('Rank (1=highest)')
    lead_id = fields.Many2one('crm.lead')
    partner_id = fields.Many2one('res.partner')
    action_type = fields.Selection([
        ('follow_up', 'Follow Up'),
        ('call_back', 'Scheduled Call Back'),
        ('proposal', 'Send Proposal'),
        ('close', 'Close Attempt'),
        ('rescue', 'At-Risk Rescue'),
        ('nurture', 'Nurture Touch'),
        ('qualify', 'Qualify New Lead'),
    ])
    reason_ar = fields.Text('Why This Action (Arabic)')
    reason_en = fields.Text('Why This Action (English)')
    estimated_impact = fields.Selection([
        ('high', 'High Impact'), ('medium', 'Medium'), ('low', 'Low')
    ])
    is_completed = fields.Boolean(default=False)
    completed_at = fields.Datetime()
    outcome_notes = fields.Text()

class DailyActionConfig(models.Model):
    _name = 'era.daily.action.config'
    _description = 'Salesperson Routing Config'

    user_id = fields.Many2one('res.users', required=True)
    languages = fields.Many2many('res.lang')
    regions = fields.Many2many('era.region')  # Custom region model
    max_portfolio_size = fields.Integer(default=50)
    current_load = fields.Integer(compute='_compute_load')
```

### Action Ranking Algorithm

```python
def rank_actions(self, salesperson):
    """
    Score each potential action and return top 10.
    Factors: lead score (#4), deal age, last activity, stage velocity,
             scheduled callbacks, at-risk flags (#6 if available).
    """
    candidates = []

    # 1. Overdue activities (highest weight)
    # 2. Scheduled callbacks for today
    # 3. High-score leads with no recent activity
    # 4. At-risk deals (from Agent #6 if available)
    # 5. New unqualified leads assigned
    # 6. Nurture touches for warm leads

    # Use LLM to generate reason for top 10
    return sorted(candidates, key=lambda x: x['score'], reverse=True)[:10]
```

### Lead Routing

```python
def route_lead(self, lead):
    """
    Assign new lead to best-fit salesperson.
    Factors: language match, region match, current load, specialization.
    """
    candidates = self.env['era.daily.action.config'].search([])
    best = max(candidates, key=lambda c: self._route_score(c, lead))
    lead.user_id = best.user_id
```

### Cron Job

- **Name:** `Daily Actions — Morning Build`
- **Interval:** Daily at 7 AM AST
- **Action:** Build action list for each active salesperson

### LLM Integration

- One LLM call per salesperson per day for ranking + reasoning
- **Prompt:** Provide lead data + scores + activity history → get ranked actions with Arabic/English reasons
- **Model:** Cheap model sufficient (GPT-4o-mini / Haiku)

### UI Components

1. **Homepage dashboard widget** — "Your Top 10 Today" with action cards
2. **Each card shows:** Lead name, action type, reason (Arabic), estimated impact
3. **Quick actions:** Mark complete, snooze, open lead
4. **Completion tracking:** Progress bar (X/10 done today)

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.daily.actions_count` | 10 | Actions per salesperson per day |
| `era.daily.build_hour` | 7 | Hour to build list (AST) |
| `era.daily.llm_model` | `gpt-4o-mini` | Model for ranking/reasoning |

## File Structure

```
era_crm_daily_actions/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── daily_action.py
│   ├── daily_action_builder.py    # Ranking algorithm
│   ├── daily_action_config.py     # Routing config
│   ├── lead_router.py             # Lead assignment
│   └── crm_lead_inherit.py
├── data/
│   ├── cron.xml
│   └── system_parameters.xml
├── views/
│   ├── daily_action_views.xml
│   ├── daily_action_config_views.xml
│   └── dashboard_widget.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── js/
        │   └── action_dashboard.js
        └── xml/
            └── action_dashboard.xml
```

## Testing Checklist

- [ ] Cron builds action list for each salesperson at 7 AM
- [ ] Actions ranked sensibly (overdue first, then by score)
- [ ] Arabic and English reasons generated
- [ ] Lead routing assigns to best-fit salesperson
- [ ] Dashboard widget shows top 10 actions
- [ ] Completion tracking works
- [ ] Respects salesperson portfolio limits

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] Daily cron generates actions without errors
- [ ] Consumes lead scores from Agent #4
- [ ] Lead routing by language/region works
- [ ] Dashboard widget functional
- [ ] Arabic translations complete
