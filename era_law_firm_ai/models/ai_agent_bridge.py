"""Governance lives on the agent.

Odoo 19 ships `ai.agent`, and it is the agent that carries the system prompt, the
LLM model, the sources and the tools. Since the model is what decides where the
data actually goes, the firm's approval and its PDPL record belong on the same
record -- not on a separate provider sitting in front of it, which could only ever
describe one of its agents correctly.

`ai.agent` has no company of its own, and the firms running this are branches of
one practice, so approval is a single decision rather than one per company.

The request still clears policy before anything is dispatched: an approved agent,
explicit consent from a named user, the field whitelist, the document's
classification against the agent's ceiling, redaction, a hash, an audit entry.
The agent only ever receives the redacted payload.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_sanitize
from odoo.tools.mail import plaintext2html

_logger = logging.getLogger(__name__)

# Ordered from least to most sensitive. `blocked` is deliberately absent: it is not a
# level an agent can be raised to, it means the document never leaves at all.
CLASSIFICATION_LEVELS = [
    ('public', 'Public'),
    ('internal', 'Internal'),
    ('confidential', 'Confidential'),
]
CLASSIFICATION_ORDER = [key for key, _label in CLASSIFICATION_LEVELS]


class AIAgent(models.Model):
    _inherit = 'ai.agent'

    legal_approved = fields.Boolean(
        string='Approved for Legal Work', copy=False,
        help="The firm accepts this agent for privileged client material. Until it is ticked "
             "the agent cannot receive a single legal request, whatever permissions the user holds. "
             "Record the processing location and retention policy before ticking it.")
    legal_processing_location = fields.Char(
        string='Processing Location',
        help="Country or region where the model behind this agent processes the data. Required "
             "under the Personal Data Protection Law. It follows the LLM Model above, so revisit "
             "it whenever that changes.")
    legal_retention_policy = fields.Text(
        string='Retention Policy',
        help="How long the model provider keeps prompts and outputs, and how deletion is "
             "requested. Recorded so the firm can answer for what happens to client material.")
    legal_max_classification = fields.Selection(
        CLASSIFICATION_LEVELS, string='Highest Classification Accepted', default='confidential',
        help="The most sensitive document this agent may receive; anything above it is refused. "
             "The levels are ordered, so Confidential also covers Internal and Public. A document "
             "marked Blocked is never sent to any agent whatever this says.")

    @api.constrains('legal_max_classification')
    def _check_max_classification(self):
        for agent in self:
            if agent.legal_approved and not agent.legal_max_classification:
                raise ValidationError(_(
                    'Set the highest classification "%s" may receive.', agent.name))

    @api.constrains('legal_approved', 'legal_processing_location', 'legal_retention_policy')
    def _check_legal_approval(self):
        for agent in self:
            if agent.legal_approved and not (agent.legal_processing_location and agent.legal_retention_policy):
                raise ValidationError(_(
                    'Record where "%s" processes data and how long it is kept before approving it '
                    'for legal work.', agent.name))


class LegalAIRequest(models.Model):
    _inherit = 'legal.ai.request'

    agent_id = fields.Many2one(
        'ai.agent', string='AI Agent', required=True, ondelete='restrict',
        domain=[('legal_approved', '=', True)],
        help="The agent that answers this request. Its system prompt, model and sources decide "
             "how the answer is produced, and its approval is what allows the request at all.")
    has_approved_agent = fields.Boolean(
        compute='_compute_has_approved_agent',
        help="Whether any agent has been approved for legal work yet. An empty agent list means "
             "none has, not that something is broken.")

    @api.depends_context('company')
    def _compute_has_approved_agent(self):
        approved = bool(self.env['ai.agent'].sudo().search_count([('legal_approved', '=', True)], limit=1))
        for record in self:
            record.has_approved_agent = approved

    def action_open_agents(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Agents'),
            'res_model': 'ai.agent',
            'view_mode': 'list,form',
        }

    def _build_extra_system_context(self):
        """Overridden where the charter lives; empty when none is configured."""
        self.ensure_one()
        return ''

    def _build_agent_prompt(self, payload):
        """What the agent is asked. Only ever the redacted payload."""
        self.ensure_one()
        lines = [f'الغرض: {self.purpose}']
        if self.case_id:
            case_types = dict(self.case_id._fields['case_type'].selection)
            lines.append(f'نوع القضية: {case_types.get(self.case_id.case_type, "")}')
            if self.case_id.court_id:
                lines.append(f'المحكمة: {self.case_id.court_id.display_name}')
        lines.append('')
        lines.append(payload)
        return '\n'.join(lines)

    def _dispatch_to_provider(self, payload):
        """Called by action_send once the request has cleared policy and redaction."""
        self.ensure_one()
        try:
            messages = self.agent_id._generate_response(
                self._build_agent_prompt(payload),
                extra_system_context=self._build_extra_system_context())
        except Exception as error:
            _logger.warning('era_law_firm_ai: agent %s failed on request %s: %s',
                            self.agent_id.name, self.id, error)
            raise UserError(_('The AI agent could not answer this request: %s', error)) from error
        return self._store_sanitized_response('\n\n'.join(messages) if messages else '')


class AIAgentDefaults(models.Model):
    _inherit = 'ai.agent'

    legal_field_ids = fields.Many2many(
        'legal.ai.field', 'ai_agent_legal_field_rel', 'agent_id', 'field_id',
        string='Data This Agent Needs',
        help="Ticked automatically on a new request for this agent, so the lawyer starts from "
             "what the work actually needs instead of an empty list.")


class LegalAIRequestFields(models.Model):
    _inherit = 'legal.ai.request'

    field_ids = fields.Many2many(
        'legal.ai.field', 'legal_ai_request_field_rel', 'request_id', 'field_id',
        string='Data to Share',
        help="Tick what the agent may see. The payload is built from exactly these, so nothing "
             "else about the case can leave even by accident.")
    instructions_sent = fields.Text(
        string='Instructions as Sent', readonly=True, copy=False,
        help="The extra instructions after redaction, kept so the request can be asked again with "
             "them adjusted. The unredacted original is still discarded on dispatch.")
    payload_preview = fields.Text(
        string='Exactly What Will Be Sent', compute='_compute_payload_preview',
        help="The assembled payload after redaction -- the real thing, not a description of it. "
             "Read it before giving consent.")

    @api.onchange('agent_id')
    def _onchange_agent_default_fields(self):
        if self.agent_id and not self.field_ids:
            self.field_ids = self.agent_id.legal_field_ids

    @api.depends('field_ids', 'case_id', 'document_id', 'input_payload')
    def _compute_payload_preview(self):
        for record in self:
            record.payload_preview = record._prepare_redacted_payload() if record.state in (
                'draft', 'approved') else record.redacted_payload

    @api.depends('field_ids')
    def _compute_fields_sent(self):
        for record in self:
            record.fields_sent = ','.join(sorted(record.field_ids.mapped('technical_name')))

    def _document_text(self):
        """Plain text of the attached document, best effort."""
        self.ensure_one()
        attachment = self.document_id.attachment_id
        if not attachment or not attachment.raw:
            return ''
        try:
            return attachment.raw.decode('utf-8', errors='replace')[:20000]
        except Exception:
            return ''

    @staticmethod
    def _strip_html(value):
        import re
        return re.sub(r'<[^>]+>', ' ', value or '').strip()

    def _prepare_redacted_payload(self):
        """Assembled from the ticked entries, then redacted. The free-text box is
        the lawyer's own instructions and goes through the same redaction."""
        self.ensure_one()
        parts = []
        for entry in self.field_ids.sorted('sequence'):
            value = entry._value_for(self)
            if value:
                parts.append(f'{entry.name}: {value}')
        if self.input_payload:
            parts.extend(['', self.input_payload])
        return self._redact('\n'.join(parts))


