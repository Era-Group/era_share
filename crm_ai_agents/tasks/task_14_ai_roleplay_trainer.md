# Task 14 — AI Roleplay Trainer (تدريب الأدوار بالذكاء الاصطناعي)

## Context

**Phase:** 4 | **Priority:** Low-Medium  
**Duration:** ~3 weeks  
**Dependencies:** None (uses historical data)  
**Module name:** `era_crm_roleplay_trainer`  
**Path:** `submodules/era_share_latest/era_crm_roleplay_trainer/`

## Business Value

Trains salespeople on realistic Saudi objections without relying on a human trainer. AI-powered win/loss analysis generates actionable feedback after every closed deal. Monthly performance scorecards with AI coaching tips help managers develop their teams.

## What This Agent Does

1. **Roleplay training** with realistic Saudi objections via conversational AI
2. **Automated Win/Loss analysis** — generates a debrief report after every closed deal
3. **Monthly performance scorecards** per salesperson with AI coaching comments

## Technical Requirements

### Odoo Models

```python
class RoleplaySession(models.Model):
    _name = 'era.roleplay.session'
    _description = 'AI Roleplay Training Session'

    salesperson_id = fields.Many2one('res.users', required=True)
    scenario_id = fields.Many2one('era.roleplay.scenario')
    conversation = fields.Text('Conversation Log (JSON)')
    # [{"role": "customer", "text": "..."}, {"role": "salesperson", "text": "..."}]
    duration_minutes = fields.Float()
    score = fields.Float('Performance Score (0-100)')
    feedback = fields.Text('AI Feedback (Arabic)')
    strengths = fields.Text('Strengths Identified (JSON)')
    areas_to_improve = fields.Text('Areas to Improve (JSON)')
    objections_handled = fields.Integer()
    objections_missed = fields.Integer()
    started_at = fields.Datetime()
    completed_at = fields.Datetime()

class RoleplayScenario(models.Model):
    _name = 'era.roleplay.scenario'
    _description = 'Roleplay Scenario'

    name = fields.Char(required=True)
    name_ar = fields.Char('Name (Arabic)')
    difficulty = fields.Selection([
        ('beginner', 'Beginner'), ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ])
    customer_persona = fields.Text('Customer Persona (JSON)')
    # {"name": "أبو محمد", "role": "IT Manager", "company_type": "government",
    #  "personality": "skeptical", "budget_concern": true, "region": "riyadh"}
    objections = fields.Text('Objections Pool (JSON)')
    # [{"text": "الميزانية ما تسمح", "type": "budget", "difficulty": "hard"},
    #  {"text": "عندنا عقد مع شركة ثانية", "type": "competitor", "difficulty": "medium"}]
    success_criteria = fields.Text('What Good Looks Like (JSON)')
    industry = fields.Many2one('res.partner.industry')

class WinLossAnalysis(models.Model):
    _name = 'era.win.loss.analysis'
    _description = 'Win/Loss Analysis'

    lead_id = fields.Many2one('crm.lead', required=True)
    salesperson_id = fields.Many2one('res.users')
    outcome = fields.Selection([('won', 'Won'), ('lost', 'Lost')])

    # AI analysis
    key_factors = fields.Text('Key Win/Loss Factors (JSON)')
    # [{"factor": "Fast response time", "impact": "positive", "weight": 0.3}]
    objection_handling_score = fields.Float()
    follow_up_quality_score = fields.Float()
    relationship_building_score = fields.Float()
    recommendation = fields.Text('AI Recommendation (Arabic)')
    lessons_learned = fields.Text('Lessons Learned (Arabic)')
    analyzed_at = fields.Datetime()

class SalespersonScorecard(models.Model):
    _name = 'era.salesperson.scorecard'
    _description = 'Monthly Performance Scorecard'

    salesperson_id = fields.Many2one('res.users', required=True)
    month = fields.Date('Month')

    # Metrics
    deals_won = fields.Integer()
    deals_lost = fields.Integer()
    win_rate = fields.Float()
    avg_deal_size = fields.Float()
    avg_cycle_days = fields.Float()
    total_revenue = fields.Float()

    # AI coaching
    roleplay_sessions = fields.Integer()
    avg_roleplay_score = fields.Float()
    top_strength = fields.Char()
    top_improvement_area = fields.Char()
    ai_coaching_notes = fields.Text('AI Coaching Notes (Arabic)')
    recommended_scenarios = fields.Many2many('era.roleplay.scenario')
```

### Saudi Objection Library

