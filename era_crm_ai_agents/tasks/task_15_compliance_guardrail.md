# Task 15 — Compliance Guardrail (طبقة حارس الامتثال)

## Context

**Phase:** 1 (MUST be first) | **Priority:** Critical — Prerequisite for all outbound agents  
**Duration:** 1–1.5 weeks  
**Dependencies:** None (all other outbound agents depend on this)  
**Module name:** `era_crm_compliance`  
**Path:** `submodules/era_share_latest/era_crm_compliance/`

## Business Value

This is NOT a standalone agent — it's a **cross-cutting layer** that every outbound agent must pass through before sending any message. It's cheap to build and prevents costly PDPL violations. It also enforces Saudi cultural norms (prayer times, Hijri calendar awareness) and proper Arabic etiquette (titles, greetings). Build this FIRST.

## What This Agent Does

1. **Manages PDPL consent records** — blocks any automated send without explicit consent; opt-out within 72 hours
2. **Enforces send windows** — respects Hijri calendar, prayer times, and business hours
3. **Applies cultural speech norms** — greetings (السلام عليكم), titles (شيخ/أستاذ), respectful closings
4. **Ensures data erasure** capability — DSAR support, logged to critical event log (Rule 20)

## Technical Requirements

### Odoo Models

```python
class PDPLConsent(models.Model):
    _name = 'era.pdpl.consent'
    _description = 'PDPL Marketing Consent Record'

    partner_id = fields.Many2one('res.partner', required=True, index=True)
    consent_type = fields.Selection([
        ('marketing_whatsapp', 'WhatsApp Marketing'),
        ('marketing_email', 'Email Marketing'),
        ('marketing_sms', 'SMS Marketing'),
        ('transactional', 'Transactional Messages'),
    ], required=True)
    status = fields.Selection([
        ('granted', 'Consent Granted'),
        ('revoked', 'Consent Revoked'),
        ('expired', 'Consent Expired'),
        ('pending', 'Pending Confirmation'),
    ], required=True, default='pending')
    granted_at = fields.Datetime()
    revoked_at = fields.Datetime()
    expires_at = fields.Datetime()
    consent_source = fields.Selection([
        ('explicit_form', 'Explicit Web Form'),
        ('whatsapp_opt_in', 'WhatsApp Opt-in Reply'),
        ('verbal_recorded', 'Verbal (Recorded)'),
        ('imported', 'Imported from Legacy'),
    ])
    consent_proof = fields.Text('Proof Reference')  # Link to recording, form submission, etc.
    ip_address = fields.Char()  # For web form consent

class PDPLOptOut(models.Model):
    _name = 'era.pdpl.optout'
    _description = 'PDPL Opt-Out Request'

    partner_id = fields.Many2one('res.partner', required=True)
    channel = fields.Selection([
        ('whatsapp', 'WhatsApp'), ('email', 'Email'),
        ('sms', 'SMS'), ('all', 'All Channels'),
    ])
    requested_at = fields.Datetime(default=fields.Datetime.now)
    processed_at = fields.Datetime()
    processed_within_72h = fields.Boolean(compute='_compute_sla')
    status = fields.Selection([
        ('pending', 'Pending'),
        ('processed', 'Processed'),
        ('overdue', 'Overdue (>72h)'),
    ])

class DSARRequest(models.Model):
    _name = 'era.dsar.request'
    _description = 'Data Subject Access Request'

    partner_id = fields.Many2one('res.partner', required=True)
    request_type = fields.Selection([
        ('access', 'Data Access Request'),
        ('deletion', 'Data Deletion Request'),
        ('correction', 'Data Correction Request'),
        ('portability', 'Data Portability Request'),
    ])
    status = fields.Selection([
        ('received', 'Received'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
    ], default='received')
    data_export = fields.Binary('Exported Data')
    completed_at = fields.Datetime()

class ComplianceEventLog(models.Model):
    _name = 'era.compliance.event'
    _description = 'Compliance Event Log (Rule 20)'

    event_type = fields.Selection([
        ('consent_granted', 'Consent Granted'),
        ('consent_revoked', 'Consent Revoked'),
        ('message_blocked', 'Message Blocked (No Consent)'),
        ('message_sent', 'Message Sent (Compliant)'),
        ('optout_requested', 'Opt-Out Requested'),
        ('optout_processed', 'Opt-Out Processed'),
        ('dsar_received', 'DSAR Received'),
        ('dsar_completed', 'DSAR Completed'),
        ('data_deleted', 'Data Deleted'),
        ('send_window_blocked', 'Blocked by Send Window'),
        ('cultural_correction', 'Cultural Norm Applied'),
    ])
    partner_id = fields.Many2one('res.partner')
    agent_name = fields.Char('Requesting Agent')
    details = fields.Text()
    timestamp = fields.Datetime(default=fields.Datetime.now)
    user_id = fields.Many2one('res.users')
```

