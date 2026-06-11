import logging
import re
import urllib.parse
from html import unescape

from lxml import html as lxml_html

from odoo import api, models

_logger = logging.getLogger(__name__)

# Schema template code for the FAQPage auto-derived from a page's content.
_PAGE_FAQ_SCHEMA_CODE = 'page_faq_page'

# ---------------------------------------------------------------------------
# ONDELETE PATTERN FOR POLYMORPHIC SCHEMA INSTANCES
# ---------------------------------------------------------------------------
# era.seo.schema.instance uses a polymorphic (res_model, res_id) reference
# instead of a real FK, so the DB cannot enforce CASCADE.  Every model that
# can host schema instances MUST override unlink() to delete its instances
# first.  Copy the _cleanup_schema_instances() helper below into new host
# models (Phase 5 blog.post, etc.) and call it from unlink().
# ---------------------------------------------------------------------------


class WebsitePage(models.Model):
    """Extend website.page with the ERA SEO mixin.

    Per SPEC §7.2: era.seo.mixin fields land on website.page here.
    Stock website.seo.metadata fields (website_meta_title, etc.) are kept
    intact; our seo_title is treated as authoritative when both are set.
    Sync happens via write() so that the stock QWeb rendering always picks up
    the ERA values without requiring template changes for description/keywords.
    """

    _name = 'website.page'
    _inherit = ['website.page', 'era.seo.mixin']

    # --- Canonical path override ----------------------------------------------

    def _get_seo_path(self):
        return self.url or '/'

    # --- Sync ERA → stock SEO fields -----------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            # Seed both directions: ERA wins when both are set, otherwise
            # whichever side carries data populates the other.
            rec._sync_era_to_stock()
            rec._sync_stock_to_era()
        # Phase 6: auto-attach hreflang entries on first creation.
        try:
            records._sync_era_hreflang_entries()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'website.page.create: hreflang sync skipped (%s)', exc,
            )
        # Auto-attach the FAQPage schema when the new page already carries an
        # FAQ accordion (e.g. a page cloned from an FAQ-bearing template).
        try:
            records._sync_era_faq_schema()
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                'website.page.create: FAQ schema sync skipped (%s)', exc,
            )
        return records

    # ERA SEO field  <->  stock website.seo.metadata field
    _ERA_TO_STOCK = {
        'seo_title': 'website_meta_title',
        'seo_description': 'website_meta_description',
        'seo_keywords': 'website_meta_keywords',
    }
    _STOCK_TO_ERA = {v: k for k, v in _ERA_TO_STOCK.items()}

    def write(self, vals):
        result = super().write(vals)
        if not self.env.context.get('_era_no_sync'):
            # Bidirectional, last-write-wins. If an ERA field was written
            # (our SEO tab / AI fill), mirror it into the stock website_meta_*
            # columns. If instead a stock field was written (the website
            # builder's "Optimize SEO" dialog), mirror it back into the ERA
            # fields — which is what the frontend actually renders, so the
            # dialog's edits now show up and aren't reverted by a later sync.
            if set(self._ERA_TO_STOCK).intersection(vals):
                for rec in self:
                    rec._sync_era_to_stock()
            elif set(self._STOCK_TO_ERA).intersection(vals):
                for rec in self:
                    rec._sync_stock_to_era()
        # Phase 6: refresh hreflang on URL/website/language-affecting changes.
        hreflang_keys = {'url', 'website_id'}
        if hreflang_keys.intersection(vals):
            try:
                self._sync_era_hreflang_entries()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'website.page.write: hreflang sync skipped (%s)', exc,
                )
        # Refresh the FAQPage schema when content changes through the page
        # record itself. Editor saves that go via ir.ui.view (the common case)
        # are handled by the ir.ui.view override.
        if any(k in vals for k in ('arch', 'arch_db', 'arch_base', 'view_id')):
            try:
                self._sync_era_faq_schema()
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'website.page.write: FAQ schema sync skipped (%s)', exc,
                )
        return result

    def _sync_era_to_stock(self):
        """Mirror ERA SEO fields into the delegated website_meta_* columns.

        Runs in the record's current language context, so per-language ERA
        values land in the matching website_meta_* translation. Skipped under
        the ``_era_no_sync`` guard (post_init_hook, and the reverse sync).
        """
        if self.env.context.get('_era_no_sync'):
            return
        update = {}
        for era_field, stock_field in self._ERA_TO_STOCK.items():
            value = self[era_field]
            if value and self[stock_field] != value:
                update[stock_field] = value
        if update:
            self.with_context(_era_no_sync=True).write(update)

    def _sync_stock_to_era(self):
        """Mirror stock website_meta_* edits (Optimize SEO dialog) into the
        authoritative ERA fields the frontend renders.

        Current-language context, so editing the dialog in Arabic updates the
        Arabic ERA translation. Guarded against recursion via _era_no_sync.
        """
        if self.env.context.get('_era_no_sync'):
            return
        update = {}
        for stock_field, era_field in self._STOCK_TO_ERA.items():
            value = self[stock_field]
            if value and self[era_field] != value:
                update[era_field] = value
        if update:
            self.with_context(_era_no_sync=True).write(update)

    # --- FAQPage schema (auto-derived live from page content) ----------------

    @staticmethod
    def _era_clean_text(text):
        """Decode HTML entities, then collapse all whitespace (including the
        resulting non-breaking spaces) to single spaces; '' for falsy input.

        A literal ``&nbsp;`` can survive in the stored arch (double-escaped
        content); unescaping keeps such entities out of the JSON-LD answer
        text so the structured data reads cleanly.
        """
        if not text:
            return ''
        return re.sub(r'\s+', ' ', unescape(text)).strip()

    def era_faq_main_entity(self):
        """Return this page's FAQ as a schema.org ``FAQPage.mainEntity`` list,
        parsed LIVE from the page content. Returns ``[]`` when the page has no
        FAQ accordion.

        Resolved at render time by the ``page_faq_page`` JSON-LD template
        (``{{ record.era_faq_main_entity | json }}``), so the structured data
        always reflects current content — including the active language — with
        nothing stored to go stale.

        Supports the Odoo ``s_faq_collapse`` snippet (``card-header`` /
        ``card-body``) and the Bootstrap ``accordion-item`` markup. Each
        question pairs with the answer inside the SAME accordion item, and only
        cards living inside an accordion / FAQ container are considered — so
        unrelated ``.card-body`` blocks elsewhere on the page (pricing, blog,
        team cards, …) are never mistaken for an answer. Never raises.
        """
        self.ensure_one()
        try:
            arch = self.arch or ''
            # Cheap pre-filter: skip the lxml parse entirely on the vast
            # majority of pages, which have no FAQ accordion markup.
            if 's_faq_collapse' not in arch and 'accordion' not in arch:
                return []
            tree = lxml_html.fromstring(arch)
        except Exception:  # noqa: BLE001
            return []

        def _in_faq_context(node):
            anc = node.getparent()
            while anc is not None:
                cls = anc.get('class') or ''
                if 'accordion' in cls or 's_faq_collapse' in cls:
                    return True
                anc = anc.getparent()
            return False

        def _first_text(item, classes):
            for cls in classes:
                found = item.find_class(cls)
                if found:
                    return self._era_clean_text(found[0].text_content())
            return ''

        items = tree.find_class('accordion-item') + [
            card for card in tree.find_class('card') if _in_faq_context(card)
        ]
        main_entity, seen = [], set()
        for item in items:
            question = _first_text(item, ['accordion-button', 'card-header'])
            answer = _first_text(item, ['accordion-body', 'card-body'])
            if (question and answer and len(question) > 2 and len(answer) > 2
                    and question not in seen):
                seen.add(question)
                main_entity.append({
                    '@type': 'Question',
                    'name': question,
                    'acceptedAnswer': {'@type': 'Answer', 'text': answer},
                })
        return main_entity

    def _sync_era_faq_schema(self):
        """Attach a ``page_faq_page`` schema instance to pages whose content
        has an FAQ accordion; remove it from pages that no longer do.

        Idempotent and side-effect-safe. The instance stores no ``data_json``
        — the Q&A pairs are resolved live by the template from
        :meth:`era_faq_main_entity`, so there is nothing to keep in sync beyond
        the instance's existence.
        """
        if 'era.seo.schema.instance' not in self.env:
            return
        Template = self.env['era.seo.schema.template'].sudo()
        Instance = self.env['era.seo.schema.instance'].sudo()
        faq_tpl = Template.search([('code', '=', _PAGE_FAQ_SCHEMA_CODE)], limit=1)
        if not faq_tpl:
            return
        for page in self.sudo():
            if not page.id:
                continue
            try:
                has_faq = bool(page.era_faq_main_entity())
            except Exception:  # noqa: BLE001
                has_faq = False
            existing = Instance.search([
                ('res_model', '=', page._name),
                ('res_id', '=', page.id),
                ('template_id', '=', faq_tpl.id),
            ], limit=1)
            if has_faq and not existing:
                Instance.create({
                    'template_id': faq_tpl.id,
                    'res_model': page._name,
                    'res_id': page.id,
                    'active': True,
                })
            elif existing and not has_faq:
                existing.unlink()

    # --- Enrich OG / Twitter via stock get_website_meta() override -----------

    def _default_website_meta(self):
        """Inject ERA OG and Twitter field values into the stock meta defaults.

        The stock QWeb template renders OG / Twitter from the dict returned
        by get_website_meta(), which calls _default_website_meta() internally.
        Overriding here is the correct Odoo hook per the docstring of the
        stock method in website.seo.metadata.
        """
        result = super()._default_website_meta()
        og = result.get('default_opengraph', {})
        tw = result.get('default_twitter', {})

        effective_title = self.seo_og_title or self.seo_title
        if effective_title:
            og['og:title'] = effective_title
            tw['twitter:title'] = effective_title

        effective_desc = self.seo_og_description or self.seo_description
        if effective_desc:
            og['og:description'] = effective_desc
            tw['twitter:description'] = effective_desc

        if self.seo_og_type:
            og['og:type'] = self.seo_og_type

        if self.seo_og_image_url:
            og['og:image'] = self.seo_og_image_url
            tw['twitter:image'] = self.seo_og_image_url

        if self.seo_twitter_card:
            tw['twitter:card'] = self.seo_twitter_card

        if self.seo_twitter_site:
            tw['twitter:site'] = self.seo_twitter_site

        if self.seo_twitter_creator:
            tw['twitter:creator'] = self.seo_twitter_creator

        result['default_opengraph'] = og
        result['default_twitter'] = tw
        return result

    # --- Schema instance cleanup on page delete ------------------------------

    def _cleanup_schema_instances(self):
        """Delete all era.seo.schema.instance records for these pages.

        Called from unlink() before the page records are removed.
        Silently skips if the model is not installed (e.g. during a partial
        test run where era.seo.schema.instance does not yet exist).
        """
        if 'era.seo.schema.instance' not in self.env:
            return
        instances = self.env['era.seo.schema.instance'].sudo().search([
            ('res_model', '=', self._name),
            ('res_id', 'in', self.ids),
        ])
        if instances:
            _logger.debug(
                'website_page.unlink: removing %d schema instance(s) for ids %s',
                len(instances), self.ids,
            )
            instances.unlink()

    def _cleanup_audit_findings(self):
        """Delete audit findings targeting these pages.

        Findings use a polymorphic (res_model, res_id) reference with no
        DB-level CASCADE, so deleting a page would otherwise leave orphaned
        findings whose target record has vanished — the AI 'Suggest Fix' then
        errors with 'target record vanished'. Cleaning them here keeps the
        Findings list in step with the pages that actually exist.
        """
        if 'era.seo.audit.finding' not in self.env:
            return
        findings = self.env['era.seo.audit.finding'].sudo().search([
            ('res_model', '=', self._name),
            ('res_id', 'in', self.ids),
        ])
        if findings:
            findings.unlink()

    def unlink(self):
        """Override to cascade-delete schema instances + hreflang + findings.

        Per SPEC §8 Step 2 / §12: polymorphic FKs have no DB-level CASCADE,
        so we clean up schema instances, hreflang rows and audit findings here.
        See the ONDELETE PATTERN comment at the top of this file for how to
        replicate this in other host models.
        """
        self._cleanup_schema_instances()
        self._cleanup_era_hreflang()
        self._cleanup_audit_findings()
        return super().unlink()

    # --- Validation helpers --------------------------------------------------

    def action_open_rich_results_test(self):
        """Open Google Rich Results Test pre-filled with this page's URL.

        Returns an ir.actions.act_url so the browser opens a new tab.
        Per SPEC §8 Step 7 (Validate JSON-LD button).
        """
        self.ensure_one()
        base = self.website_id.domain or ''
        base = base.rstrip('/')
        page_url = base + (self.url or '/')
        test_url = (
            'https://search.google.com/test/rich-results?url='
            + urllib.parse.quote(page_url, safe='')
        )
        return {
            'type': 'ir.actions.act_url',
            'url': test_url,
            'target': 'new',
        }
