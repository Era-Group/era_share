"""The Saudi MOJ legislation corpus, kept attached to the research agent as sources.

The research agent answers only from its sources, so it is useless -- and, under
the module's own `_check_sources_present`, un-approvable -- until the statute texts
are attached to it. Uploading seventy laws by hand, and re-uploading one every time
it is amended, is not a thing anyone keeps doing. So the corpus is synced instead.

This is not the hand-kept `legal.legislation` register that was removed. Nothing
here is typed by a person: every row is written by the sync from what the Ministry
publishes, and every row is backed by a real source the agent has actually read.
The row exists so the sync can tell what it already has, re-attach only what has
changed, and show a lawyer which laws are in front of the agent and when.

Corpus source: the scraper-moj pipeline, published unauthenticated at
https://moj-laws.era.net.sa (summary.json, articles.jsonl). Odoo's own embedding
engine indexes and retrieves what is attached; nothing here re-implements that.
"""

import hashlib
import json
import logging

import requests

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

ENDPOINT_PARAM = 'era_law_firm_ai.moj_endpoint'
LAST_SCRAPED_PARAM = 'era_law_firm_ai.moj_last_scraped_at'
DEFAULT_ENDPOINT = 'https://moj-laws.era.net.sa'
FETCH_TIMEOUT = 60


