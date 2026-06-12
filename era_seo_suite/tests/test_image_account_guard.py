"""The Blog Gen image account must be image-capable (API-key OpenAI/Cloudflare).

CLI-proxy accounts (Claude / ChatGPT subscriptions) are text-only; pointing the
cover-image generation at one used to fail on every article and silently publish
without a cover. The picker's domain now hides them, and a stale ICP config
falls back to "no image account" with a clear log.
"""
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestImageAccountGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hub = cls.env['era.seo.suite.hub'].create({'name': 'guard-test'})
        cls.icp = cls.env['ir.config_parameter'].sudo()

    def _set_image_account(self, account):
        self.icp.set_param('era_seo.image_account_id', str(account.id))

    def test_cli_proxy_account_is_ignored_for_images(self):
        cli = self.env['era.ai.account'].create({
            'name': 'gpt-cli', 'provider': 'openai', 'auth_mode': 'cli_proxy',
        })
        self._set_image_account(cli)
        with self.assertLogs('odoo.addons.era_seo_suite.models.seo_suite_hub', 'WARNING'):
            self.assertFalse(self.hub._resolve_image_account())
        # No image account resolved -> the article publishes without a cover
        # instead of erroring on every generation.
        self.assertIsNone(self.hub._generate_article_image('a hero image'))

    def test_api_key_accounts_still_resolve(self):
        for provider, extra in (('openai', {'secret': 'sk'}),
                                ('cloudflare', {'secret': 't', 'cf_account_id': 'a'})):
            acc = self.env['era.ai.account'].create(dict(
                {'name': 'img-%s' % provider, 'provider': provider,
                 'auth_mode': 'api_key'}, **extra))
            self._set_image_account(acc)
            self.assertEqual(self.hub._resolve_image_account(), acc)
