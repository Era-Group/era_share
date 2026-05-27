"""Tests for the SEO audit runner (Phase 7 / SPEC §13)."""
import logging

from odoo.tests import TransactionCase, tagged

_logger = logging.getLogger(__name__)


@tagged('post_install', '-at_install')
class TestAuditRunner(TransactionCase):
    """Exercise the run + a handful of checks end-to-end."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env['website.page']
        cls.Run = cls.env['era.seo.audit.run']
        cls.Finding = cls.env['era.seo.audit.finding']

    def _make_page(self, url='/audit-test', arch_xml=None, **vals):
        view = self.env['ir.ui.view'].create({
            'name': 'audit test view',
            'type': 'qweb',
            'arch': arch_xml or '<div><h1>Title</h1><p>Body.</p></div>',
            'key': 'era_seo_manager.audit_test_view_{}'.format(url.replace('/', '_')),
        })
        vals.setdefault('view_id', view.id)
        vals['url'] = url
        return self.Page.create(vals)

    def test_run_state_transitions(self):
        run = self.Run.create({})
        self.assertEqual(run.state, 'draft')
        run._run_audit()
        self.assertIn(run.state, ('done', 'failed'))
        self.assertTrue(run.date_started)
        self.assertTrue(run.date_finished)

    def test_missing_title_creates_critical(self):
        page = self._make_page(url='/audit-title-missing')
        page.write({'seo_title': False, 'seo_description': 'desc'})
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ])
        self.assertTrue(f, 'missing seo_title must produce a finding')
        self.assertEqual(f[0].severity, 'critical')

    def test_title_too_long_warning(self):
        page = self._make_page(url='/audit-long-title')
        page.write({'seo_title': 'X' * 80, 'seo_description': 'd'})
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'title_too_long'),
        ])
        self.assertTrue(f)
        self.assertEqual(f[0].severity, 'warning')

    def test_duplicate_titles_critical(self):
        page_a = self._make_page(url='/audit-dup-a')
        page_b = self._make_page(url='/audit-dup-b')
        page_a.write({'seo_title': 'Same Title'})
        page_b.write({'seo_title': 'Same Title'})
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('check_code', '=', 'duplicate_seo_title'),
        ])
        self.assertGreaterEqual(len(f), 2)

    def test_noindex_in_sitemap_critical(self):
        page = self._make_page(url='/audit-noindex-sitemap')
        page.write({
            'seo_robots_index': False,
            'seo_sitemap_include': True,
        })
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'noindex_in_sitemap'),
        ])
        self.assertTrue(f)

    def test_slug_uppercase_info(self):
        page = self._make_page(url='/Audit-Slug-Caps')
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'slug_contains_uppercase'),
        ])
        self.assertTrue(f)

    def test_mark_resolved_flow(self):
        page = self._make_page(url='/audit-resolved-test')
        page.write({'seo_title': False})
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ], limit=1)
        self.assertTrue(f)
        self.assertFalse(f.is_resolved)
        f.action_mark_resolved()
        self.assertTrue(f.is_resolved)
        self.assertTrue(f.resolved_date)
        self.assertTrue(f.resolved_user_id)

    def test_action_open_target_returns_form_action(self):
        page = self._make_page(url='/audit-open-target-test')
        page.write({'seo_title': False})
        run = self.Run.create({})
        run._run_audit()
        f = self.Finding.search([
            ('run_id', '=', run.id),
            ('res_id', '=', page.id),
        ], limit=1)
        self.assertTrue(f)
        action = f.action_open_target()
        self.assertEqual(action['res_model'], 'website.page')
        self.assertEqual(action['res_id'], page.id)

    def test_per_language_description_detection(self):
        """A short description in one language is flagged even when another
        language's description is fine."""
        fr = self.env.ref('base.lang_fr')
        fr.active = True
        website = self.env['website'].search([], limit=1)
        website.language_ids = [(4, self.env.ref('base.lang_en').id), (4, fr.id)]
        page = self._make_page(url='/audit-perlang')
        page.website_id = website.id
        # English: fine (long). French: too short.
        page.with_context(lang='en_US').write({
            'seo_description': 'A perfectly adequate English meta description that '
                               'comfortably clears the seventy character minimum bar.'})
        page.with_context(lang='fr_FR').write({'seo_description': 'Trop court'})

        self.Run.create({})._run_audit()

        fr_finding = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'description_too_short'),
            ('lang_id', '=', fr.id),
        ])
        en_finding = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'description_too_short'),
            ('lang_id', '=', self.env.ref('base.lang_en').id),
        ])
        self.assertTrue(fr_finding, 'Short French description must be flagged.')
        self.assertFalse(en_finding, 'Adequate English description must NOT be flagged.')

    def test_rerun_does_not_duplicate_findings(self):
        """Two runs over the same defect yield ONE finding, not two."""
        page = self._make_page(url='/audit-no-dupe')
        page.write({'seo_title': False, 'seo_description': 'desc'})

        self.Run.create({})._run_audit()
        self.Run.create({})._run_audit()

        findings = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ])
        self.assertEqual(len(findings), 1, 'Re-running must upsert, not duplicate.')

    def test_rerun_updates_existing_in_place(self):
        page = self._make_page(url='/audit-update')
        page.write({'seo_title': False})
        run1 = self.Run.create({})
        run1._run_audit()
        f = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ])
        self.assertEqual(len(f), 1)
        first_id = f.id

        run2 = self.Run.create({})
        run2._run_audit()
        f2 = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ])
        self.assertEqual(len(f2), 1)
        self.assertEqual(f2.id, first_id, 'Same row reused across runs.')
        self.assertEqual(f2.run_id, run2, 'run_id re-points at the latest run.')

    def test_fixed_issue_auto_resolves(self):
        page = self._make_page(url='/audit-autoresolve')
        page.write({'seo_title': False, 'seo_description': 'd'})
        self.Run.create({})._run_audit()
        f = self.Finding.search([
            ('res_id', '=', page.id),
            ('check_code', '=', 'missing_seo_title'),
        ])
        self.assertTrue(f)
        self.assertFalse(f.is_resolved)

        # Fix the page, re-run -> the finding should auto-resolve.
        page.write({'seo_title': 'Now I have a proper SEO title here'})
        self.Run.create({})._run_audit()
        f.invalidate_recordset()
        self.assertTrue(f.is_resolved, 'A no-longer-detected issue must auto-resolve.')

    def test_reappearing_issue_reopens(self):
        page = self._make_page(url='/audit-reopen')
        page.write({'seo_title': False})
        self.Run.create({})._run_audit()
        f = self.Finding.search([
            ('res_id', '=', page.id), ('check_code', '=', 'missing_seo_title')])
        # Manually resolve it.
        f.action_mark_resolved()
        self.assertTrue(f.is_resolved)
        # Issue still present on next run -> reopen.
        self.Run.create({})._run_audit()
        f.invalidate_recordset()
        self.assertFalse(f.is_resolved, 'Still-present issue must reopen.')

    def test_counts_compute(self):
        page = self._make_page(url='/audit-counts-test')
        page.write({'seo_title': False, 'seo_description': False})
        run = self.Run.create({})
        run._run_audit()
        run.invalidate_recordset()
        self.assertEqual(
            run.total_count,
            run.critical_count + run.warning_count + run.info_count,
        )
