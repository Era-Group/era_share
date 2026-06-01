# Task 03 — Waterfall Enrichment Engine (محرك الإثراء بالتسلسل)

## Context

**Phase:** 1 (Infrastructure) | **Priority:** High — Foundation for many agents  
**Duration:** 1–1.5 weeks  
**Dependencies:** None (foundational)  
**Module name:** `era_crm_waterfall_enrichment`  
**Path:** `submodules/era_share_latest/era_crm_waterfall_enrichment/`

## Business Value

Data quality is the prerequisite for every downstream agent. The waterfall model tries enrichment providers one-by-one, stopping on success — this minimizes cost because you only pay when enrichment actually works. Contactability check (email + phone + WhatsApp) ensures outreach channels are valid before agents attempt communication.

## What This Agent Does

1. **Tries enrichment providers sequentially** (waterfall) — stops when data is found:
   - Provider 1 → Provider 2 → Provider 3 → Web Search fallback
2. **Fills missing data columns** from public sources via targeted web search + LLM extraction
3. **Validates contactability:** Email (ZeroBounce) + Phone (HLR lookup) + WhatsApp (waha number check)
4. **Updates `res.partner` / `crm.lead`** with enriched data and quality score

## Technical Requirements

### Odoo Models

```python
# New model: era.enrichment.provider
class EnrichmentProvider(models.Model):
    _name = 'era.enrichment.provider'
    _description = 'Enrichment Data Provider'
    _order = 'sequence'

    name = fields.Char(required=True)  # e.g., "LinkedIn", "Wathiq", "Maroof"
    provider_type = fields.Selection([
        ('linkedin', 'LinkedIn'),
        ('wathiq', 'Wathiq (Saudi CR)'),
        ('maroof', 'Maroof'),
        ('zerobounce', 'ZeroBounce (Email)'),
        ('hlr', 'HLR (Phone)'),
        ('waha', 'WhatsApp Check'),
        ('web_search', 'Web Search + LLM'),
        ('custom_api', 'Custom API'),
    ])
    sequence = fields.Integer(default=10)  # Waterfall order
    api_endpoint = fields.Char()
    is_active = fields.Boolean(default=True)
    cost_per_call = fields.Float('Cost per API Call ($)', digits=(6, 4))
    success_rate = fields.Float('Historical Success Rate %', digits=(5, 2))
    monthly_budget = fields.Float('Monthly Budget Cap ($)')
    monthly_spent = fields.Float('Spent This Month ($)')

# New model: era.enrichment.request
class EnrichmentRequest(models.Model):
    _name = 'era.enrichment.request'
    _description = 'Enrichment Request'

    partner_id = fields.Many2one('res.partner')
    lead_id = fields.Many2one('crm.lead')
    request_type = fields.Selection([
        ('full', 'Full Enrichment'),
        ('contactability', 'Contactability Check Only'),
        ('specific', 'Specific Fields Only'),
    ])
    status = fields.Selection([
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'All Providers Failed'),
    ], default='queued')
    fields_requested = fields.Text('Fields to Enrich (JSON)')
    fields_enriched = fields.Text('Fields Enriched (JSON)')
    providers_tried = fields.Text('Providers Tried (JSON)')  # [{provider, status, cost, time}]
    total_cost = fields.Float('Total Cost ($)')
    contactability = fields.Text('Contactability Result (JSON)')
    # {email: {valid: bool, provider: str}, phone: {valid: bool}, whatsapp: {exists: bool}}
```

### Waterfall Logic

```python
def enrich_record(self, record, fields_needed):
    """
    Try each provider in sequence order.
    Stop when all requested fields are filled OR all providers exhausted.
    Pay-on-success: only count cost when provider returns data.
    """
    providers = self.env['era.enrichment.provider'].search(
        [('is_active', '=', True)],
        order='sequence'
    )
    results = {}
    for provider in providers:
        if all(f in results for f in fields_needed):
            break  # All fields filled
        if provider.monthly_spent >= provider.monthly_budget:
            continue  # Budget exhausted for this provider
        try:
            data = self._call_provider(provider, record)
            if data:
                results.update(data)
                provider.monthly_spent += provider.cost_per_call
        except Exception:
            continue  # Try next provider
    return results
```

