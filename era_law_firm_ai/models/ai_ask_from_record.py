"""Ask the AI from any record of a file, not only from the case itself.

Odoo's own AI button reaches these records too, and it sends them straight to
the model with no consent screen, no redaction, no hash and no audit entry —
the chain that every legal.ai.request goes through. It is left where it is,
deliberately, as the quick path; what this adds is the governed one, at the
same reach: a button on the hearing and the document as well as the case, so
the lawyer who needs an answer they can be asked about does not have to walk
back to the case to get it.

A playbook works on a case, so a record's job here is only to say which case it
belongs to — and, where the record is itself a document, which document.
"""
from odoo import _, models
from odoo.exceptions import UserError


class LegalAIAskable(models.AbstractModel):
    """Adds the governed Ask AI button to a record that belongs to a case."""
    _name = 'legal.ai.askable'
    _description = 'Record that can open the governed AI wizard'

    def _ai_case(self):
        """The case this record belongs to. Overridden where the link differs."""
        self.ensure_one()
        return self.case_id

    def _ai_document(self):
        """The document to work on, when the record is one."""
        return self.env['legal.document']

    def action_ask_ai(self):
        self.ensure_one()
        case = self._ai_case()
        if not case:
            raise UserError(_(
                'This record is not linked to a case yet, and every AI task '
                'works on a case file. Link it to one first.'))
        context = {
            'active_id': case.id,
            'active_model': 'legal.case',
            'default_case_id': case.id,
        }
        document = self._ai_document()
        if document:
            context['default_document_id'] = document.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Ask the AI'),
            'res_model': 'legal.ai.playbook.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': context,
        }


class LegalHearing(models.Model):
    _name = 'legal.hearing'
    _inherit = ['legal.hearing', 'legal.ai.askable']


class LegalDeadline(models.Model):
    _name = 'legal.deadline'
    _inherit = ['legal.deadline', 'legal.ai.askable']


class LegalDocument(models.Model):
    _name = 'legal.document'
    _inherit = ['legal.document', 'legal.ai.askable']

    def _ai_document(self):
        # opened from a document, the tasks that work on one start with it
        return self

class LegalEngagement(models.Model):
    _name = 'legal.engagement'
    _inherit = ['legal.engagement', 'legal.ai.askable']


class LegalConflictCheck(models.Model):
    _name = 'legal.conflict.check'
    _inherit = ['legal.conflict.check', 'legal.ai.askable']


class LegalConsultation(models.Model):
    _name = 'legal.consultation'
    _inherit = ['legal.consultation', 'legal.ai.askable']
