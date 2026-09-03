"""The requests a firm actually makes, ready to run from the case.

Building an AI request by hand meant choosing an agent, ticking the right
catalogue entries, and writing instructions that ask for the right shape of
answer — every time, for the same dozen tasks. A playbook is one of those
tasks written down once: which agent, which data, what to ask for, and who
the output is for. From the case, a lawyer picks the task and lands on the
request already assembled, with the payload in front of them and consent
still theirs to give.

Nothing in the governance changes. The playbook fills the form; the request
still clears the same policy, redaction, consent and audit on the way out.
Sensitive entries are never ticked by a playbook on its own — the wizard
offers them, named, and the lawyer chooses.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LegalAIPlaybook(models.Model):
    _name = 'legal.ai.playbook'
    _description = 'Legal AI Playbook'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True,
                       help="The task as a lawyer would name it.")
    summary = fields.Text(translate=True,
                          help="What comes back, in one or two lines. Shown before the task is chosen.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    category = fields.Selection([
        ('client', 'For the Client'),
        ('brief', 'Brief and Assessment'),
        ('preparation', 'Preparation'),
        ('drafting', 'Drafting'),
        ('research', 'Research and Documents'),
        ('closing', 'Handover and Closing'),
    ], required=True, default='brief')
    icon = fields.Char(default='fa-magic')
    agent_id = fields.Many2one(
        'ai.agent', required=True, ondelete='restrict',
        help="The agent that runs this task. It must be approved for legal work before "
             "any request built from this playbook can be sent.")
    field_ids = fields.Many2many(
        'legal.ai.field', 'legal_ai_playbook_field_rel', 'playbook_id', 'field_id',
        string='Data Shared', help="Ticked on every request built from this playbook.")
    optional_field_ids = fields.Many2many(
        'legal.ai.field', 'legal_ai_playbook_optional_rel', 'playbook_id', 'field_id',
        string='Sensitive Extras',
        help="Entries this task can use but that carry names, narrative or money. Never "
             "ticked by the playbook itself; the lawyer adds them from the wizard.")
    instructions = fields.Text(
        required=True, translate=True,
        help="What the agent is asked to produce: the structure, the tone, the limits. Sent "
             "as the request's instructions, after redaction like everything else.")
    applies_to = fields.Selection([
        ('all', 'Every case'),
        ('litigation', 'Litigation'),
        ('execution', 'Execution'),
        ('consultation', 'Consultation'),
    ], default='all', required=True, help="Which case types offer this task.")
    needs_document = fields.Boolean(
        help="The task works on one document; the wizard asks which.")
    needs_question = fields.Boolean(
        help="The task needs the lawyer's own facts or question; the wizard insists on them.")
    is_shipped = fields.Boolean(
        default=False, readonly=True,
        help="Shipped with the module. A firm's own playbooks are left alone by upgrades.")
    agent_ready = fields.Boolean(
        compute='_compute_agent_ready',
        help="Whether the agent behind this task has been approved for legal work.")

    @api.depends('agent_id.legal_approved')
    def _compute_agent_ready(self):
        for playbook in self:
            playbook.agent_ready = bool(playbook.agent_id.legal_approved)

    def _request_values(self, case, document=None, question=None, extras=None):
        """Everything a request needs, assembled from this playbook."""
        self.ensure_one()
        fields_to_share = self.field_ids | (extras or self.env['legal.ai.field'])
        if document and not any(f.technical_name == 'document_text' for f in fields_to_share):
            text_entry = self.env['legal.ai.field'].search(
                [('technical_name', '=', 'document_text')], limit=1)
            fields_to_share |= text_entry
        instructions = self.instructions or ''
        if question:
            instructions = '%s\n\nما يقدمه المحامي:\n%s' % (instructions, question.strip())
        return {
            'purpose': self.name,
            'playbook_id': self.id,
            'agent_id': self.agent_id.id,
            'case_id': case.id,
            'document_id': document.id if document else False,
            'field_ids': [(6, 0, fields_to_share.ids)],
            'input_payload': instructions,
        }


class LegalAIRequestPlaybook(models.Model):
    _inherit = 'legal.ai.request'

    playbook_id = fields.Many2one(
        'legal.ai.playbook', string='Playbook', ondelete='set null', readonly=True,
        help="The task this request was built from, if any.")


class LegalAIPlaybookWizard(models.TransientModel):
    """Pick the task, see what it shares, land on the assembled request."""
    _name = 'legal.ai.playbook.wizard'
    _description = 'Ask the AI about a case'

    case_id = fields.Many2one('legal.case', required=True,
                              default=lambda self: self.env.context.get('active_id'))
    case_type = fields.Selection(related='case_id.case_type')
    playbook_id = fields.Many2one('legal.ai.playbook', required=True, string='Task')
    document_id = fields.Many2one('legal.document', string='Document')
    question = fields.Text(
        string='Your facts or question',
        help="Whatever the task needs from you: the facts as you know them, the question to "
             "research, the point to argue. Redacted with everything else.")
    include_extras = fields.Boolean(
        string='Also share the sensitive extras',
        help="Adds the entries this task can use that carry names, narrative or money. "
             "They are listed below so you know exactly what that means.")

    needs_document = fields.Boolean(related='playbook_id.needs_document')
    needs_question = fields.Boolean(related='playbook_id.needs_question')
    agent_ready = fields.Boolean(related='playbook_id.agent_ready')
    agent_name = fields.Char(related='playbook_id.agent_id.name')
    briefing = fields.Html(compute='_compute_briefing')

    @api.depends('playbook_id', 'include_extras', 'case_id')
    def _compute_briefing(self):
        for wizard in self:
            playbook = wizard.playbook_id
            if not playbook:
                wizard.briefing = ''
                continue
            shared = '، '.join(playbook.field_ids.mapped('name')) or _('nothing from the case')
            extras = '، '.join(playbook.optional_field_ids.mapped('name'))
            parts = ['<p>%s</p>' % (playbook.summary or '')]
            parts.append('<p><b>%s</b> %s</p>' % (_('Will share:'), shared))
            if extras:
                parts.append('<p><b>%s</b> %s</p>' % (
                    _('Sensitive extras this task can use:'), extras))
            if playbook.needs_document:
                parts.append('<p><i>%s</i></p>' % _('Works on one document — choose it below.'))
            if playbook.needs_question:
                parts.append('<p><i>%s</i></p>' % _('Needs your own facts or question below.'))
            wizard.briefing = ''.join(parts)

    @api.onchange('playbook_id')
    def _onchange_playbook_reset(self):
        self.include_extras = False
        if not self.playbook_id.needs_document:
            self.document_id = False

    def action_run(self):
        self.ensure_one()
        playbook = self.playbook_id
        if playbook.applies_to not in ('all', self.case_id.case_type):
            raise UserError(_('"%s" is not offered for this type of case.', playbook.name))
        if playbook.needs_document and not self.document_id:
            raise UserError(_('This task works on a document. Choose which one.'))
        if self.document_id and self.document_id.case_id != self.case_id:
            raise UserError(_('The document belongs to another case.'))
        if playbook.needs_question and not (self.question or '').strip():
            raise UserError(_('This task needs your facts or question. Write them in the box.'))
        extras = playbook.optional_field_ids if self.include_extras else None
        request = self.env['legal.ai.request'].create(playbook._request_values(
            self.case_id, document=self.document_id, question=self.question, extras=extras))
        return {
            'type': 'ir.actions.act_window',
            'name': request.purpose,
            'res_model': 'legal.ai.request',
            'res_id': request.id,
            'view_mode': 'form',
            'target': 'current',
        }


class LegalCaseAskAI(models.Model):
    _inherit = 'legal.case'

    def action_ask_ai(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Ask the AI'),
            'res_model': 'legal.ai.playbook.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'active_id': self.id, 'default_case_id': self.id},
        }