### Compliance Gate API

```python
class ComplianceGate:
    """
    Every outbound agent calls this before sending any message.
    Returns: {allowed: bool, reason: str, corrections: list}
    """

    def check_send(self, partner_id, channel, message_text, agent_name):
        """
        Master compliance check. Returns dict:
        {
            "allowed": True/False,
            "blocked_reasons": ["no_consent", "prayer_time", ...],
            "corrections": [{"type": "add_greeting", "suggestion": "السلام عليكم"}],
            "modified_text": "..." (with cultural corrections applied)
        }
        """
        result = {"allowed": True, "blocked_reasons": [], "corrections": []}

        # 1. PDPL Consent check
        if not self._has_valid_consent(partner_id, channel):
            result["allowed"] = False
            result["blocked_reasons"].append("no_pdpl_consent")

        # 2. Opt-out check
        if self._is_opted_out(partner_id, channel):
            result["allowed"] = False
            result["blocked_reasons"].append("opted_out")

        # 3. Send window check
        if not self._in_send_window():
            result["allowed"] = False
            result["blocked_reasons"].append("outside_send_window")

        # 4. Cultural corrections
        corrections = self._check_cultural_norms(message_text)
        result["corrections"] = corrections

        # 5. Log event
        self._log_event(result, partner_id, agent_name)

        return result
```

### Send Window Logic

```python
class SendWindowChecker:
    """
    Blocks sends during:
    1. Prayer times (5 daily prayers + buffer)
    2. Outside business hours (configurable)
    3. Hijri holidays (Eid al-Fitr, Eid al-Adha, National Day)
    4. Friday prayer time (extended block)
    """

    def _in_send_window(self):
        now = fields.Datetime.now()  # Convert to AST
        
        # Check prayer times
        if self._is_prayer_time(now):
            return False

        # Check Hijri holidays
        if self._is_hijri_holiday(now):
            return False

        # Check business hours
        if not self._is_business_hours(now):
            return False

        return True

    def _is_prayer_time(self, dt):
        """
        Check against prayer times for current date.
        Source: Aladhan API or local calculation.
        Block: prayer_time ± buffer_minutes
        """
        pass

    def _is_hijri_holiday(self, dt):
        """
        Check Hijri calendar for major holidays.
        Uses hijri_converter library.
        """
        pass
```

### Cultural Norms Engine

```python
class CulturalNormsChecker:
    """
    Ensures messages meet Saudi cultural expectations.
    """

    def check_cultural_norms(self, message_text):
        corrections = []

        # 1. Greeting check
        if not self._has_greeting(message_text):
            corrections.append({
                'type': 'add_greeting',
                'suggestion': 'السلام عليكم ورحمة الله',
                'position': 'start',
            })

        # 2. Title check (if partner has known title)
        # Sheikh, Ustaz, Doctor, Engineer
        title_correction = self._check_title(message_text)
        if title_correction:
            corrections.append(title_correction)

        # 3. Closing check
        if not self._has_closing(message_text):
            corrections.append({
                'type': 'add_closing',
                'suggestion': 'وتقبلوا فائق الاحترام والتقدير',
                'position': 'end',
            })

        return corrections

    GREETINGS = ['السلام عليكم', 'أهلاً', 'مرحباً', 'صباح الخير', 'مساء الخير']
    TITLES = {
        'sheikh': ['شيخ', 'الشيخ'],
        'ustaz': ['أستاذ', 'الأستاذ'],
        'doctor': ['دكتور', 'الدكتور'],
        'engineer': ['مهندس', 'المهندس'],
    }
```