class LegalAIRequestScope(models.Model):
    """Keep a request inside one client file.

    The document was pickable from any case, so a request could carry the text of
    one client's document while labelled as another client's matter -- the same
    crossing of files the conflict check exists to stop, arriving through a form
    field. The domain narrows the list; the constraint is what actually enforces
    it, since a domain is only a convenience in the interface.
    """
    _inherit = 'legal.ai.request'

    @api.constrains('case_id', 'document_id')
    def _check_document_belongs_to_case(self):
        for record in self:
            if record.case_id and record.document_id and record.document_id.case_id != record.case_id:
                raise ValidationError(_(
                    'The document "%(document)s" belongs to case %(other)s, not %(case)s. '
                    'A request must stay within one client file.',
                    document=record.document_id.name,
                    other=record.document_id.case_id.name or _('another case'),
                    case=record.case_id.name))

    @api.onchange('document_id')
    def _onchange_document_sets_case(self):
        if self.document_id and not self.case_id:
            self.case_id = self.document_id.case_id

    @api.onchange('case_id')
    def _onchange_case_clears_foreign_document(self):
        if self.case_id and self.document_id and self.document_id.case_id != self.case_id:
            self.document_id = False

    def _check_provider_policy(self):
        """Also answer for the document's own file.

        The inherited check only looked at case_id, so a request carrying just a
        document skipped the access check entirely -- and that document's text is
        the widest thing the catalogue can send.
        """
        super()._check_provider_policy()
        for record in self:
            document_case = record.document_id.case_id
            if document_case and not self.env.user.can_access_legal_case(document_case):
                raise UserError(_('You cannot send a document from a case you cannot access.'))


