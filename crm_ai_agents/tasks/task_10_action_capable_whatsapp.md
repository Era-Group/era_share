# Task 10 — Action-Capable WhatsApp Agent (وكيل واتساب قادر على التنفيذ)

## Context

**Phase:** 4 | **Priority:** Medium  
**Duration:** ~3 weeks (complex API integration)  
**Dependencies:** Agent #9 (Inbound Qualifier), Agent #15 (Compliance)  
**Module name:** `era_crm_wa_action_agent`  
**Path:** `submodules/era_share_latest/era_crm_wa_action_agent/`

## Business Value

Elevates the WhatsApp bot from just answering questions to executing real tasks — checking inventory, generating quotes, booking meetings. Reduces human intervention for routine operations and accelerates the sales cycle.

## What This Agent Does

1. **Calls Odoo APIs in real-time** from WhatsApp: inventory check, price quote, meeting booking
2. **Displays product catalog** and recommends products inside WhatsApp
3. **Books meetings automatically** respecting prayer times and salesperson availability

## Technical Requirements

### Odoo Models

```python
class WAActionSession(models.Model):
    _name = 'era.wa.action.session'
    _description = 'WhatsApp Action Session'

    qualification_id = fields.Many2one('era.wa.qualification')
    waha_chat_id = fields.Char(required=True)
    partner_id = fields.Many2one('res.partner')
    lead_id = fields.Many2one('crm.lead')

    # Actions performed
    actions_log = fields.Text('Actions Log (JSON)')
    # [{"action": "inventory_check", "product": "...", "result": "...", "at": "..."}]

    # Quotes generated
    quotation_ids = fields.Many2many('sale.order')

    # Meetings booked
    event_ids = fields.Many2many('calendar.event')

    status = fields.Selection([
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('escalated', 'Escalated to Human'),
    ], default='active')
```

### Available Actions (Tool Use)

```python
AGENT_TOOLS = {
    'check_inventory': {
        'description': 'Check product stock availability',
        'odoo_model': 'product.product',
        'method': 'check_stock_available',
        'requires_approval': False,
    },
    'generate_quote': {
        'description': 'Generate a price quotation',
        'odoo_model': 'sale.order',
        'method': 'create_wa_quotation',
        'requires_approval': True,  # Needs salesperson approval
    },
    'book_meeting': {
        'description': 'Book a meeting with salesperson',
        'odoo_model': 'calendar.event',
        'method': 'book_wa_meeting',
        'requires_approval': False,
    },
    'get_product_info': {
        'description': 'Get product details and pricing',
        'odoo_model': 'product.template',
        'method': 'get_product_details',
        'requires_approval': False,
    },
    'recommend_products': {
        'description': 'Recommend products based on need',
        'odoo_model': 'product.template',
        'method': 'recommend_products',
        'requires_approval': False,
    },
}
```

### Inventory Check

```python
def check_stock_available(self, product_query):
    """
    1. LLM extracts product name/SKU from customer message
    2. Search product.product by name fuzzy match
    3. Check qty_available in relevant warehouse
    4. Reply with availability in Arabic
    """
    pass
```

### Quote Generation

```python
def create_wa_quotation(self, lead_id, products):
    """
    1. Create sale.order from conversation context
    2. Add order lines from discussed products
    3. Generate PDF quotation
    4. Send to salesperson for approval
    5. On approval → send PDF to customer via WhatsApp
    """
    pass
```

### Meeting Booking with Prayer Time Respect

```python
class PrayerTimeAwareScheduler:
    """
    Books meetings respecting:
    1. Salesperson calendar availability
    2. Saudi prayer times (no meetings during prayer)
    3. Working hours (Sun-Thu, 8AM-5PM AST typically)
    4. Customer preference (morning/afternoon)
    """
    PRAYER_BUFFER_MINUTES = 30  # Block 30 min around each prayer

    def get_available_slots(self, salesperson_id, date, duration_hours=1):
        # 1. Get prayer times for date (API or calculation)
        # 2. Get salesperson's busy slots from calendar
        # 3. Filter out prayer windows + buffer
        # 4. Return available slots
        pass
```

### Product Catalog in WhatsApp

```python
def send_product_catalog(self, chat_id, category=None):
    """
    Send product cards via WhatsApp interactive messages:
    - Product image
    - Name (Arabic)
    - Price
    - Quick reply buttons: "اطلب عرض سعر" / "تفاصيل أكثر"
    """
    pass
```

### LLM Integration

- Function-calling / tool-use LLM that decides which action to invoke
- **Model:** Advanced model needed for tool selection (Claude / GPT-4o)
- **Safety:** All destructive actions (create order, book meeting) require confirmation

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.wa_action.prayer_buffer_minutes` | 30 | Buffer around prayer times |
| `era.wa_action.working_hours_start` | 8 | Working day start (AST) |
| `era.wa_action.working_hours_end` | 17 | Working day end (AST) |
| `era.wa_action.quote_approval_required` | `True` | Require salesperson approval for quotes |
| `era.wa_action.llm_model` | `claude-sonnet-4-6` | Model for action routing |

## Security & Compliance

- Bot operates under limited API permissions — cannot delete, cannot access financials
- Quote creation requires salesperson approval
- All actions logged to critical event log (Rule 20)
- Compliance gate on all outbound messages (Agent #15)
- No superuser access — operates under bot user permissions (Rule 09)

## File Structure

```
era_crm_wa_action_agent/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── wa_action_session.py
│   ├── wa_action_agent.py          # Main agent with tool use
│   ├── wa_inventory_tool.py        # Inventory check
│   ├── wa_quotation_tool.py        # Quote generation
│   ├── wa_meeting_tool.py          # Meeting booking
│   ├── wa_catalog_tool.py          # Product catalog
│   ├── prayer_time_scheduler.py    # Prayer-aware scheduling
│   └── crm_lead_inherit.py
├── controllers/
│   ├── __init__.py
│   └── waha_webhook.py
├── data/
│   ├── system_parameters.xml
│   └── prayer_times.xml            # Prayer time data/API config
├── views/
│   ├── wa_action_session_views.xml
│   └── dashboard.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Inventory check returns correct stock levels
- [ ] Quote generation creates valid sale.order
- [ ] Quote PDF sent after salesperson approval
- [ ] Meeting booking respects prayer times
- [ ] Meeting booking respects salesperson calendar
- [ ] Product catalog renders in WhatsApp
- [ ] Tool selection by LLM works correctly
- [ ] Actions logged to event log
- [ ] Bot cannot perform unauthorized actions

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] At least 5 tools functional (inventory, quote, meeting, catalog, recommend)
- [ ] Prayer time awareness working
- [ ] Salesperson approval flow for quotes
- [ ] All actions logged
- [ ] Compliance gate active
- [ ] Arabic translations complete