### Opt-Out Processing

```python
def process_optout(self, partner_id, channel='all'):
    """
    1. Revoke all relevant consent records
    2. Mark opt-out as processed
    3. Check SLA (must be within 72 hours)
    4. Log to compliance event log
    5. Block all future sends for this partner+channel
    """
    pass
```

### DSAR Handler

```python
def handle_dsar(self, partner_id, request_type):
    """
    Data Subject Access Request handler:
    - access: Export all partner data to JSON/PDF
    - deletion: Remove personal data, keep anonymized records
    - correction: Flag records for correction
    - portability: Export in machine-readable format
    """
    pass
```

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.compliance.prayer_buffer_minutes` | 20 | Minutes to block around prayers |
| `era.compliance.business_hours_start` | 8 | Start of send window (AST) |
| `era.compliance.business_hours_end` | 21 | End of send window (AST) |
| `era.compliance.consent_expiry_days` | 365 | Days until consent expires |
| `era.compliance.optout_sla_hours` | 72 | Max hours to process opt-out |
| `era.compliance.friday_block_start` | 11 | Friday prayer block start |
| `era.compliance.friday_block_end` | 14 | Friday prayer block end |

## Security & Compliance

- This IS the security layer — must be bulletproof
- All events logged immutably (Rule 20)
- Consent records cannot be deleted (only revoked)
- DSAR processing logged completely
- No LLM calls — pure rule-based logic (no AI cost, no AI hallucination risk)

## File Structure

```
era_crm_compliance/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── pdpl_consent.py
│   ├── pdpl_optout.py
│   ├── dsar_request.py
│   ├── compliance_event_log.py
│   ├── compliance_gate.py         # Main gate API
│   ├── send_window.py             # Prayer + hours + holidays
│   ├── cultural_norms.py          # Arabic etiquette checker
│   └── res_partner_inherit.py     # Consent status on partner
├── data/
│   ├── system_parameters.xml
│   ├── hijri_holidays.xml         # Major Hijri holidays
│   └── cron.xml                   # Opt-out SLA checker cron
├── views/
│   ├── pdpl_consent_views.xml
│   ├── pdpl_optout_views.xml
│   ├── dsar_views.xml
│   ├── compliance_log_views.xml
│   ├── res_partner_views_inherit.xml
│   └── dashboard.xml              # Compliance dashboard
├── security/
│   ├── ir.model.access.csv
│   └── compliance_groups.xml      # Special compliance officer group
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Consent required before any send — blocks without consent
- [ ] Opt-out revokes consent within 72h
- [ ] Opt-out SLA monitoring works (alerts for overdue)
- [ ] Prayer time blocking works (5 daily + Friday extended)
- [ ] Hijri holiday blocking works
- [ ] Business hours enforcement works
- [ ] Cultural norms: missing greeting detected
- [ ] Cultural norms: missing title detected
- [ ] DSAR access request exports all partner data
- [ ] DSAR deletion anonymizes correctly
- [ ] All events logged to compliance log
- [ ] Consent records cannot be deleted
- [ ] Compliance gate API callable by other agents

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] PDPL consent management complete (grant, revoke, expire)
- [ ] Opt-out processing within 72h SLA
- [ ] DSAR handler for access + deletion
- [ ] Send window blocks prayer times + holidays + hours
- [ ] Cultural norms checker functional
- [ ] Compliance event log captures all events
- [ ] Compliance gate API documented and testable
- [ ] Other agents (#1, #9, #10) can call gate API
- [ ] No LLM dependency — pure rules
- [ ] Arabic translations complete
- [ ] Compliance dashboard for management
