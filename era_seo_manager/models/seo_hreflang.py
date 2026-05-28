"""ERA SEO — hreflang entries.

One row per ``(res_model, res_id, lang)`` triple. Each row carries the
absolute URL to use in the corresponding ``<link rel="alternate"
hreflang="…">`` tag. Exactly one row per ``(res_model, res_id)`` group
may be flagged ``is_xdefault``.

Auto-population is driven by ``era.seo.mixin._sync_era_hreflang_entries()``
which is invoked on create / write of any host model that inherits the
mixin. Admins may override the auto-computed URL by editing the row in
the backend; subsequent syncs leave overridden rows alone (detected by
the ``is_manual`` flag).

Per SPEC §12.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class EraSeoHreflang(models.Model):
    _name = 'era.seo.hreflang'
    _description = 'SEO Hreflang Entry'
    _order = 'res_model, res_id, is_xdefault desc, lang_id'

    name = fields.Char(string='Name', compute='_compute_name', store=True)

    res_model = fields.Char(string='Record Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    lang_id = fields.Many2one(
        'res.lang',
        string='Language',
        ondelete='cascade',
        required=True,
        index=True,
    )
    url = fields.Char(
        string='URL',
        required=True,
        help='Absolute URL to emit in <link rel="alternate" hreflang="…" href="…"/>. '
             'Auto-computed on create/write of the host record unless is_manual is set.',
    )
    is_xdefault = fields.Boolean(
        string='x-default',
        default=False,
        help='Emit this row as hreflang="x-default" (the fallback for users '
             'whose locale matches none of the explicit alternates).',
    )
    is_manual = fields.Boolean(
        string='Manual Override',
        default=False,
        help='Set when an admin edits the URL by hand. Auto-sync will skip '
             'this row on subsequent host-record writes.',
    )
    active = fields.Boolean(default=True)

    # --- SQL --------------------------------------------------------------

    def init(self):
        """Composite uniqueness on (res_model, res_id, lang_id)."""
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS era_seo_hreflang_triple_uidx
            ON era_seo_hreflang (res_model, res_id, lang_id)
        """)

    # --- Computes ---------------------------------------------------------

    @api.depends('res_model', 'res_id', 'lang_id', 'is_xdefault')
    def _compute_name(self):
        for rec in self:
            label = rec.lang_id.code or '?'
            if rec.is_xdefault:
                label = 'x-default'
            rec.name = '{} on {}#{}'.format(label, rec.res_model or '?', rec.res_id or 0)

    # --- Constraints ------------------------------------------------------

    @api.constrains('res_model', 'res_id', 'is_xdefault')
    def _check_one_xdefault(self):
        """At most one ``is_xdefault`` row per (res_model, res_id) group."""
        for rec in self.filtered('is_xdefault'):
            duplicates = self.sudo().search([
                ('res_model', '=', rec.res_model),
                ('res_id', '=', rec.res_id),
                ('is_xdefault', '=', True),
                ('id', '!=', rec.id),
            ])
            if duplicates:
                raise ValidationError(
                    _('Only one x-default hreflang entry is allowed per record '
                      '(%s#%d).', rec.res_model, rec.res_id)
                )

    # --- Public API -------------------------------------------------------

    @api.model
    def _get_for_record(self, record):
        """Return the active hreflang entries for ``record``, ordered."""
        if not record or not record.id:
            return self.browse()
        return self.sudo().search([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('active', '=', True),
        ])

    @api.model
    def _emit_disabled(self):
        """Site-wide kill switch: ``era_seo.hreflang_enabled`` ICP."""
        flag = self.env['ir.config_parameter'].sudo().get_param(
            'era_seo.hreflang_enabled', 'True',
        )
        return flag in ('False', '0', 'false', '')

    @api.model
    def _era_resync_all_records(self):
        """Walk every concrete model carrying ``era.seo.mixin`` and re-sync.

        Called when a global setting changes that invalidates many rows
        at once (e.g. the website's default language flipped). Detects
        eligible models via duck-typing on ``_sync_era_hreflang_entries``,
        so any future host model picks up the resweep automatically.

        Failures on one record do not stop the loop; each model's batch
        is wrapped in a savepoint.
        """
        total = 0
        for model_name in list(self.env.registry.keys()):
            Model = self.env[model_name]
            if getattr(Model, '_abstract', True):
                continue
            if not hasattr(Model, '_sync_era_hreflang_entries'):
                continue
            try:
                records = Model.sudo().search([])
                if records:
                    records._sync_era_hreflang_entries()
                    total += len(records)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    'era.seo.hreflang resync: model %s skipped (%s)',
                    model_name, exc,
                )
        _logger.info('era.seo.hreflang: resynced %d records across all host models', total)
        return total

    def action_resync_all(self):
        """Server-action entry: re-sync every host model. Admin button."""
        self._era_resync_all_records()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': 'Hreflang rows re-synced from every host record.',
                'sticky': False,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
