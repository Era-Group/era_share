# Part of Era Group custom addons.
import logging
import re
from datetime import timedelta

from lxml import html as lxml_html

from odoo import _, fields, models, modules
from odoo.addons.whatsapp.tools.whatsapp_exception import WhatsAppError
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rich HTML -> WhatsApp text formatting
# WhatsApp markers: *bold*, _italic_, ~strike~, ```monospace```, • bullets,
# 1. numbered lists. Used when sending Odoo-composed rich text over WAHA.
# ---------------------------------------------------------------------------
_WA_WRAP = {'b': '*', 'strong': '*', 'i': '_', 'em': '_',
            's': '~', 'del': '~', 'strike': '~'}
_WA_MONO = {'code', 'pre', 'tt', 'samp', 'kbd'}
_WA_BLOCK = {'p', 'div', 'section', 'article', 'blockquote', 'tr',
             'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


def _wa_tag(node):
    return node.tag.lower() if isinstance(node.tag, str) else ''


def _wa_wrap_inline(mark, inner):
    """Wrap inner in a WhatsApp marker, keeping surrounding whitespace outside the
    markers (WhatsApp does not format '* text *')."""
    stripped = inner.strip()
    if not stripped:
        return inner
    lead = inner[:len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f'{lead}{mark}{stripped}{mark}{trail}'


def _wa_render(node, ordered=None):
    parts = []
    if node.text:
        parts.append(node.text)
    counter = 0
    for child in node:
        ctag = _wa_tag(child)
        if ctag == 'br':
            parts.append('\n')
        elif ctag in ('ul', 'ol'):
            parts.append('\n' + _wa_render(child, ordered=(ctag == 'ol')))
        elif ctag == 'li':
            counter += 1
            prefix = f'{counter}. ' if ordered else '• '
            parts.append('\n' + prefix + _wa_render(child).strip())
        elif ctag == 'a':
            txt = _wa_render(child)
            href = (child.get('href') or '').strip()
            if href.startswith(('http://', 'https://')) and href not in txt:
                parts.append((txt.strip() + ' ' + href) if txt.strip() else href)
            else:
                parts.append(txt or href)
        elif ctag in _WA_BLOCK:
            parts.append('\n' + _wa_render(child) + '\n')
        elif ctag in _WA_WRAP:
            parts.append(_wa_wrap_inline(_WA_WRAP[ctag], _wa_render(child)))
        elif ctag in _WA_MONO:
            inner = _wa_render(child).strip()
            parts.append(f'```{inner}```' if inner else '')
        else:
            parts.append(_wa_render(child, ordered))
        if child.tail:
            parts.append(child.tail)
    return ''.join(parts)


def html_to_whatsapp(html):
    """Convert an Odoo HTML body to WhatsApp-formatted text (bold/italic/strike/
    monospace/bullets/numbers), falling back to html2plaintext on parse errors."""
    if not html:
        return ''
    text = str(html)
    if '<' not in text:
        return text.strip()
    try:
        root = lxml_html.fragment_fromstring(text, create_parent='o-root')
    except Exception:
        return html2plaintext(text)
    out = _wa_render(root).replace('\xa0', ' ')
    out = re.sub(r'[ \t]+\n', '\n', out)
    out = re.sub(r'[ \t]{2,}', ' ', out)
    out = re.sub(r'\n{3,}', '\n\n', out)
    return out.strip()


class WhatsappMessage(models.Model):
    _inherit = 'whatsapp.message'

    waha_chat_id = fields.Char(copy=False, index=True)

    def _send_message(self, with_commit=False):
        """Route WAHA-account messages through WAHA; delegate the rest to Meta (super)."""
        waha = self.filtered(lambda m: m.wa_account_id.provider == 'waha')
        meta = self - waha
        if meta:
            super(WhatsappMessage, meta)._send_message(with_commit=with_commit)
        for message in waha:
            message._waha_send_one(with_commit=with_commit)

    def _waha_is_cold_outreach(self):
        """True when this message goes to a conversation that never answered us.

        Replies inside a live conversation are the safe kind of traffic and stay allowed
        around the clock; only outreach to silent contacts is worth restricting to
        business hours.
        """
        self.ensure_one()
        message = self.mail_message_id
        if not message or message.model != 'discuss.channel' or not message.res_id:
            return True
        return not self.env['whatsapp.message'].sudo().search_count([
            ('mail_message_id.model', '=', 'discuss.channel'),
            ('mail_message_id.res_id', '=', message.res_id),
            ('message_type', '=', 'inbound'),
        ])

    def _waha_send_one(self, with_commit=False):
        self.ensure_one()
        # We bypass super()'s per-message guards, so re-check them here.
        if self.state != 'outgoing':
            return
        account = self.wa_account_id
        # Hold guard (circuit breaker / cold-outreach window). A hold is temporary, so the
        # message stays 'outgoing' and the queue cron delivers it once the condition
        # clears — deferring rather than failing keeps the conversation intact.
        hold = account._waha_hold_reason(is_cold=self._waha_is_cold_outreach())
        if hold:
            max_hours = account.waha_hold_max_hours or 0
            waited = fields.Datetime.now() - self.create_date
            if max_hours and waited > timedelta(hours=max_hours):
                self._handle_error(
                    failure_type='network',
                    error_message=_("Held for over %(hours)sh and never sent: %(reason)s",
                                    hours=max_hours, reason=hold))
            else:
                _logger.info("WAHA: holding message %s — %s", self.id, hold)
            return
        # Session pre-send guard: sending while the session is not 'working' yields a
        # phantom failure (WAHA 200 with no id, or a 422 "session status not as expected").
        # When the cached status looks non-working, probe WAHA live and branch on the
        # RETURNED status — not on account.waha_status, which the probe's autonomous
        # commit can't refresh inside this REPEATABLE READ transaction. Fail OPEN on a
        # probe error (live is None): the send POST below is the authoritative test, so a
        # transient status-GET blip must not strand a message from a healthy session. Block
        # only when the probe POSITIVELY reports a non-working status.
        if account.waha_status != 'working':
            live = account._waha_probe_status()
            if live is not None and live != 'working':
                self._handle_error(
                    failure_type='network',
                    error_message=_(
                        "WhatsApp session is not connected (status: %s). Reconnect the "
                        "WAHA session, then resend.", live))
                return
        try:
            chat_id = self.waha_chat_id or ''
            if not chat_id:
                self._assert_recipient_identifier()
                if self.mobile_number_formatted:
                    self._check_number_blacklist()
                chat_id = account._waha_chat_id(self.mobile_number_formatted)
            if not chat_id:
                raise WhatsAppError(failure_type='phone_invalid')
            body = html_to_whatsapp(self.body) if self.body else ''
            # Human-like pacing: show "typing…" to the recipient for a length-scaled
            # delay (min 2s) before the message actually goes out (anti-ban).
            account._waha_simulate_typing(chat_id, body)
            attachment = self.mail_message_id.attachment_ids[:1]
            if attachment:
                cap = account.waha_max_media_mb or 0
                if cap and attachment.file_size and attachment.file_size > cap * 1024 * 1024:
                    self._handle_error(
                        failure_type='unknown',
                        error_message=_(
                            "Attachment '%(name)s' is too large (%(size).1f MB, max %(cap)s MB).",
                            name=attachment.name or 'file',
                            size=attachment.file_size / (1024 * 1024), cap=cap))
                    return
                response = account._waha_send_media(chat_id, attachment, caption=body)
            else:
                response = account._waha_send_text(chat_id, body)
            # Dig the id out of the whole response (shape varies by WAHA engine).
            msg_uid = account._waha_extract_id(response)
        except WhatsAppError as err:
            self._handle_error(whatsapp_error_code=err.error_code,
                               error_message=err.error_message, failure_type=err.failure_type)
            return
        except (UserError, ValidationError) as err:
            self._handle_error(failure_type='network', error_message=str(err))
            return
        if not msg_uid:
            # Log the response shape (keys + raw id only, no message content) so an
            # unrecognized id format can be diagnosed and added to _waha_extract_id.
            _logger.warning(
                "WAHA send returned no usable message id (response keys=%s, id=%r)",
                list(response) if isinstance(response, dict) else type(response).__name__,
                response.get('id') if isinstance(response, dict) else None,
            )
            self._handle_error(failure_type='unknown', error_message='WAHA returned no message id')
            return
        self.write({'state': 'sent', 'msg_uid': msg_uid})
        if with_commit and not modules.module.current_test:
            self.env.cr.commit()

    def _resend_failed(self):
        """WAHA channels are always valid (no 24h window), so bypass the validity gate that
        would otherwise cancel WAHA messages as 'outdated_channel'."""
        waha = self.filtered(lambda m: m.wa_account_id.provider == 'waha')
        retry = waha.filtered(
            lambda m: m.state == 'error' and m.failure_type != 'whatsapp_unrecoverable')
        if retry:
            retry.write({'state': 'outgoing', 'failure_type': False, 'failure_reason': False})
            self.env.ref('whatsapp.ir_cron_send_whatsapp_queue')._trigger()
        others = self - waha
        if others:
            super(WhatsappMessage, others)._resend_failed()

    def _get_whatsapp_gc_domain(self):
        """Never GC WAHA messages: their msg_uid is the durable dedup key used by the
        webhook and by the history backfill's 'last 2 messages' check."""
        return super()._get_whatsapp_gc_domain() + [('wa_account_id.provider', '!=', 'waha')]
