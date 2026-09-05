"""What the firm's own AI button hands the agent when it is opened on a record.

Odoo builds this context only for its own three interface keys, so a button
with a key of its own would arrive with the record's id and nothing in it. The
server fills that in instead of the browser, which is both richer and more
trustworthy: it can read the file's hearings, deadlines, documents and history
— the same renderings the governed request uses — and it reads them as the
user, so a restricted document stays out of a chat opened by someone who
cannot open it.

This is the quick path, not the governed one: no consent screen, no redaction,
no audit entry. It is the firm's decision, resting on the models running inside
its own system. What it buys is an agent that has both the statute corpus and
the file in front of it — which neither of the two buttons beside it can be.
"""
from odoo import models

from .ai_research_button import RESEARCH_KEY

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
        if caller_component != RESEARCH_KEY:
            return context
        context.append(
            'أنت تنظر إلى سجل «%s» داخل نظام إدارة مكتب محاماة سعودي، '
            'وهذه بياناته: %s' % (self._description, self._ai_serialize_fields_data()))
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
        if caller_component != RESEARCH_KEY:
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
