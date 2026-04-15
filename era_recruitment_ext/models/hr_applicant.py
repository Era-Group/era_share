import importlib.util
import io
import json
import logging
import re
from string import Template

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html2plaintext
from odoo.tools.pdf import PdfReadError, PdfReader


_logger = logging.getLogger(__name__)

MAX_CV_CHARS = 12000


class HrApplicant(models.Model):
    _inherit = 'hr.applicant'

    ai_match_score = fields.Float(string="AI Match Score", digits=(5, 2), tracking=True)
    ai_match_score_display = fields.Char(
        string="AI Match Score (%)",
        compute="_compute_ai_match_score_display",
    )
    ai_match_summary = fields.Text(string="AI Summary", tracking=True)
    ai_match_strengths = fields.Text(string="AI Strengths")
    ai_match_gaps = fields.Text(string="AI Gaps")
    ai_match_raw_response = fields.Text(string="AI Raw Response")
    ai_match_last_run = fields.Datetime(string="AI Match Last Run", tracking=True)

    @api.depends('ai_match_score')
    def _compute_ai_match_score_display(self):
        for applicant in self:
            score = applicant.ai_match_score
            if score is False or score is None:
                applicant.ai_match_score_display = False
                continue
            formatted = ("%.2f" % score).rstrip('0').rstrip('.')
            applicant.ai_match_score_display = "%s%%" % formatted

    def action_ai_analyze_cv(self):
        self.ensure_one()
        agent = self._get_ai_agent()
        prompt = self._build_ai_prompt()
        response_text = self._call_ai_agent(agent, prompt)
        parsed = self._parse_ai_response(response_text)

        values = {
            'ai_match_last_run': fields.Datetime.now(),
            'ai_match_raw_response': response_text,
        }
        if parsed:
            values.update(parsed)
        else:
            values.update({
                'ai_match_score': False,
                'ai_match_summary': response_text,
                'ai_match_strengths': False,
                'ai_match_gaps': False,
            })

        self.write(values)

        message = _("AI CV analysis completed.")
        if values.get('ai_match_score') is not False:
            message = _("AI CV analysis completed. Match score: %s", values.get('ai_match_score'))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("AI CV Analysis"),
                'message': message,
                'sticky': False,
                'next': {
                    'type': 'ir.actions.client',
                    'tag': 'soft_reload',
                },
            },
        }

    def _get_ai_agent(self):
        param_value = self.env['ir.config_parameter'].sudo().get_param('era_recruitment_ext.ai_agent_id')
        agent = self.env['ai.agent']
        if param_value:
            try:
                agent = agent.browse(int(param_value)).sudo()
            except (TypeError, ValueError):
                agent = self.env['ai.agent']
        if not agent or not agent.exists():
            agent = self.env['ai.agent'].sudo().search([], limit=1)
        if not agent:
            raise UserError(_("No AI agent is configured. Set one in Settings > Recruitment."))
        return agent

    def _build_ai_prompt(self):
        prompt_template = self.env['ir.config_parameter'].sudo().get_param(
            'era_recruitment_ext.ai_prompt_template'
        )
        if not prompt_template:
            raise UserError(_("AI prompt template is not configured."))

        if '$user_language' not in prompt_template:
            prompt_template = (
                prompt_template.rstrip()
                + "\n\n"
                + "The summary/strengths/gaps must be written in the user's language: $user_language."
            )

        cv_text = self._get_cv_text()
        job = self.job_id
        lang_code = (self.env.user.lang or '').strip()
        lang_name = ''
        if lang_code:
            lang = self.env['res.lang'].sudo().search([('code', '=', lang_code)], limit=1)
            lang_name = lang.name or lang_code
        values = {
            'applicant_name': self.partner_name or '',
            'job_name': job.name or '' if job else '',
            'job_description': html2plaintext(job.description or '') if job else '',
            'job_requirements': job.requirements or '' if job else '',
            'cv_text': cv_text,
            'user_language': lang_name or lang_code or 'English',
        }

        try:
            return Template(prompt_template).substitute(values)
        except KeyError as exc:
            raise UserError(_(
                "Unknown placeholder '%s' in AI prompt template. "
                "Available: $applicant_name, $job_name, $job_description, "
                "$job_requirements, $cv_text, $user_language."
            ) % exc.args[0])

    def _get_cv_text(self):
        attachments = self._get_cv_attachments()
        if not attachments:
            raise UserError(_("No CV attachments found. Please attach a CV first."))

        texts = []
        for attachment in attachments:
            content = self._extract_text_from_attachment(attachment)
            if content:
                texts.append(content)

        if not texts:
            raise UserError(self._get_no_text_error(attachments))

        cv_text = "\n\n".join(texts)
        if len(cv_text) > MAX_CV_CHARS:
            cv_text = cv_text[:MAX_CV_CHARS]
        return cv_text

    def _get_cv_attachments(self):
        # 1. Use the designated main attachment when available — it's explicitly marked as primary.
        main = (
            self.message_main_attachment_id
            if hasattr(self, 'message_main_attachment_id') and self.message_main_attachment_id
            else self.env['ir.attachment']
        )
        if main and main.type == 'binary' and self._is_supported_text_attachment(main):
            return main

        # 2. Check direct record attachments (binary, CV-format files only).
        direct = self.attachment_ids.filtered(
            lambda a: a.type == 'binary' and self._is_supported_text_attachment(a)
        )
        if direct:
            return direct

        # 3. Last resort: search message attachments but restrict to CV-format files only
        #    (PDF, DOCX, ODT, TXT) to avoid picking up emails or other unrelated files.
        if hasattr(self, 'message_ids'):
            return self.message_ids.attachment_ids.filtered(
                lambda a: a.type == 'binary' and self._is_supported_text_attachment(a)
            )
        return self.env['ir.attachment']

    def _extract_text_from_attachment(self, attachment):
        content = None
        if hasattr(attachment, '_get_attachment_content'):
            content = attachment._get_attachment_content()
        if not content:
            content = attachment.index_content
        raw = attachment.raw or b''
        if not content and raw:
            try:
                content = attachment._index(raw, attachment.mimetype)
            except Exception:
                _logger.exception("Failed to index attachment %s", attachment.id)
                content = None
        if not content and raw and self._is_pdf_attachment(attachment, raw):
            content = self._extract_pdf_text(raw)
        if not content:
            return ''
        return str(content).strip()

    def _get_no_text_error(self, attachments):
        hints = []
        attachment_details = self._format_attachment_details(attachments)
        pdf_attachments = attachments.filtered(
            lambda attachment: (attachment.mimetype or '').endswith('pdf')
        )
        image_only = all(
            attachment.mimetype and attachment.mimetype.startswith('image/')
            for attachment in attachments
        )
        unsupported_only = all(
            not self._is_supported_text_attachment(attachment)
            for attachment in attachments
        )
        if pdf_attachments and not importlib.util.find_spec('pdfminer.high_level'):
            hints.append(_("PDF text extraction requires the pdfminer.six package."))
        if image_only:
            hints.append(_("Image-based CVs need OCR (Recruitment Extract) or a text-based PDF/DOCX."))
        if pdf_attachments and not image_only:
            hints.append(_("If the PDF is scanned, enable OCR (Recruitment Extract) or upload a text-based file."))
        if unsupported_only:
            hints.append(_("Supported text formats: PDF (text), DOCX, ODT, TXT."))
        if hints:
            return _("No readable text found in the attached CV(s). Checked: %s. %s") % (
                attachment_details, " ".join(hints)
            )
        return _("No readable text found in the attached CV(s).")

    def _format_attachment_details(self, attachments):
        details = []
        for attachment in attachments:
            name = attachment.name or str(attachment.id)
            mimetype = attachment.mimetype or 'unknown'
            details.append("%s (%s)" % (name, mimetype))
        return ", ".join(details)

    def _is_supported_text_attachment(self, attachment):
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return (
            mimetype.endswith('pdf')
            or mimetype in (
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/vnd.oasis.opendocument.text',
                'text/plain',
            )
            or name.endswith(('.pdf', '.docx', '.odt', '.txt'))
        )

    def _is_pdf_attachment(self, attachment, raw):
        mimetype = (attachment.mimetype or '').lower()
        name = (attachment.name or '').lower()
        return mimetype.endswith('pdf') or name.endswith('.pdf') or raw.startswith(b'%PDF-')

    def _extract_pdf_text(self, raw):
        try:
            reader = PdfReader(io.BytesIO(raw))
            texts = []
            for page in reader.pages:
                page_text = page.extract_text() or ''
                page_text = page_text.strip()
                if page_text:
                    texts.append(page_text)
            return "\n".join(texts)
        except PdfReadError:
            return ''
        except Exception:
            _logger.exception("Failed to extract PDF text with PdfReader")
            return ''

    def _call_ai_agent(self, agent, prompt):
        try:
            response_parts = agent.get_direct_response(prompt)
        except UserError:
            raise
        except Exception:
            _logger.exception("AI CV analysis failed for applicant %s", self.id)
            raise UserError(_("AI analysis failed. Please try again later."))

        response_text = "\n\n".join(response_parts or []).strip()
        if not response_text:
            raise UserError(_("AI did not return a response."))
        return response_text

    def _parse_ai_response(self, response_text):
        data = self._extract_json(response_text)
        if not isinstance(data, dict):
            return None

        summary = self._stringify(data.get('summary'))
        strengths = self._list_to_text(data.get('strengths'))
        gaps = self._list_to_text(data.get('gaps'))

        score = self._to_float(data.get('match_score'))
        if score is not None:
            score = max(0.0, min(100.0, score))
        else:
            score = False

        return {
            'ai_match_score': score,
            'ai_match_summary': summary or False,
            'ai_match_strengths': strengths or False,
            'ai_match_gaps': gaps or False,
        }

    def _extract_json(self, response_text):
        cleaned = (response_text or '').strip()
        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```[a-zA-Z0-9]*\n', '', cleaned)
            cleaned = cleaned.rsplit('```', 1)[0].strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        match = re.search(r'\{.*\}', cleaned, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None

    def _stringify(self, value):
        if value in (None, False):
            return ''
        return str(value).strip()

    def _list_to_text(self, value):
        if not value:
            return ''
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return "\n".join("- %s" % item for item in items)
        return str(value).strip()

    def _to_float(self, value):
        if value in (None, False, ''):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
