"""What the AI is handed when it is opened on a record of a legal file.

Odoo serialises the record's own fields and its chatter, which is a record but
not a file: it says nothing of the hearings, the deadlines, the documents or
how the case got here. The server adds those, which is both richer and more
trustworthy: it can read the file's hearings, deadlines, documents and history
— the same renderings the governed request uses — and it reads them as the
user, so a restricted document stays out of a chat opened by someone who
cannot open it.

This is the quick path, not the governed one: no consent screen, no redaction,
no audit entry. It is the firm's decision, resting on the models running inside
its own system. What it buys is an agent that has both the statute corpus and
the file in front of it — which neither of the two buttons beside it can be.
"""
import json

from odoo import models

from .ai_research_button import RESEARCH_KEY

# Both ways the same agent is reached: the app's own key, sent from every
# screen inside the app, and Odoo's key, which one of our records still
# answers with when it is opened from outside the app. Neither knows anything
# of a case file, so the file is added to both.
LEGAL_KEYS = (RESEARCH_KEY, 'chatter_ai_button')

# What is worth sending, in the order a lawyer would read it. The narrative
# entries live on legal.case, so a hearing and a document reach the same file.
CASE_BLOCKS = (
    ('الأطراف وأدوارهم', '_era_render_parties'),
    ('سجل الجلسات', '_era_render_hearings'),
    ('المواعيد النظامية', '_era_render_deadlines'),
    ('المستندات', '_era_render_documents'),
    ('مسار الملف', '_era_render_timeline'),
    ('الملخص المالي', '_era_render_financials'),
)


class LegalAIAnyRecordContext(models.AbstractModel):
    """Any record at all, when the question is asked with the firm's key.

    Odoo serialises the record for its own keys and reads it from the browser.
    Ours arrives without that, and the screens it covers are every screen in
    the app — a statute, a template, a rule — so the record is read here, on
    the server, as the user who asked.
    """
    _inherit = 'base'

    def _ai_initialise_context(self, caller_component, text_selection=None,
                              front_end_info=None):
        context = super()._ai_initialise_context(
            caller_component, text_selection, front_end_info)
        if caller_component == RESEARCH_KEY and self:
            context.append(
                'أنت تنظر إلى سجل «%s» داخل نظام إدارة مكتب محاماة سعودي، '
                'وهذه بياناته: %s' % (self._description, self._era_ai_record_json()))
        return context

    def _era_ai_record_json(self):
        """The record's fields, with Arabic left as Arabic.

        Odoo's serialiser escapes non-ASCII, which turns every Arabic letter
        into six characters — on a file written in Arabic that is most of the
        context, spent on nothing. Re-encoding is cheaper than a second
        serialiser, and keeps whatever Odoo decides to put in.
        """
        data = self._ai_serialize_fields_data()
        try:
            return json.dumps(json.loads(data), ensure_ascii=False, default=str)
        except ValueError:
            return data


class LegalAIRecordContext(models.AbstractModel):
    """Mixed into every record the firm's AI button can be pressed on."""
    _inherit = 'legal.ai.askable'

    def _era_ai_case(self):
        """The case whose file should travel with this record."""
        return self._ai_case()

    def _ai_initialise_context(self, caller_component, text_selection=None,
                              front_end_info=None):
        context = super()._ai_initialise_context(
            caller_component, text_selection, front_end_info)
        if caller_component not in LEGAL_KEYS:
            return context
        case = self._era_ai_case()
        if case:
            context.append(case._era_ai_file_context())
        return context


class LegalCaseChatContext(models.Model):
    _inherit = 'legal.case'

    def _era_ai_case(self):
        return self

    def _ai_initialise_context(self, caller_component, text_selection=None,
                              front_end_info=None):
        context = super()._ai_initialise_context(
            caller_component, text_selection, front_end_info)
        if caller_component not in LEGAL_KEYS:
            return context
        context.append(
            'أنت تنظر إلى ملف قضية داخل نظام إدارة مكتب محاماة سعودي.')
        context.append(self._era_ai_file_context())
        return context

    def _era_ai_file_context(self):
        """The file as text: only the blocks that have something in them.

        An empty heading tells the model a section exists and is blank, which
        it then tends to remark on; leaving it out says the same thing without
        spending the context on it.
        """
        self.ensure_one()
        parts = ['ملف القضية %s:' % (self.name or '')]
        for title, renderer in CASE_BLOCKS:
            body = getattr(self, renderer)()
            if body:
                parts.append('%s:\n%s' % (title, body))
        return '\n\n'.join(parts)
