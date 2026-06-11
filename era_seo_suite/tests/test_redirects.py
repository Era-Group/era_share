"""Tests for the redirect manager (Phase 3 / SPEC §9).

Covers:
  - Plain (non-regex) match, scope priority, hit-counter
  - Regex match with backreference substitution
  - 410 Gone
  - Path normalization (query/fragment stripping)
  - Constraint enforcement (no self-loop, required target, regex validity)
  - 404 log upsert and resolution flow
  - CSV import dry-run + actual run
"""
import base64

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestRedirectModel(TransactionCase):
    """Pure-ORM tests on era.seo.redirect (no HTTP)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Redirect = cls.env['era.seo.redirect']
        cls.Website = cls.env['website']
        cls.website = cls.Website.search([], limit=1)

    # --- Plain match ----------------------------------------------------------

    def test_plain_match_returns_target(self):
        r = self.Redirect.create({
            'source_url': '/old-page',
            'target_url': '/new-page',
            'redirect_type': '301',
        })
        match, target = self.Redirect._find_match('/old-page')
        self.assertEqual(match, r)
        self.assertEqual(target, '/new-page')

    def test_plain_miss_returns_none(self):
        self.Redirect.create({
            'source_url': '/foo',
            'target_url': '/bar',
            'redirect_type': '301',
        })
        match, target = self.Redirect._find_match('/something-else')
        self.assertFalse(match)
        self.assertIsNone(target)

    def test_inactive_redirect_not_matched(self):
        self.Redirect.create({
            'source_url': '/inactive',
            'target_url': '/somewhere',
            'redirect_type': '301',
            'is_active': False,
        })
        match, _t = self.Redirect._find_match('/inactive')
        self.assertFalse(match)

    def test_register_hit_increments_count(self):
        r = self.Redirect.create({
            'source_url': '/hit-test',
            'target_url': '/target',
            'redirect_type': '301',
        })
        self.assertEqual(r.hit_count, 0)
        r._register_hit()
        r.invalidate_recordset()
        self.assertEqual(r.hit_count, 1)
        r._register_hit()
        r.invalidate_recordset()
        self.assertEqual(r.hit_count, 2)
        self.assertTrue(r.last_hit_date)

    # --- Scope priority -------------------------------------------------------

    def test_website_scoped_beats_global(self):
        """Same source path, two rules: website-scoped wins for that website."""
        global_rule = self.Redirect.create({
            'source_url': '/scoped',
            'target_url': '/global-target',
            'redirect_type': '301',
        })
        scoped_rule = self.Redirect.create({
            'source_url': '/scoped',
            'target_url': '/website-target',
            'redirect_type': '301',
            'website_id': self.website.id,
        })
        match, target = self.Redirect._find_match('/scoped', website=self.website)
        self.assertEqual(match, scoped_rule)
        self.assertEqual(target, '/website-target')
        # The global rule still wins for sites without a scoped override.
        match2, target2 = self.Redirect._find_match('/scoped', website=None)
        self.assertEqual(match2, global_rule)
        self.assertEqual(target2, '/global-target')

    # --- Regex match ----------------------------------------------------------

    def test_regex_match_with_backreference(self):
        self.Redirect.create({
            'source_url': r'^/blog/old/(.+)$',
            'target_url': r'/articles/\1',
            'redirect_type': '301',
            'is_regex': True,
        })
        match, target = self.Redirect._find_match('/blog/old/hello-world')
        self.assertTrue(match)
        self.assertEqual(target, '/articles/hello-world')

    def test_regex_partial_does_not_match(self):
        """re.fullmatch is used — a partial prefix match should not fire."""
        self.Redirect.create({
            'source_url': r'^/api$',
            'target_url': '/new-api',
            'redirect_type': '301',
            'is_regex': True,
        })
        match, _t = self.Redirect._find_match('/api/v2')
        self.assertFalse(match)

    def test_plain_match_wins_over_regex(self):
        """If both a plain and a regex rule match, the plain one wins."""
        plain = self.Redirect.create({
            'source_url': '/page',
            'target_url': '/plain-target',
            'redirect_type': '301',
        })
        self.Redirect.create({
            'source_url': r'^/page$',
            'target_url': '/regex-target',
            'redirect_type': '301',
            'is_regex': True,
        })
        match, target = self.Redirect._find_match('/page')
        self.assertEqual(match, plain)
        self.assertEqual(target, '/plain-target')

    # --- 410 Gone -------------------------------------------------------------

    def test_410_match_returns_none_target(self):
        gone = self.Redirect.create({
            'source_url': '/gone-page',
            'redirect_type': '410',
        })
        match, target = self.Redirect._find_match('/gone-page')
        self.assertEqual(match, gone)
        self.assertIsNone(target)

    # --- Path normalization ---------------------------------------------------

    def test_normalize_path_strips_query_and_fragment(self):
        self.assertEqual(self.Redirect._normalize_path('/p?x=1'), '/p')
        self.assertEqual(self.Redirect._normalize_path('/p#frag'), '/p')
        self.assertEqual(self.Redirect._normalize_path('/p?x=1#frag'), '/p')

    def test_normalize_path_handles_absolute_url(self):
        normalized = self.Redirect._normalize_path('https://example.com/foo')
        self.assertEqual(normalized, '/foo')

    def test_normalize_path_adds_leading_slash(self):
        self.assertEqual(self.Redirect._normalize_path('foo'), '/foo')

    def test_normalize_path_empty_returns_root(self):
        self.assertEqual(self.Redirect._normalize_path(''), '/')

    # --- Constraints ----------------------------------------------------------

    def test_target_required_for_301(self):
        with self.assertRaises(ValidationError):
            self.Redirect.create({
                'source_url': '/no-target',
                'redirect_type': '301',
                # target_url omitted
            })

    def test_target_not_required_for_410(self):
        r = self.Redirect.create({
            'source_url': '/gone',
            'redirect_type': '410',
        })
        self.assertTrue(r.id)

    def test_self_loop_rejected(self):
        with self.assertRaises(ValidationError):
            self.Redirect.create({
                'source_url': '/same',
                'target_url': '/same',
                'redirect_type': '301',
            })

    def test_invalid_regex_rejected(self):
        with self.assertRaises(ValidationError):
            self.Redirect.create({
                'source_url': r'^/[invalid(',
                'target_url': '/foo',
                'redirect_type': '301',
                'is_regex': True,
            })

    def test_plain_source_must_start_with_slash(self):
        with self.assertRaises(ValidationError):
            self.Redirect.create({
                'source_url': 'no-slash',
                'target_url': '/foo',
                'redirect_type': '301',
            })

    # --- Display name ---------------------------------------------------------

    def test_name_computed(self):
        r = self.Redirect.create({
            'source_url': '/a',
            'target_url': '/b',
            'redirect_type': '301',
        })
        self.assertIn('/a', r.name)
        self.assertIn('/b', r.name)
        self.assertIn('301', r.name)

    def test_name_for_410_uses_gone_marker(self):
        r = self.Redirect.create({
            'source_url': '/gone-name-test',
            'redirect_type': '410',
        })
        self.assertIn('410', r.name)
        self.assertIn('/gone-name-test', r.name)


@tagged('post_install', '-at_install')
class TestRedirectLog(TransactionCase):
    """Tests for the 404 log upsert + vacuum."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env['era.seo.redirect.log']

    def test_record_miss_creates_row(self):
        self.Log._record_miss('/missing-1')
        row = self.Log.search([('path', '=', '/missing-1')], limit=1)
        self.assertTrue(row)
        self.assertEqual(row.hit_count, 1)

    def test_record_miss_increments_existing(self):
        self.Log._record_miss('/missing-2')
        self.Log._record_miss('/missing-2')
        self.Log._record_miss('/missing-2')
        rows = self.Log.search([('path', '=', '/missing-2')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.hit_count, 3)

    def test_self_referer_dropped(self):
        """A referer ending with the page itself is not stored."""
        self.Log._record_miss(
            '/missing-3',
            referer='https://example.com/missing-3',
        )
        row = self.Log.search([('path', '=', '/missing-3')], limit=1)
        self.assertFalse(row.last_referer)

    def test_external_referer_kept(self):
        self.Log._record_miss(
            '/missing-4',
            referer='https://google.com/search?q=foo',
        )
        row = self.Log.search([('path', '=', '/missing-4')], limit=1)
        self.assertIn('google.com', row.last_referer or '')

    def test_action_create_redirect_returns_form_action(self):
        self.Log._record_miss('/missing-5')
        row = self.Log.search([('path', '=', '/missing-5')], limit=1)
        action = row.action_create_redirect()
        self.assertEqual(action['res_model'], 'era.seo.redirect')
        self.assertEqual(action['context'].get('default_source_url'), '/missing-5')
        self.assertEqual(action['context'].get('default_created_from'), 'auto_404')


@tagged('post_install', '-at_install')
class TestRedirectImportWizard(TransactionCase):
    """CSV import wizard: dry-run, real run, error handling."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Wizard = cls.env['era.seo.redirect.import.wizard']
        cls.Redirect = cls.env['era.seo.redirect']
        # action_import enforces the SEO Manager group server-side;
        # grant it the same way real import users get it.
        cls.env.user.write({'group_ids': [(4, cls.env.ref(
            'era_seo_suite.group_era_seo_manager').id)]})

    @staticmethod
    def _encode(csv_text):
        return base64.b64encode(csv_text.encode('utf-8'))

    def test_dry_run_does_not_write(self):
        before = self.Redirect.search_count([])
        wiz = self.Wizard.create({
            'data_file': self._encode('source,target\n/dry,/run\n'),
            'filename': 'test.csv',
            'dry_run': True,
        })
        wiz.action_import()
        self.assertEqual(wiz.state, 'done')
        self.assertEqual(self.Redirect.search_count([]), before,
                         'Dry run must not create records.')
        self.assertIn('Dry run', wiz.report)

    def test_actual_run_creates_records(self):
        wiz = self.Wizard.create({
            'data_file': self._encode(
                'source,target,type\n'
                '/import-a,/new-a,301\n'
                '/import-b,/new-b,302\n'
            ),
            'filename': 'test.csv',
            'dry_run': False,
        })
        wiz.action_import()
        self.assertEqual(wiz.created_count, 2)
        a = self.Redirect.search([('source_url', '=', '/import-a')], limit=1)
        b = self.Redirect.search([('source_url', '=', '/import-b')], limit=1)
        self.assertEqual(a.redirect_type, '301')
        self.assertEqual(b.redirect_type, '302')
        self.assertEqual(a.created_from, 'import')

    def test_idempotent_re_import_updates(self):
        """Importing the same source twice updates rather than duplicates."""
        wiz1 = self.Wizard.create({
            'data_file': self._encode('source,target\n/upsert,/first\n'),
            'filename': 'test.csv',
            'dry_run': False,
        })
        wiz1.action_import()
        wiz2 = self.Wizard.create({
            'data_file': self._encode('source,target\n/upsert,/second\n'),
            'filename': 'test.csv',
            'dry_run': False,
        })
        wiz2.action_import()
        recs = self.Redirect.search([('source_url', '=', '/upsert')])
        self.assertEqual(len(recs), 1, 'Re-import should upsert, not duplicate.')
        self.assertEqual(recs.target_url, '/second')
        self.assertEqual(wiz2.updated_count, 1)

    def test_missing_required_column_reports_error(self):
        wiz = self.Wizard.create({
            'data_file': self._encode('foo,bar\n/x,/y\n'),
            'filename': 'test.csv',
        })
        wiz.action_import()
        self.assertGreater(wiz.error_count, 0)
        self.assertIn('Missing required column', wiz.report)

    def test_invalid_type_flagged(self):
        wiz = self.Wizard.create({
            'data_file': self._encode(
                'source,target,type\n/bad,/y,999\n'
            ),
            'filename': 'test.csv',
            'dry_run': True,
        })
        wiz.action_import()
        self.assertGreater(wiz.error_count, 0)
        self.assertIn('999', wiz.report)


@tagged('post_install', '-at_install')
class TestRedirectDispatch(TransactionCase):
    """End-to-end-ish: simulate an HTTP path resolution by calling the hook helpers.

    Avoids a full HttpCase because dispatching a real 404 through Werkzeug
    requires more setup than these tests need.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Redirect = cls.env['era.seo.redirect']
        cls.Log = cls.env['era.seo.redirect.log']

    def test_dispatch_path_to_target_via_find_match(self):
        """Sanity check covering the helper combo the ir.http hook uses."""
        self.Redirect.create({
            'source_url': '/dispatch-test',
            'target_url': '/dispatch-target',
            'redirect_type': '301',
        })
        normalized = self.Redirect._normalize_path('/dispatch-test?utm=1')
        rule, target = self.Redirect._find_match(normalized)
        self.assertTrue(rule)
        self.assertEqual(target, '/dispatch-target')


@tagged('post_install', '-at_install')
class TestRedirectPolish(TransactionCase):
    """Phase 3 polish: query string forwarding, lang-prefix, trailing slash,
    system-path skip-list. Tests the hook helper classmethods directly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.era_seo_suite.models.ir_http import (
            IrHttp,
            _SYSTEM_PATH_PREFIXES,
        )
        cls.IrHttp = IrHttp
        cls.system_prefixes = _SYSTEM_PATH_PREFIXES
        cls.Redirect = cls.env['era.seo.redirect']

    # --- System path skip-list ------------------------------------------------

    def test_system_paths_are_skipped(self):
        self.assertTrue(self.IrHttp._era_is_system_path('/web/login'))
        self.assertTrue(self.IrHttp._era_is_system_path('/my/orders'))
        self.assertTrue(self.IrHttp._era_is_system_path('/static/anything'))
        self.assertTrue(self.IrHttp._era_is_system_path('/website/static/foo'))
        self.assertTrue(self.IrHttp._era_is_system_path('/_health'))

    def test_non_system_paths_pass_through(self):
        self.assertFalse(self.IrHttp._era_is_system_path('/old-page'))
        self.assertFalse(self.IrHttp._era_is_system_path('/blog/article'))
        self.assertFalse(self.IrHttp._era_is_system_path('/web-portal'))
        self.assertFalse(self.IrHttp._era_is_system_path('/myaccount'))
        self.assertFalse(self.IrHttp._era_is_system_path('/'))

    def test_system_prefixes_list_covers_admin_surfaces(self):
        """Sanity guard: don't accidentally drop /web/ from the skip-list."""
        self.assertIn('/web/', self.system_prefixes)
        self.assertIn('/my/', self.system_prefixes)
        self.assertIn('/odoo/', self.system_prefixes)

    # --- Trailing slash toggle ------------------------------------------------

    def test_toggle_adds_slash(self):
        self.assertEqual(self.IrHttp._era_toggle_trailing_slash('/foo'), '/foo/')

    def test_toggle_removes_slash(self):
        self.assertEqual(self.IrHttp._era_toggle_trailing_slash('/foo/'), '/foo')

    def test_toggle_root_returns_none(self):
        self.assertIsNone(self.IrHttp._era_toggle_trailing_slash('/'))

    def test_trailing_slash_lookup_matches_either_form(self):
        """A rule for /foo also resolves a request for /foo/ (and vice versa)."""
        self.Redirect.create({
            'source_url': '/foo',
            'target_url': '/bar',
            'redirect_type': '301',
        })
        # Direct match on /foo
        rule, _ = self.Redirect._find_match('/foo')
        self.assertTrue(rule)
        # /foo/ does not match the same plain rule directly...
        rule_slash, _ = self.Redirect._find_match('/foo/')
        self.assertFalse(rule_slash)
        # ...but the hook's toggle helper produces the alternate form.
        alt = self.IrHttp._era_toggle_trailing_slash('/foo/')
        self.assertEqual(alt, '/foo')
        rule_alt, _ = self.Redirect._find_match(alt)
        self.assertTrue(rule_alt)


class TestQueryStringForwarding(TransactionCase):
    """Unit-test the query string merger without needing an HTTP request.

    Builds a fake ``request`` object exposing ``httprequest.query_string``
    and patches the module-level ``request`` proxy that ``_era_forward_query_string``
    reads.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from odoo.addons.era_seo_suite.models import ir_http as ir_http_mod
        cls.ir_http_mod = ir_http_mod

    def _run_with_qs(self, qs_bytes, target):
        """Temporarily monkey-patch ``request`` in the module to a fake."""
        class _FakeHttp:
            def __init__(self, qs):
                self.query_string = qs
        class _FakeRequest:
            def __init__(self, qs):
                self.httprequest = _FakeHttp(qs)
        original = self.ir_http_mod.request
        self.ir_http_mod.request = _FakeRequest(qs_bytes)
        try:
            return self.ir_http_mod.IrHttp._era_forward_query_string(target)
        finally:
            self.ir_http_mod.request = original

    def test_appends_qs_when_target_has_none(self):
        out = self._run_with_qs(b'utm=foo&ref=bar', '/new-page')
        self.assertEqual(out, '/new-page?utm=foo&ref=bar')

    def test_merges_with_existing_target_qs(self):
        out = self._run_with_qs(b'utm=foo', '/new-page?keep=1')
        self.assertEqual(out, '/new-page?keep=1&utm=foo')

    def test_no_inbound_qs_keeps_target_unchanged(self):
        out = self._run_with_qs(b'', '/new-page')
        self.assertEqual(out, '/new-page')

    def test_empty_target_passes_through(self):
        out = self._run_with_qs(b'utm=foo', '')
        self.assertEqual(out, '')

    def test_works_on_absolute_url_target(self):
        out = self._run_with_qs(b'utm=foo', 'https://example.com/new')
        self.assertEqual(out, 'https://example.com/new?utm=foo')