class LegalAIRequestGate(models.Model):
    """Say which gate is shut, not that a gate is shut.

    The four preconditions used to share one message, so a request that failed on
    the company kill switch read exactly like one missing consent. Each is now
    reported on its own, with where to go and fix it.
    """
    _inherit = 'legal.ai.request'

    ai_enabled = fields.Boolean(
        related='company_id.legal_ai_enabled', string='AI Enabled for Company', readonly=True,
        help="The company-level switch. While it is off no request can be sent, whatever else "
             "is configured.")

    def _assert_gate_open(self):
        self.ensure_one()
        if not self.company_id.legal_ai_enabled:
            raise UserError(_(
                'Governed AI is switched off for %(company)s, so no request can be sent. '
                'A manager turns it on under Settings > Law Firm > Artificial Intelligence.',
                company=self.company_id.display_name))
        if not self.agent_id:
            raise UserError(_('Choose the AI agent that should answer this request.'))
        if not self.agent_id.legal_approved:
            raise UserError(_(
                'The agent "%(agent)s" is not approved for legal work. A legal manager approves it '
                'under Configuration > AI Agents, after recording where it processes data and how '
                'long it keeps it.', agent=self.agent_id.name))
        if not (self.consent_user_id and self.consent_date):
            raise UserError(_(
                'This request has no recorded consent. Use "Give Consent" first -- consent names '
                'the person who agreed to this material leaving the firm.'))


class LegalAIRequestClassification(models.Model):
    """Refuse on classification with the numbers in hand.

    The message used to say only that the policy disallowed the classification,
    without naming the document, its level, the agent, or its ceiling -- leaving
    the reader with nothing to act on.
    """
    _inherit = 'legal.ai.request'

    document_classification = fields.Selection(
        related='document_id.ai_classification', string='Document Classification', readonly=True)
    agent_max_classification = fields.Selection(
        related='agent_id.legal_max_classification', string='Agent Accepts Up To', readonly=True)
    classification_blocked = fields.Boolean(
        compute='_compute_classification_blocked',
        help="Whether the document is too sensitive for the chosen agent, so the request would be "
             "refused on dispatch.")

    @api.depends('document_id.ai_classification', 'agent_id.legal_max_classification')
    def _compute_classification_blocked(self):
        for record in self:
            record.classification_blocked = bool(record._classification_refusal())

    def _classification_refusal(self):
        """The reason this pairing cannot be sent, or an empty string."""
        self.ensure_one()
        level = self.document_id.ai_classification if self.document_id else 'internal'
        if level == 'blocked':
            return _('The document "%s" is marked Blocked, so it is never sent to any agent.',
                     self.document_id.name)
        ceiling = self.agent_id.legal_max_classification
        if not ceiling or level not in CLASSIFICATION_ORDER:
            return ''
        if CLASSIFICATION_ORDER.index(level) <= CLASSIFICATION_ORDER.index(ceiling):
            return ''
        labels = dict(CLASSIFICATION_LEVELS)
        return _(
            'The document "%(document)s" is classified %(level)s, and the agent "%(agent)s" accepts '
            'nothing above %(ceiling)s. Either raise what the agent accepts under Configuration > '
            'AI Agents, or reclassify the document -- reclassifying is a decision about the '
            'material itself, not a way around the check.',
            document=self.document_id.name,
            level=labels.get(level, level),
            agent=self.agent_id.name,
            ceiling=labels.get(ceiling, ceiling))

    def _check_provider_policy(self):
        super()._check_provider_policy()
        for record in self:
            refusal = record._classification_refusal()
            if refusal:
                raise UserError(refusal)


