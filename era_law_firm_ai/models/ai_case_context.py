"""What a case can tell an agent, beyond single fields.

The catalogue could send a case's reference, court and claim amount, and the
full text of one document — nothing that says what has actually happened on
the file. A report of the proceedings to date, a brief with next steps, a
hearing to prepare for: each needs the hearings, the deadlines, the documents
and the file's own history, rendered the way a lawyer would read them.

These are composite catalogue entries. Each is still one line in the
whitelist, still ticked or unticked by the lawyer, still redacted like every
other line, and still visible in the preview before consent. What changes is
that a tick now sends a rendered log instead of one value. The rendering
respects the same boundaries the rest of the module does: a restricted
document is listed only to someone allowed to see it, and anything that names
a person or reads like a narrative is marked sensitive.
"""
import re

from odoo import fields, models
from odoo.tools import html2plaintext

from .ai import LegalAIRequest as _Base

COMPOSITE_FIELDS = {
    'parties_roles', 'hearings_log', 'deadlines_log', 'documents_list',
    'stage_timeline', 'case_log', 'financial_summary', 'internal_notes',
}


class LegalAIRequestContext(models.Model):
    _inherit = 'legal.ai.request'

    # Later registration wins the MRO, so this is the set the policy reads.
    _ALLOWED_FIELDS = _Base._ALLOWED_FIELDS | COMPOSITE_FIELDS

    def _composite_renderers(self):
        return {
            'parties_roles': self._render_parties,
            'hearings_log': self._render_hearings,
            'deadlines_log': self._render_deadlines,
            'documents_list': self._render_documents,
            'stage_timeline': self._render_timeline,
            'case_log': self._render_case_log,
            'financial_summary': self._render_financials,
            'internal_notes': self._render_internal_notes,
        }

    # ---------------------------------------------------------------- helpers
    def _label(self, record, field_name):
        field = record._fields[field_name]
        return dict(field._description_selection(record.env)).get(record[field_name], '')

    @staticmethod
    def _date(value):
        return fields.Date.to_string(value) if value else ''

    @staticmethod
    def _text(value):
        return re.sub(r'\s+', ' ', html2plaintext(value or '')).strip()

    # -------------------------------------------------------------- renderers
    def _render_parties(self):
        lines = []
        for party in self.case_id.party_ids:
            line = '- %s: %s' % (self._label(party, 'role'), party.partner_id.display_name)
            if party.representative_id:
                line += ' (يمثله %s)' % party.representative_id.display_name
            lines.append(line)
        return '\n'.join(lines)

    def _render_hearings(self):
        lines = []
        for hearing in self.case_id.hearing_ids.sorted('start_datetime'):
            when = self._date(hearing.start_datetime)
            if hearing.hijri_date:
                when += ' (%s هـ)' % hearing.hijri_date
            line = '- %s — %s — %s' % (when, hearing.name, self._label(hearing, 'state'))
            if hearing.outcome:
                line += '\n  ما جرى: %s' % self._text(hearing.outcome)
            lines.append(line)
        return '\n'.join(lines)

    def _render_deadlines(self):
        lines = []
        for deadline in self.case_id.deadline_ids.sorted('deadline_date'):
            lines.append('- %s — %s — %s — المصدر: %s' % (
                self._date(deadline.deadline_date), deadline.name,
                self._label(deadline, 'state'), deadline.source or ''))
        return '\n'.join(lines)

    def _render_documents(self):
        """Titles and states only; the text of a document is its own entry.

        A restricted document is listed only to someone allowed to open it,
        so a report built by one lawyer cannot reveal that another's
        restricted file exists.
        """
        lines = []
        user = self.env.user
        for document in self.case_id.document_ids.sorted('create_date'):
            if document.restricted and user not in document.allowed_user_ids \
                    and user != document.owner_id:
                continue
            line = '- %s — %s — %s' % (
                document.name, self._label(document, 'document_type'),
                self._label(document, 'state'))
            if document.hijri_date:
                line += ' — %s هـ' % document.hijri_date
            lines.append(line)
        return '\n'.join(lines)

    def _render_timeline(self):
        """The file's own history: opened, each stage and status change, closed."""
        case = self.case_id
        events = [(case.create_date, 'فتح الملف')]
        tracked = {'stage_id', 'state'}
        # Tracking values are administrator-only by ACL, while the messages
        # they hang on are readable by anyone who can open the case. What is
        # rendered here is the stage and status history of a file the user
        # already has in front of them, so the elevation reveals nothing new.
        for message in case.message_ids:
            for value in message.sudo().tracking_value_ids:
                if value.field_id.name not in tracked:
                    continue
                events.append((message.date, '%s: %s ← %s' % (
                    value.field_id.field_description,
                    value.old_value_char or '—', value.new_value_char or '—')))
        if case.close_date:
            events.append((fields.Datetime.to_datetime(case.close_date), 'إغلاق الملف'))
        events.sort(key=lambda item: item[0] or fields.Datetime.now())
        return '\n'.join('- %s — %s' % (self._date(when), what) for when, what in events)

    def _render_case_log(self):
        """The chatter's own words, newest last, without who wrote them."""
        notes = []
        for message in self.case_id.message_ids.sorted('date'):
            if message.message_type not in ('comment', 'notification'):
                continue
            if message.tracking_value_ids and not self._text(message.body):
                continue
            body = self._text(message.body)
            if body:
                notes.append('- %s — %s' % (self._date(message.date), body[:600]))
        return '\n'.join(notes[-20:])

    def _render_financials(self):
        case = self.case_id
        currency = case.currency_id.name or ''
        rows = [
            ('الساعات القابلة للفوترة', '%.2f' % case.billable_hours),
            ('المفوتر', '%.2f %s' % (case.invoiced_amount, currency)),
            ('المسدد', '%.2f %s' % (case.paid_amount, currency)),
            ('المتبقي', '%.2f %s' % (case.outstanding_amount, currency)),
            ('المصروفات', '%.2f %s' % (case.expense_amount, currency)),
        ]
        if 'trust_allocated_amount' in case._fields:
            rows.append(('أمانة الموكِّل المخصصة لهذه القضية',
                         '%.2f %s' % (case.trust_allocated_amount, currency)))
        return '\n'.join('- %s: %s' % row for row in rows)

    def _render_internal_notes(self):
        return self._text(self.case_id.internal_notes)


class LegalAIFieldComposite(models.Model):
    _inherit = 'legal.ai.field'

    def _value_for(self, request):
        self.ensure_one()
        renderer = request._composite_renderers().get(self.technical_name)
        if renderer and self.source == 'case':
            return renderer() if request.case_id else ''
        return super()._value_for(request)