class MojLaw(models.Model):
    _name = 'moj.law'
    _description = 'MOJ Statute Attached as a Source'
    _order = 'name'

    law_id = fields.Char(
        string='MoJ Statute ID', required=True, index=True,
        help="The identifier the Ministry's portal uses for this statute. The sync matches on it, "
             "so an amended statute updates its row instead of creating a second one.")
    name = fields.Char(string='Statute', required=True)
    law_type = fields.Char(string='Type')
    status = fields.Char(
        string='Legal Status',
        help="Whether the statute is in force or repealed, as the Ministry reports it. Carried so a "
             "repealed law is visibly repealed rather than silently retrieved as if current.")
    article_count = fields.Integer(string='Articles')
    content_hash = fields.Char(
        string='Content Hash', help="SHA-256 of the rendered statute text. The sync re-attaches a "
             "statute only when this changes, so an unchanged law is not re-embedded every week.")
    last_synced = fields.Datetime(string='Last Synced', readonly=True)
    source_ids = fields.Many2many(
        'ai.agent.source', 'moj_law_source_rel', 'law_id', 'source_id',
        string='Attached Sources', copy=False,
        help="The ai.agent.source rows this statute is attached through, one per target agent.")
    source_url = fields.Char(string='Official URL')

    _law_unique = models.Constraint('UNIQUE(law_id)', 'This statute is already tracked.')

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    @api.model
    def _endpoint(self):
        base = self.env['ir.config_parameter'].sudo().get_param(ENDPOINT_PARAM, DEFAULT_ENDPOINT)
        return (base or DEFAULT_ENDPOINT).rstrip('/')

    @api.model
    def _target_agents(self):
        return self.env['ai.agent'].sudo().search([('moj_corpus_target', '=', True)])

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------
    @api.model
    def _fetch_json(self, path):
        response = requests.get(f'{self._endpoint()}/{path}', timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        return response.json()

    @api.model
    def _fetch_jsonl(self, path):
        response = requests.get(f'{self._endpoint()}/{path}', timeout=FETCH_TIMEOUT)
        response.raise_for_status()
        rows = []
        for line in response.text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    @api.model
    def _render_law_text(self, meta, articles):
        """One statute as a single plain-text document.

        Odoo chunks the attachment itself and collapses whitespace when it does, so
        the statute name and article number are put inline at the start of every
        article rather than left to line breaks -- a retrieved chunk then still
        carries what it takes to cite it, even after the newlines are gone.
        """
        name = meta.get('law_name') or ''
        header = _('%(name)s — النوع: %(type)s — الحالة: %(status)s',
                   name=name,
                   type=meta.get('law_type') or _('غير محدد'),
                   status=meta.get('law_status') or _('غير محدد'))
        blocks = [header]
        current_chapter = None
        for article in articles:
            chapter = article.get('chapter')
            if chapter and chapter != current_chapter:
                current_chapter = chapter
                blocks.append(f'== {chapter} ==')
            label = article.get('article_label') or ''
            repealed = _(' (مادة ملغاة)') if article.get('is_cancelled') else ''
            text = (article.get('text') or '').strip()
            blocks.append(f'«{name}» — {label}{repealed}: {text}')
        return '\n\n'.join(blocks)

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    @api.model
    def _run_corpus_sync(self, force=False):
        """Fetch the corpus and reconcile every target agent's sources with it.

        Called weekly by cron, and directly (force=True) after an agent is newly
        flagged. Returns a short summary string for the caller/log.
        """
        icp = self.env['ir.config_parameter'].sudo()
        summary = self._fetch_json('summary.json')
        scraped_at = summary.get('scraped_at')
        last_scraped = icp.get_param(LAST_SCRAPED_PARAM)

        if not force and scraped_at and scraped_at == last_scraped:
            _logger.info('era_law_firm_ai: MOJ corpus unchanged (scraped_at=%s), skipping.', scraped_at)
            return 'unchanged'

        agents = self._target_agents()
        if not agents:
            _logger.warning('era_law_firm_ai: no agent is flagged to carry the MOJ corpus; nothing to attach.')
            return 'no-target'

        articles = self._fetch_jsonl('articles.jsonl')
        grouped = self._group_articles(articles)
        _logger.info('era_law_firm_ai: syncing %d statutes onto %d agent(s).', len(grouped), len(agents))

        synced = 0
        for law_id, bundle in grouped.items():
            if self._sync_one_law(law_id, bundle['meta'], bundle['articles'], agents):
                synced += 1

        self._retire_missing_laws(set(grouped.keys()))

        if scraped_at:
            icp.set_param(LAST_SCRAPED_PARAM, scraped_at)
        _logger.info('era_law_firm_ai: MOJ corpus sync done (%d of %d statutes (re)attached).',
                     synced, len(grouped))
        return f'{synced}/{len(grouped)} (re)attached'

    @api.model
    def _group_articles(self, articles):
        """{law_id: {'meta': {...}, 'articles': [... ordered]}}."""
        grouped = {}
        for article in articles:
            law_id = article.get('law_id')
            if not law_id:
                continue
            bundle = grouped.setdefault(law_id, {'meta': {}, 'articles': []})
            bundle['articles'].append(article)
            if not bundle['meta']:
                bundle['meta'] = {
                    'law_name': article.get('law_name'),
                    'law_type': article.get('law_type'),
                    'law_status': article.get('law_status'),
                    'source_url': article.get('source_url'),
                }
        for bundle in grouped.values():
            bundle['articles'].sort(key=lambda a: a.get('order_index') or 0)
        return grouped

    def _sync_one_law(self, law_id, meta, articles, agents):
        """Ensure each target agent carries this statute's current text. Returns
        True if anything was (re)attached, False if every agent was already current."""
        text = self._render_law_text(meta, articles)
        content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

        law = self.search([('law_id', '=', law_id)], limit=1)
        # captured before the write below, so the per-agent freshness test can tell
        # whether this statute's text actually changed since it was last attached.
        old_hash = law.content_hash if law else None
        vals = {
            'law_id': law_id,
            'name': meta.get('law_name') or law_id,
            'law_type': meta.get('law_type'),
            'status': meta.get('law_status'),
            'source_url': meta.get('source_url'),
            'article_count': len(articles),
            'content_hash': content_hash,
            'last_synced': fields.Datetime.now(),
        }
        if law:
            law.write(vals)
        else:
            law = self.create(vals)

        changed = False
        raw = text.encode('utf-8')
        text_unchanged = old_hash == content_hash
        for agent in agents:
            existing = law.source_ids.filtered(lambda s: s.agent_id == agent)
            healthy = existing and all(s.status in ('processing', 'indexed') for s in existing)
            if healthy and text_unchanged:
                # this agent already carries the current text; don't re-embed it
                continue
            if existing:
                existing.unlink()
            new_source = self.env['ai.agent.source'].sudo().create_from_binary_files(
                [{
                    'name': vals['name'],
                    'raw': raw,
                    'mimetype': 'text/plain',
                }],
                agent.id,
            )
            law.source_ids = [(4, sid) for sid in new_source.ids]
            changed = True
        return changed

    def _retire_missing_laws(self, current_ids):
        """A statute the Ministry no longer lists is detached and its row dropped,
        so the agent stops retrieving text that is no longer published."""
        stale = self.search([('law_id', 'not in', list(current_ids))])
        if not stale:
            return
        _logger.info('era_law_firm_ai: retiring %d statute(s) no longer published.', len(stale))
        stale.source_ids.unlink()
        stale.unlink()

    def action_open_sources(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Attached Sources'),
            'res_model': 'ai.agent.source',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.source_ids.ids)],
        }


class AIAgentMojCorpus(models.Model):
    _inherit = 'ai.agent'

    moj_corpus_target = fields.Boolean(
        string='Carries the MOJ Legislation Corpus', copy=False,
        help="When set, the scheduled sync keeps the full Saudi MOJ legislation corpus attached to "
             "this agent as sources, so it can cite the statute text instead of recalling it. Set it "
             "on an agent that answers only from its sources.")
    moj_corpus_law_count = fields.Integer(
        string='Statutes Attached', compute='_compute_moj_corpus_law_count',
        help="How many statutes the sync currently has attached to this agent as sources.")

    def _compute_moj_corpus_law_count(self):
        Law = self.env['moj.law']
        for agent in self:
            agent.moj_corpus_law_count = Law.search_count([
                ('source_ids.agent_id', '=', agent.id),
            ]) if agent.moj_corpus_target else 0

    def action_resync_moj_corpus(self):
        """Force a full resync onto the target agents now, ignoring the unchanged gate.

        Used after flagging a new agent: the scheduled sync skips fetching when the
        corpus has not changed since last run, which would leave a freshly flagged
        agent without the sources the others already have.
        """
        self.env['moj.law']._run_corpus_sync(force=True)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('MOJ legislation corpus resynced.'),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
