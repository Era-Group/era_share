# -*- coding: utf-8 -*-
import logging
import re

from odoo import api, fields, models, _

from .ai_utils import extract_json_object as _cs_extract_json

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    cs_timeline_synced = fields.Boolean(
        string='CS Timeline Synced', default=False, copy=False, index=True)

    # --- AI sentiment ---
    cs_sentiment_label = fields.Selection([
        ('positive', 'Positive'),
        ('neutral', 'Neutral'),
        ('negative', 'Negative'),
    ], string='Sentiment', readonly=True, copy=False)
    cs_sentiment_score = fields.Integer(string='Sentiment Score', readonly=True, copy=False)
    cs_sentiment_reason = fields.Char(string='Sentiment Reason', readonly=True, copy=False)
    cs_sentiment_analyzed = fields.Boolean(
        string='Sentiment Analyzed', default=False, copy=False, index=True)

    def _cs_sentiment_text(self):
        """Build the text fed to the sentiment agent."""
        self.ensure_one()
        parts = [self.name or '']
        parts.append(re.sub(r'<[^>]+>', ' ', self.description or ''))
        msgs = self.message_ids.filtered(
            lambda m: m.message_type in ('email', 'comment') and m.body)[:5]
        for m in msgs:
            parts.append(re.sub(r'<[^>]+>', ' ', m.body or ''))
        text = ' '.join(p.strip() for p in parts if p and p.strip())
        return text[:4000]

    def _cs_analyze_sentiment(self):
        """Call the AI agent to score sentiment on each ticket. Guarded & idempotent."""
        agent = self.env.ref('era_customer_success.cs_sentiment_agent', raise_if_not_found=False)
        if not agent:
            return
        root = self.env.ref('base.user_root')
        # Commit per ticket so a single slow/hanging AI call cannot roll back the
        # whole batch on a cron timeout (each analysed ticket stays durable).
        # Skip commits under a test cursor to preserve test transaction isolation.
        autocommit = type(self.env.cr).__name__ != 'TestCursor'
        for tk in self:
            if tk.cs_sentiment_analyzed:
                continue
            text = tk._cs_sentiment_text()
            if len(text) < 20:
                tk.cs_sentiment_analyzed = True
                if autocommit:
                    tk.env.cr.commit()
                continue
            try:
                response = agent.with_user(root).get_direct_response(prompt=text)
                raw = response[0] if response else ''
                data = _cs_extract_json(raw)
            except Exception as e:
                # handled transient AI/transport errors (e.g. CLI timeout) -> retried next cron
                _logger.warning("CS sentiment analysis skipped for ticket %s (AI error): %s", tk.id, e)
                continue
            if not isinstance(data, dict):
                tk.cs_sentiment_analyzed = True
                if autocommit:
                    tk.env.cr.commit()
                continue
            label = (data.get('sentiment') or '').lower()
            if label not in ('positive', 'neutral', 'negative'):
                label = False
            try:
                score = int(data.get('score') or 0)
            except (TypeError, ValueError):
                score = 0
            tk.write({
                'cs_sentiment_label': label or False,
                'cs_sentiment_score': max(-100, min(100, score)),
                'cs_sentiment_reason': (data.get('reason') or '')[:255] or False,
                'cs_sentiment_analyzed': True,
            })
            if label == 'negative':
                tk._cs_sentiment_escalate()
            if autocommit:
                tk.env.cr.commit()

    def _cs_sentiment_escalate(self):
        """Negative sentiment -> one Service Recovery activity per account.

        The latest sentiment is surfaced on the account form (sentiment badge/score),
        so we no longer post a chatter note per ticket per cron run — that flooded the
        timeline with one message for every negative ticket on every analysis run.
        """
        self.ensure_one()
        account = self.env['cs.account']._account_for_partner(self.partner_id)
        if not account:
            return
        if account.csm_user_id:
            # Cap at one open service-recovery activity per account — do NOT create a
            # separate task for every negative-sentiment ticket (that floods the CSM).
            # Match an existing one by the translated summary prefix (before the ticket
            # name slot), so it works regardless of the user's language.
            marker = _('Service recovery – negative sentiment on ticket %s', '\x00').split('\x00')[0]
            if account.activity_ids.filtered(lambda a: a.summary and a.summary.startswith(marker)):
                return
            account._auto_activity_schedule(
                act_type_xmlid='mail.mail_activity_data_call',
                summary=_('Service recovery – negative sentiment on ticket %s', self.name),
                user_id=account.csm_user_id.id)
