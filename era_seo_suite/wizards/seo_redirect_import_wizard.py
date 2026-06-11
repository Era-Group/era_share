"""ERA SEO — CSV bulk import for era.seo.redirect.

Accepts a CSV with at minimum a ``source`` and ``target`` column. Optional
columns: ``type`` (defaults to wizard default), ``website`` (matched by
name or domain), ``lang`` (matched by code), ``notes``, ``is_regex``.

Dry-run mode produces a report without writing anything, so admins can
confirm row counts before committing.

Per SPEC §9.3.
"""
import base64
import csv
import io
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ('source', 'target')
_VALID_TYPES = ('301', '302', '307', '308', '410')
_TRUTHY = ('1', 'true', 'yes', 'y', 't', 'on')


class EraSeoRedirectImportWizard(models.TransientModel):
    _name = 'era.seo.redirect.import.wizard'
    _description = 'SEO Redirect Import Wizard'

    data_file = fields.Binary(string='CSV File', required=True)
    filename = fields.Char(string='File Name')
    delimiter = fields.Selection(
        [(',', 'Comma (,)'), (';', 'Semicolon (;)'), ('\t', 'Tab')],
        default=',',
        required=True,
    )
    has_header = fields.Boolean(default=True)
    default_type = fields.Selection(
        [
            ('301', '301 — Moved Permanently'),
            ('302', '302 — Found (Temporary)'),
            ('307', '307 — Temporary'),
            ('308', '308 — Permanent'),
            ('410', '410 — Gone'),
        ],
        default='301',
        required=True,
    )
    default_website_id = fields.Many2one('website', string='Default Website')
    dry_run = fields.Boolean(
        default=True,
        help='When enabled, parse and validate the file but write nothing. '
             'Disable to actually upsert the redirects.',
    )

    report = fields.Text(string='Report', readonly=True)
    created_count = fields.Integer(readonly=True)
    updated_count = fields.Integer(readonly=True)
    error_count = fields.Integer(readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Done')],
        default='draft',
        readonly=True,
    )

    # --- Action --------------------------------------------------------------

    def action_import(self):
        """Parse, validate, optionally upsert. Re-open self with the report."""
        self.ensure_one()
        # Server-side gate: `_process_rows` writes era.seo.redirect via
        # sudo(), which bypasses the model ACL — so the group must be
        # re-checked here, not only on the button/menu in the views.
        if not self.env.user.has_group('era_seo_suite.group_era_seo_manager'):
            raise AccessError(
                _('Importing redirects requires the SEO Manager group.')
            )
        if not self.data_file:
            raise UserError(_('Please upload a CSV file first.'))

        rows, errors = self._parse_csv()
        created, updated, row_errors = self._process_rows(rows)
        errors.extend(row_errors)

        self.write({
            'state': 'done',
            'created_count': created,
            'updated_count': updated,
            'error_count': len(errors),
            'report': self._format_report(rows, errors, created, updated),
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Redirect Import Result'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    # --- Internals -----------------------------------------------------------

    def _parse_csv(self):
        """Return ``(rows, errors)``.

        Each row is a dict keyed by canonical column name (``source``,
        ``target``, ``type``, ``website``, ``lang``, ``notes``, ``is_regex``).
        """
        try:
            raw = base64.b64decode(self.data_file)
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Could not decode upload: %s', exc)) from exc

        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = raw.decode('cp1256')
            except UnicodeDecodeError as exc:
                raise UserError(
                    _('Unsupported file encoding. Please save the CSV as UTF-8.')
                ) from exc

        reader = csv.reader(io.StringIO(text), delimiter=self.delimiter)
        rows = list(reader)
        if not rows:
            return [], [_('File is empty.')]

        errors = []
        if self.has_header:
            header = [c.strip().lower() for c in rows[0]]
            data_rows = rows[1:]
        else:
            header = ['source', 'target', 'type', 'website', 'lang', 'notes', 'is_regex']
            data_rows = rows

        missing = [c for c in _REQUIRED_COLUMNS if c not in header]
        if missing:
            errors.append(
                _('Missing required column(s): %s. Found: %s',
                  ', '.join(missing), ', '.join(header))
            )
            return [], errors

        parsed = []
        for line_no, row in enumerate(data_rows, start=2 if self.has_header else 1):
            if not any(cell.strip() for cell in row):
                continue  # skip blank lines
            record = {}
            for col_name, value in zip(header, row):
                record[col_name] = value.strip()
            record.setdefault('_line', line_no)
            parsed.append(record)
        return parsed, errors

    def _process_rows(self, rows):
        Redirect = self.env['era.seo.redirect'].sudo()
        Website = self.env['website'].sudo()
        Lang = self.env['res.lang'].sudo()

        created = updated = 0
        errors = []

        for row in rows:
            line = row.get('_line', '?')
            vals, row_errors = self._row_to_vals(row, Website, Lang)
            if row_errors:
                errors.extend(_('Line %s: %s', line, e) for e in row_errors)
                continue
            if self.dry_run:
                continue

            existing = Redirect.search([
                ('source_url', '=', vals['source_url']),
                ('website_id', '=', vals.get('website_id', False) or False),
                ('lang_id', '=', vals.get('lang_id', False) or False),
            ], limit=1)
            try:
                if existing:
                    existing.write(vals)
                    updated += 1
                else:
                    vals['created_from'] = 'import'
                    Redirect.create(vals)
                    created += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(_('Line %s: write failed: %s', line, exc))

        return created, updated, errors

    def _row_to_vals(self, row, Website, Lang):
        """Translate one CSV row into ``era.seo.redirect`` vals; collect errors."""
        errors = []
        source = (row.get('source') or '').strip()
        target = (row.get('target') or '').strip()
        if not source:
            errors.append(_('source is empty'))
        rtype = (row.get('type') or '').strip() or self.default_type
        if rtype not in _VALID_TYPES:
            errors.append(_('invalid type %r (allowed: %s)',
                            rtype, ', '.join(_VALID_TYPES)))
        if rtype != '410' and not target:
            errors.append(_('target is required for type %s', rtype))

        is_regex_raw = (row.get('is_regex') or '').strip().lower()
        is_regex = is_regex_raw in _TRUTHY

        vals = {
            'source_url': source,
            'target_url': target if rtype != '410' else False,
            'redirect_type': rtype,
            'is_regex': is_regex,
            'notes': row.get('notes') or False,
        }

        # Optional website
        website_label = (row.get('website') or '').strip()
        if website_label:
            website = Website.search(
                ['|', ('name', '=', website_label), ('domain', '=', website_label)],
                limit=1,
            )
            if website:
                vals['website_id'] = website.id
            else:
                errors.append(_('unknown website %r', website_label))
        elif self.default_website_id:
            vals['website_id'] = self.default_website_id.id

        # Optional lang
        lang_code = (row.get('lang') or '').strip()
        if lang_code:
            lang = Lang.search([('code', '=', lang_code)], limit=1)
            if lang:
                vals['lang_id'] = lang.id
            else:
                errors.append(_('unknown lang code %r', lang_code))

        return vals, errors

    def _format_report(self, rows, errors, created, updated):
        lines = []
        lines.append(_('Total rows parsed: %d', len(rows)))
        if self.dry_run:
            lines.append(_('Dry run — no changes were written.'))
            valid = len(rows) - len(errors)
            lines.append(_('Would create or update: %d valid row(s).', valid))
        else:
            lines.append(_('Created: %d', created))
            lines.append(_('Updated: %d', updated))
        lines.append(_('Errors: %d', len(errors)))
        if errors:
            lines.append('')
            lines.append(_('--- Errors ---'))
            lines.extend(errors[:50])
            if len(errors) > 50:
                lines.append(_('… and %d more', len(errors) - 50))
        return '\n'.join(str(line) for line in lines)