class LegalAIResponseRendering(models.Model):
    """Render the agent's answer as it was written.

    Models reply in markdown -- headings, numbered points, tables -- and stored as
    plain text that arrives as one unbroken block, which is the worst possible
    shape for a legal memo the lawyer has to read and check. Odoo's own AI module
    converts with markdown2 and then sanitises; this follows the same path so the
    output looks the same wherever it is shown.

    Redaction still runs first, on the raw text, before any conversion.
    """
    _inherit = 'legal.ai.request'

    # Declared here and nowhere else: a second declaration elsewhere in the module
    # would win the MRO on import order and silently put the type back to Text.
    # sanitize stays at its default -- the content comes from a language model and is
    # untrusted, so scripts and event handlers must not survive into the form.
    sanitized_response = fields.Html(
        readonly=True,
        help="The agent's answer, redacted again on the way in and rendered from the markdown the "
             "model wrote. A draft for a lawyer to review, never advice on its own.")

    def _store_sanitized_response(self, response):
        self.ensure_one()
        redacted = self._redact(response or '')[:100000]
        self.write({'sanitized_response': self._render_markdown(redacted), 'state': 'done'})

    @api.model
    def _render_markdown(self, text):
        if not text:
            return ''
        try:
            from markdown2 import markdown
        except ImportError:
            return plaintext2html(text)
        try:
            return html_sanitize(markdown(text, extras=['fenced-code-blocks', 'tables', 'strike']))
        except Exception:
            # a malformed reply should still be readable rather than lost
            return plaintext2html(text)


class LegalAIRequestCaseLink(models.Model):
    """Always materialise the case link.

    A request can be raised against a document alone, and the onchange that fills
    in the case only fires in the interface. Setting it on write as well keeps the
    link real for imports and scripted creation, which is what lets the case count
    its own AI activity with a plain one2many instead of an or-domain that would
    quietly miss those rows.
    """
    _inherit = 'legal.ai.request'

    def _case_from_document(self, values):
        if values.get('document_id') and not values.get('case_id'):
            document = self.env['legal.document'].browse(values['document_id'])
            if document.case_id:
                values['case_id'] = document.case_id.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._case_from_document(dict(v)) for v in vals_list])

    def write(self, vals):
        if vals.get('document_id') and not vals.get('case_id'):
            for record in self:
                if not record.case_id:
                    record_vals = self._case_from_document(dict(vals))
                    super(LegalAIRequestCaseLink, record).write(record_vals)
            return True
        return super().write(vals)


class LegalCaseAIActivity(models.Model):
    """Show a case what AI work has been done on it."""
    _inherit = 'legal.case'

    ai_request_ids = fields.One2many('legal.ai.request', 'case_id', string='AI Requests')
    ai_request_count = fields.Integer(compute='_compute_ai_request_count', string='AI Requests')
    ai_dispatched_count = fields.Integer(
        compute='_compute_ai_request_count', string='Dispatched to an Agent',
        help="Of those, how many actually left the firm. A draft that was never sent shared nothing.")

    @api.depends('ai_request_ids.state')
    def _compute_ai_request_count(self):
        for case in self:
            requests = case.ai_request_ids
            case.ai_request_count = len(requests)
            case.ai_dispatched_count = len(requests.filtered(lambda r: r.state in ('sent', 'done')))

    def action_view_ai_requests(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('AI Requests'),
            'res_model': 'legal.ai.request',
            'view_mode': 'list,form',
            'domain': [('case_id', '=', self.id)],
            'context': {'default_case_id': self.id},
        }


class LegalAIRequestResend(models.Model):
    """Ask again on the same record.

    The lawyer changes the instructions and asks the same question again, so the
    request returns to input rather than branching into a new one. What was already
    sent is snapshotted first: the payload, its hash and the answer are the record
    of what left the firm, and reopening writes over all three.
    """
    _inherit = 'legal.ai.request'

    attempt_ids = fields.One2many('legal.ai.attempt', 'request_id', string='Dispatch History', readonly=True)
    attempt_count = fields.Integer(compute='_compute_attempt_count')

    @api.depends('attempt_ids')
    def _compute_attempt_count(self):
        for record in self:
            record.attempt_count = len(record.attempt_ids)

    def action_resend(self):
        """Return this request to input, keeping what was already sent."""
        self.ensure_one()
        if self.state not in ('sent', 'done', 'rejected', 'cancelled'):
            raise UserError(_('This request is still open -- edit it and send it.'))

        attempt = self.env['legal.ai.attempt']._snapshot(self)
        self.write({
            'state': 'draft',
            'consent_user_id': False,
            'consent_date': False,
            'redacted_payload': False,
            'payload_hash': False,
            'sanitized_response': False,
            'charter_id': False,
            'input_payload': self.input_payload or self.instructions_sent,
            'instructions_sent': False,
        })
        if attempt:
            self.message_post(body=_(
                'Reopened to ask again. Dispatch %(number)s is kept in the history with its '
                'payload, hash and answer.', number=attempt.sequence_number))
        return True

    def action_view_attempts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Dispatch History'),
            'res_model': 'legal.ai.attempt',
            'view_mode': 'list,form',
            'domain': [('request_id', '=', self.id)],
        }