### Contactability Check

```python
def check_contactability(self, record):
    """Validate all contact channels."""
    result = {}
    # Email: ZeroBounce API
    if record.email:
        result['email'] = self._check_email_zerobounce(record.email)
    # Phone: HLR Lookup
    if record.phone or record.mobile:
        result['phone'] = self._check_phone_hlr(record.phone or record.mobile)
    # WhatsApp: waha number check
    if record.mobile:
        result['whatsapp'] = self._check_whatsapp_waha(record.mobile)
    return result
```

### Web Search + LLM Fallback

- When all API providers fail, use web search (Google/Bing API) to find public info
- LLM extracts structured data from search results
- Cost: ~$0.005 per record (cheap model)

### API Integration Points

| Provider | Purpose | API |
|----------|---------|-----|
| LinkedIn | Company info, employee count, industry | LinkedIn API / Proxycurl |
| Wathiq | Saudi CR number validation, company status | Wathiq API |
| Maroof | Saudi business verification, reviews | Maroof API |
| ZeroBounce | Email validation | ZeroBounce API |
| HLR | Phone number validation | HLR Lookup API |
| waha | WhatsApp number check | Local waha instance |
| Web Search | Fallback data collection | Google/Bing API |

### Configuration

| Key | Default | Description |
|-----|---------|-------------|
| `era.enrichment.max_providers_per_request` | 5 | Max providers to try |
| `era.enrichment.monthly_budget_total` | 100.0 | Total monthly enrichment budget ($) |
| `era.enrichment.web_search_llm_model` | `gpt-4o-mini` | Model for web search extraction |
| `era.enrichment.batch_size` | 50 | Records per batch |

### UI Components

1. **Provider configuration view** — drag-drop reorder for waterfall sequence
2. **Enrichment request list** — with cost tracking and success rates
3. **Smart button** on `res.partner` and `crm.lead`: "Enrich" → triggers enrichment
4. **Dashboard:** Provider performance, cost breakdown, success rates

## Security & Compliance

- API keys stored in system parameters / `process.env` (Rule 03)
- Budget caps enforced in code (Rule 14)
- Enrichment results logged (Rule 20)
- No outbound messages — data enrichment only

## File Structure

```
era_crm_waterfall_enrichment/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── enrichment_provider.py
│   ├── enrichment_request.py
│   ├── enrichment_engine.py       # Waterfall logic
│   ├── contactability_checker.py  # Email/Phone/WhatsApp validation
│   ├── web_search_enrichment.py   # Fallback web search + LLM
│   ├── res_partner_inherit.py     # Enrich button
│   └── crm_lead_inherit.py        # Enrich button
├── data/
│   ├── enrichment_providers.xml   # Default providers
│   └── system_parameters.xml
├── views/
│   ├── enrichment_provider_views.xml
│   ├── enrichment_request_views.xml
│   ├── res_partner_views_inherit.xml
│   └── crm_lead_views_inherit.xml
├── security/
│   └── ir.model.access.csv
├── i18n/
│   └── ar.po
└── static/
    └── description/
        └── icon.png
```

## Testing Checklist

- [ ] Waterfall tries providers in sequence order
- [ ] Stops when all fields filled (pay-on-success)
- [ ] Budget cap prevents overspending per provider
- [ ] Email validation via ZeroBounce works
- [ ] Phone HLR lookup works
- [ ] WhatsApp check via waha works
- [ ] Web search + LLM fallback extracts data
- [ ] Smart buttons on partner and lead trigger enrichment
- [ ] Provider stats (success rate, cost) update correctly
- [ ] Monthly budget resets correctly

## Definition of Done

- [ ] Module installs cleanly on Odoo 17
- [ ] At least 3 providers configured and working
- [ ] Waterfall logic stops on success
- [ ] Contactability check validates email + phone + WhatsApp
- [ ] Budget caps enforced
- [ ] Dashboard shows provider performance
- [ ] Arabic translations complete
- [ ] Other agents (#1, #2) can call enrichment via API