```python
SAUDI_OBJECTIONS = [
    {
        'text_ar': 'الميزانية ما تسمح هالسنة',
        'text_en': 'Budget doesn\'t allow this year',
        'type': 'budget',
        'recommended_response': 'اقترح خطة دفع مرنة أو إصدار مصغر',
    },
    {
        'text_ar': 'عندنا عقد مع شركة ثانية لسه ما انتهى',
        'text_en': 'We have an existing contract',
        'type': 'competitor',
        'recommended_response': 'اسأل عن موعد انتهاء العقد واعرض تجهيز عرض مبكر',
    },
    {
        'text_ar': 'لازم أرجع للإدارة',
        'text_en': 'Need to check with management',
        'type': 'authority',
        'recommended_response': 'اعرض تقديم عرض رسمي يساعده يعرضه على الإدارة',
    },
    {
        'text_ar': 'الحين مو الوقت المناسب',
        'text_en': 'Not the right time',
        'type': 'timing',
        'recommended_response': 'اسأل عن الوقت الأنسب واحجز متابعة',
    },
    {
        'text_ar': 'شفنا نظام أرخص',
        'text_en': 'Found a cheaper option',
        'type': 'price_competition',
        'recommended_response': 'ركز على القيمة والتكلفة الإجمالية للملكية',
    },
    # ... 20+ more Saudi-specific objections
]
```

### Roleplay Engine

```python
class RoleplayEngine:
    """
    LLM acts as a Saudi customer with specific persona.
    Evaluates salesperson responses in real-time.
    """
    def generate_customer_response(self, session, salesperson_message):
        """
        1. Stay in character as the customer persona
        2. Introduce objections naturally based on scenario
        3. React realistically to salesperson's approach
        4. Track which objections were handled well
        """
        prompt = f"""
        أنت تمثل دور عميل سعودي بالمواصفات التالية:
        {session.scenario_id.customer_persona}

        الاعتراضات المتاحة: {session.scenario_id.objections}

        رد بشكل طبيعي على رسالة المندوب التالية:
        "{salesperson_message}"

        ابق في الشخصية. لا تكشف أنك ذكاء اصطناعي.
        """
        pass
```

### Win/Loss Analysis

```python
def analyze_deal(self, lead):
    """
    Triggered when lead moves to won or lost.
    1. Collect all activities, messages, notes from the deal
    2. LLM analyzes the full deal history
    3. Identifies key win/loss factors
    4. Generates Arabic recommendations
    5. Updates salesperson scorecard
    """
    pass
```

### Cron Jobs

- **Win/Loss Analysis:** Triggered by stage change (automation rule), not cron
- **Monthly Scorecard:** First of each month, generates scorecards for all reps

### LLM Integration

| Task | Model | Notes |
|------|-------|-------|
| Roleplay conversation | Claude / GPT-4o | Needs Arabic fluency + persona acting |
| Roleplay evaluation | Claude / GPT-4o | Needs nuanced feedback |
| Win/Loss analysis | GPT-4o-mini | Structured analysis |
| Scorecard coaching | GPT-4o-mini | Template-based |
| Voice roleplay (optional) | Whisper + TTS | Future enhancement |

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.roleplay.max_turns` | 20 | Max conversation turns per session |
| `era.roleplay.llm_model` | `claude-sonnet-4-6` | Model for roleplay |
| `era.roleplay.analysis_model` | `gpt-4o-mini` | Model for analysis |
| `era.roleplay.scorecard_day` | 1 | Day of month for scorecards |

## File Structure

```
era_crm_roleplay_trainer/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── roleplay_session.py
│   ├── roleplay_scenario.py
│   ├── roleplay_engine.py         # AI conversation engine
│   ├── win_loss_analysis.py
│   ├── salesperson_scorecard.py
│   ├── objection_library.py       # Saudi objections
│   └── crm_lead_inherit.py        # Win/loss trigger
├── data/
│   ├── cron.xml                   # Monthly scorecard cron
│   ├── automation.xml             # Win/loss trigger
│   ├── scenarios_demo.xml         # Demo scenarios
│   ├── objections.xml             # Saudi objection library
│   └── system_parameters.xml
├── views/
│   ├── roleplay_session_views.xml
│   ├── roleplay_scenario_views.xml
│   ├── win_loss_views.xml
│   ├── scorecard_views.xml
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
        │   └── roleplay_chat.js   # Chat interface for roleplay
        └── xml/
            └── roleplay_chat.xml
```

## Testing Checklist

- [ ] Roleplay bot stays in character as Saudi customer
- [ ] Objections introduced naturally in conversation
- [ ] Session scoring evaluates objection handling
- [ ] Win/loss analysis triggers on deal close
- [ ] Analysis identifies meaningful factors
- [ ] Monthly scorecards generate for all reps
- [ ] AI coaching notes are actionable
- [ ] Chat interface works for roleplay

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] At least 10 roleplay scenarios with Saudi objections
- [ ] Roleplay engine produces realistic conversations
- [ ] Win/loss analysis generates after every close
- [ ] Monthly scorecards with AI coaching
- [ ] Arabic translations complete
