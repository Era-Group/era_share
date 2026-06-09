import os
import base64
import logging
from tempfile import NamedTemporaryFile

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _commit_progress(cr, session_id, done=False):
    """Commit translated lines and refresh stored progress fields.

    Uses a savepoint around the progress UPDATE so a concurrent
    serialization failure only skips the counts refresh — the
    already-written msgstr rows are always committed.

    When done=True, the is_translating flag is reset in a separate
    independent commit so it is never lost even if progress counts fail.
    """
    import time

    # 1. Always commit the pending msgstr writes first
    cr.commit()

    # 2. Try to update the session progress counts (best-effort)
    try:
        cr.execute("SAVEPOINT era_progress")
        cr.execute("""
            UPDATE translation_session ts SET
                translated_count = sub.done,
                untranslated_count = ts.total_count - sub.done,
                progress = CASE WHEN ts.total_count > 0
                                THEN ROUND(100.0 * sub.done / ts.total_count, 1)
                                ELSE 0 END
            FROM (
                SELECT COUNT(*) AS done
                FROM translation_session_line
                WHERE session_id = %(sid)s
                  AND msgstr IS NOT NULL
                  AND trim(msgstr) != ''
                  AND msgid  IS NOT NULL
                  AND trim(msgid)  != ''
            ) sub
            WHERE ts.id = %(sid)s
        """, {'sid': session_id})
        cr.execute("RELEASE SAVEPOINT era_progress")
        cr.commit()
    except Exception as exc:
        cr.execute("ROLLBACK TO SAVEPOINT era_progress")
        cr.commit()
        _logger.warning('Progress counts refresh skipped (concurrent update): %s', exc)

    # 3. When finishing, reset is_translating in its own commit with retries
    #    so it is never silently lost due to a concurrent conflict.
    if done:
        for attempt in range(5):
            try:
                cr.execute("SAVEPOINT era_done")
                cr.execute(
                    'UPDATE translation_session'
                    ' SET is_translating = false, cron_id = NULL'
                    ' WHERE id = %s', (session_id,))
                cr.execute("RELEASE SAVEPOINT era_done")
                cr.commit()
                break
            except Exception as exc:
                cr.execute("ROLLBACK TO SAVEPOINT era_done")
                cr.commit()
                _logger.warning(
                    'is_translating reset attempt %d failed: %s', attempt + 1, exc)
                time.sleep(0.3 * (attempt + 1))


class TranslationSession(models.Model):
    _name = 'translation.session'
    _description = 'Translation Session'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _rec_name = 'name'
    _order = 'write_date desc'

    name = fields.Char(string='Session Name', required=True, tracking=True,
                       default=lambda self: _('New Session'))
    module_id = fields.Many2one(
        'ir.module.module', string='Module', required=True, tracking=True,
        domain=[('state', '=', 'installed')],
        ondelete='cascade',
    )
    module_name = fields.Char(string='Module Technical Name', compute='_compute_module_name',
                              store=True, readonly=False)
    source_lang_id = fields.Many2one(
        'res.lang', string='Source Language', required=True,
        default=lambda self: self.env.ref('base.lang_en', raise_if_not_found=False),
        help='Language of the original source text',
    )
    source_lang = fields.Char(string='Source Language Code', compute='_compute_source_lang',
                              store=True, readonly=False)
    target_lang_id = fields.Many2one('res.lang', string='Target Language', required=True,
                                     tracking=True)
    target_lang_code = fields.Char(related='target_lang_id.code', string='Target Language Code',
                                   store=True)

    line_ids = fields.One2many('translation.session.line', 'session_id', string='Translation Terms')

    total_count = fields.Integer(string='Total Terms', compute='_compute_progress', store=True)
    translated_count = fields.Integer(string='Translated', compute='_compute_progress', store=True)
    untranslated_count = fields.Integer(string='Untranslated', compute='_compute_progress', store=True)
    progress = fields.Float(string='Progress (%)', compute='_compute_progress', store=True,
                            digits=(5, 1))

    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
    ], default='draft', string='Status', tracking=True)

    is_translating = fields.Boolean(string='Translating in Background', default=False)
    cron_id = fields.Many2one('ir.cron', string='Background Cron', ondelete='set null')

    # -------------------------------------------------------------------------
    # Computed
    # -------------------------------------------------------------------------

    @api.depends('source_lang_id')
    def _compute_source_lang(self):
        for rec in self:
            rec.source_lang = rec.source_lang_id.code if rec.source_lang_id else 'en_US'

    @api.depends('module_id')
    def _compute_module_name(self):
        for rec in self:
            rec.module_name = rec.module_id.name if rec.module_id else False

    @api.onchange('module_id', 'target_lang_id')
    def _onchange_build_name(self):
        if self.module_id and self.target_lang_id:
            self.name = '%s → %s' % (
                self.module_id.shortdesc or self.module_id.name,
                self.target_lang_id.name,
            )

    @api.depends('line_ids', 'line_ids.is_translated')
    def _compute_progress(self):
        for rec in self:
            total = len(rec.line_ids)
            translated = len(rec.line_ids.filtered('is_translated'))
            rec.total_count = total
            rec.translated_count = translated
            rec.untranslated_count = total - translated
            rec.progress = (translated / total * 100.0) if total else 0.0

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_load_po(self):
        """Load PO entries from the module's i18n/ directory into session lines."""
        self.ensure_one()
        from odoo.modules.module import get_module_path
        module_path = get_module_path(self.module_name, display_warning=False)
        if not module_path:
            raise UserError(_('Module "%s" not found on this server.') % self.module_name)

        lang_code = self.target_lang_id.code
        i18n_dir = os.path.join(module_path, 'i18n')
        po_path = os.path.join(i18n_dir, '%s.po' % lang_code)

        if not os.path.exists(po_path):
            # Ensure i18n/ directory exists (create if missing)
            os.makedirs(i18n_dir, exist_ok=True)

            # Fall back to .pot file
            pot_path = os.path.join(i18n_dir, '%s.pot' % self.module_name)
            if os.path.exists(pot_path):
                po_path = pot_path
            else:
                pots = [f for f in os.listdir(i18n_dir) if f.endswith('.pot')]
                if pots:
                    po_path = os.path.join(i18n_dir, pots[0])
                else:
                    # No PO/POT found — extract terms from source code and save as .pot
                    _logger.info('No PO/POT found for %s — extracting from source code.', self.module_name)
                    pot_path = os.path.join(i18n_dir, '%s.pot' % self.module_name)
                    _extract_pot_from_module(self.env, self.module_name, pot_path)
                    po_path = pot_path if os.path.exists(pot_path) else None

        entries = _parse_po_file(po_path) if po_path else []

        # Replace existing lines
        self.line_ids.unlink()
        vals_list = []
        for entry in entries:
            if not entry.get('msgid'):
                continue
            vals_list.append({
                'session_id': self.id,
                'msgid': entry['msgid'],
                'msgstr': entry.get('msgstr', ''),
                'comment': entry.get('comment', ''),
            })

        if vals_list:
            self.env['translation.session.line'].create(vals_list)

        self.state = 'in_progress'

        if not po_path:
            source_msg = _('No translatable terms found in module "%s".') % self.module_name
        elif not vals_list:
            source_msg = _('Source code scanned but no translatable terms found in "%s".') % self.module_name
        else:
            source_msg = _('%d terms loaded from %s') % (len(vals_list), os.path.basename(po_path))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('PO File Loaded'),
                'message': source_msg,
                'type': 'success' if vals_list else 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_rescan_source(self):
        """Extract all translatable terms from module source and add only new (missing) ones."""
        self.ensure_one()
        from odoo.modules.module import get_module_path

        module_path = get_module_path(self.module_name, display_warning=False)
        if not module_path:
            raise UserError(_('Module "%s" not found on this server.') % self.module_name)

        # For writable (custom) modules write the POT into i18n/; for standard
        # (read-only) modules use a temporary file so we never touch Odoo core.
        if _is_writable_module(module_path):
            i18n_dir = os.path.join(module_path, 'i18n')
            os.makedirs(i18n_dir, exist_ok=True)
            pot_path = os.path.join(i18n_dir, '%s.pot' % self.module_name)
            _extract_pot_from_module(self.env, self.module_name, pot_path)
            tmp_pot = None
        else:
            import tempfile
            tmp_pot = tempfile.NamedTemporaryFile(suffix='.pot', delete=False)
            pot_path = tmp_pot.name
            tmp_pot.close()
            _extract_pot_from_module(self.env, self.module_name, pot_path)

        if not os.path.exists(pot_path) or os.path.getsize(pot_path) == 0:
            if tmp_pot is not None:
                try:
                    os.unlink(pot_path)
                except OSError:
                    pass
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Rescan Complete'),
                    'message': _('Source code scanned but no translatable terms found in "%s".') % self.module_name,
                    'type': 'warning',
                    'sticky': False,
                },
            }

        all_entries = _parse_po_file(pot_path)

        # Remove temp file if we created one
        if tmp_pot is not None:
            try:
                os.unlink(pot_path)
            except OSError:
                pass

        existing_msgids = set(self.line_ids.mapped('msgid'))

        new_vals = []
        for entry in all_entries:
            msgid = entry.get('msgid')
            if msgid and msgid not in existing_msgids:
                new_vals.append({
                    'session_id': self.id,
                    'msgid': msgid,
                    'msgstr': '',
                    'comment': entry.get('comment', ''),
                })

        if new_vals:
            self.env['translation.session.line'].create(new_vals)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Rescan Complete'),
                'message': _('%d new terms added from source code.') % len(new_vals)
                           if new_vals else _('No new terms found — session is up to date.'),
                'type': 'success' if new_vals else 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_auto_translate_all(self):
        """Queue auto-translation as a background cron job to avoid HTTP timeouts."""
        self.ensure_one()
        try:
            from deep_translator import GoogleTranslator  # noqa: F401
        except ImportError:
            raise UserError(_(
                'The deep_translator Python package is required for auto-translation.\n\n'
                'Install it with:\n  pip install deep-translator'
            ))

        untranslated_count = len(
            self.line_ids.filtered(lambda l: not l.is_translated and l.msgid.strip()))

        if not untranslated_count:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Auto-Translate'),
                    'message': _('No untranslated terms found.'),
                    'type': 'info',
                    'sticky': False,
                },
            }

        self.is_translating = True

        model_id = self.env['ir.model']._get('translation.session').id
        cron = self.env['ir.cron'].sudo().create({
            'name': 'Auto-translate: %s' % self.name,
            'model_id': model_id,
            'code': 'model.browse(%d)._auto_translate_background()' % self.id,
            'nextcall': fields.Datetime.now(),
            'active': True,
            'user_id': self.env.uid,
        })
        self.cron_id = cron.id

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Translation Started'),
                'message': _('%d terms queued for background translation. The page will refresh automatically.') % untranslated_count,
                'type': 'info',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def _auto_translate_background(self):
        """Called by ir.cron — performs the actual translation outside the HTTP request cycle.

        All DB writes use a dedicated cursor (self.pool.cursor()) completely
        separate from the cron's own cursor.  This avoids the Odoo 19 protection
        that blocks writes to the ir.cron record while it is executing.
        """
        self.ensure_one()
        try:
            from deep_translator import GoogleTranslator
        except ImportError:
            _logger.error('deep_translator not installed — background translation aborted.')
            self.env.cr.execute(
                'UPDATE translation_session SET is_translating = false WHERE id = %s', (self.id,))
            return

        src = (self.source_lang or 'en').split('_')[0]
        tgt = self.target_lang_id.code.split('_')[0]
        translator = GoogleTranslator(source=src, target=tgt)

        session_id = self.id
        # Snapshot untranslated line IDs using the cron cursor (read-only here)
        untranslated_ids = self.line_ids.filtered(
            lambda l: not l.is_translated and l.msgid.strip()).ids

        count = 0
        COMMIT_EVERY = 50

        # Use a separate cursor so commits never touch the cron-locked cursor
        with self.pool.cursor() as cr:
            for line_id in untranslated_ids:
                # Check stop flag written by action_stop_translation
                cr.execute(
                    'SELECT is_translating FROM translation_session WHERE id = %s',
                    (session_id,))
                row = cr.fetchone()
                if not row or not row[0]:
                    _logger.info(
                        'Background auto-translate stopped by user at %d terms for session %d',
                        count, session_id)
                    cr.commit()
                    return

                cr.execute(
                    'SELECT msgid FROM translation_session_line WHERE id = %s', (line_id,))
                row = cr.fetchone()
                if not row:
                    continue
                msgid = row[0]

                try:
                    result = translator.translate(msgid)
                    if result:
                        cr.execute(
                            'UPDATE translation_session_line SET msgstr = %s WHERE id = %s',
                            (result, line_id))
                        count += 1
                except Exception as exc:
                    _logger.warning('Auto-translate failed for msgid [%s]: %s', msgid[:60], exc)

                # Commit every COMMIT_EVERY terms and refresh stored progress fields
                if count > 0 and count % COMMIT_EVERY == 0:
                    _commit_progress(cr, session_id)

            # Final: mark session done, refresh progress, deactivate cron
            _commit_progress(cr, session_id, done=True)

        # Clear the ORM cache so the cron cursor doesn't flush stale computed
        # field values (translated_count etc.) and conflict with our commits.
        # The loop can run for minutes (one network call per term), long enough
        # that the cron's own cursor may already be closed here — so post the
        # completion chatter defensively and never let a dead cursor turn a
        # successful, already-committed run into a cron ERROR.
        try:
            self.env.invalidate_all()
            self.message_post(body=_('%d terms translated.') % count)
        except Exception as exc:  # noqa: BLE001 — cron cursor may be closed after a long run
            _logger.warning(
                'Auto-translate: completion chatter on the cron cursor failed '
                '(%s); retrying on a fresh cursor. %d terms were translated and '
                'committed.', exc, count)
            try:
                with self.pool.cursor() as post_cr:
                    self.with_env(self.env(cr=post_cr)).message_post(
                        body=_('%d terms translated.') % count)
                    post_cr.commit()
            except Exception as exc2:  # noqa: BLE001
                _logger.warning(
                    'Auto-translate: completion chatter retry also failed: %s', exc2)
        _logger.info('Background auto-translate finished: %d terms for session %d',
                     count, session_id)

    def action_stop_translation(self):
        """Cancel the background translation job.

        Sets is_translating=False so the running cron loop exits on its next
        iteration.  We do NOT try to write to ir.cron here — Odoo locks the
        cron record while it is executing, so writing active=False would raise
        an error.  The cron will deactivate itself when it detects the flag.
        """
        self.ensure_one()
        self.is_translating = False
        self.message_post(body=_('Background translation stopped by user.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Stopped'),
                'message': _('Background translation has been stopped.'),
                'type': 'warning',
                'sticky': False,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_save_and_import(self):
        """Write translations to the .po file and import into Odoo."""
        self.ensure_one()
        from odoo.modules.module import get_module_path
        from odoo.tools.translate import TranslationImporter

        module_path = get_module_path(self.module_name, display_warning=False)
        if not module_path:
            raise UserError(_('Module "%s" not found on this server.') % self.module_name)

        lang_code = self.target_lang_id.code

        # Ensure the language is activated in Odoo
        Lang = self.env['res.lang']
        if not Lang._lang_get(lang_code):
            raise UserError(_(
                'Language "%s" is not installed in Odoo.\n'
                'Go to Settings → Translations → Languages to add it first.'
            ) % lang_code)

        # Check whether this module is in the writable custom addons root
        is_writable = _is_writable_module(module_path)

        if is_writable:
            # Custom module → save to file then import
            i18n_dir = os.path.join(module_path, 'i18n')
            os.makedirs(i18n_dir, exist_ok=True)
            po_path = os.path.join(i18n_dir, '%s.po' % lang_code)
            _write_po_file(po_path, self.target_lang_id, self.line_ids)

            importer = TranslationImporter(self.env.cr, verbose=False)
            with open(po_path, 'rb') as fobj:
                importer.load(fobj, 'po', lang_code, module=self.module_name)
            importer.save(overwrite=True)

            self.state = 'done'
            message = _('Translations saved to %s and imported into Odoo.') % po_path
        else:
            # Standard / read-only module → import directly into DB without touching files
            importer = TranslationImporter(self.env.cr, verbose=False)
            _import_lines_directly(importer, self.line_ids, lang_code, self.module_name)
            importer.save(overwrite=True)

            self.state = 'done'
            message = _(
                'Module "%s" is a standard Odoo module — translations were applied '
                'directly to the database without modifying any files.'
            ) % self.module_name

        # Force the per-worker code-translation cache to drop entries for this
        # language so they are re-read from the PO files on the next request.
        _invalidate_code_translation_cache(lang_code)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Import Complete'),
                'message': message,
                'type': 'success',
                'sticky': True,
            },
        }

    def action_reset_to_draft(self):
        self.ensure_one()
        self.state = 'draft'


# ---------------------------------------------------------------------------
# PO file helpers (module-level, no self needed)
# ---------------------------------------------------------------------------

def _invalidate_code_translation_cache(lang_code):
    """Drop all per-language entries from the CodeTranslations singleton cache.

    ``code_translations`` is a module-level object whose dicts are only
    populated lazily and never cleared by ``TranslationImporter.save()``.
    Removing the entries for *lang_code* forces every worker to re-read the
    PO files from disk on the next ``_()`` call, making new translations
    visible immediately without a restart.
    """
    try:
        from odoo.tools.translate import code_translations
        stale = [k for k in list(code_translations.python_translations) if k[1] == lang_code]
        for k in stale:
            del code_translations.python_translations[k]
        stale = [k for k in list(code_translations.web_translations) if k[1] == lang_code]
        for k in stale:
            del code_translations.web_translations[k]
    except Exception:
        _logger.debug('Could not invalidate code_translations cache', exc_info=True)


def _unescape_po(text):
    return (text
            .replace('\\n', '\n')
            .replace('\\t', '\t')
            .replace('\\"', '"')
            .replace('\\\\', '\\'))


def _escape_po(text):
    return (text
            .replace('\\', '\\\\')
            .replace('"', '\\"')
            .replace('\n', '\\n')
            .replace('\t', '\\t'))


def _get_custom_addons_root():
    """Return the absolute path of the custom addons directory.

    We use the location of *this* module (era_po_translator) as the anchor:
    its parent directory is the custom addons root.  Any module that lives in
    the same directory is considered custom and therefore writable.
    """
    from odoo.modules.module import get_module_path
    own_path = get_module_path('era_po_translator', display_warning=False)
    return os.path.abspath(os.path.dirname(own_path))


def _is_writable_module(module_path):
    """Return True if module_path is inside the custom addons root."""
    custom_root = _get_custom_addons_root()
    abs_module = os.path.abspath(module_path)
    return abs_module.startswith(custom_root + os.sep) or abs_module == custom_root


def _import_lines_directly(importer, lines, lang_code, module_name):
    """Write session lines to a temp PO file and load it into importer.

    Odoo's PoFileReader requires:
      - A real file path (not BytesIO) so it can look for a .pot companion.
      - Each entry must have a ``#. module: <name>`` translator-comment.
      - Each entry must have a ``#: code:addons/<name>/placeholder.py:0``
        occurrence so the reader classifies it as a 'code' translation.
    """
    lines_buf = []
    # PO header
    lines_buf.append('# Temp PO generated by ERA PO Translator')
    lines_buf.append('#')
    lines_buf.append('msgid ""')
    lines_buf.append('msgstr ""')
    lines_buf.append('"Content-Type: text/plain; charset=UTF-8\\n"')
    lines_buf.append('"Content-Transfer-Encoding: 8bit\\n"')
    lines_buf.append('"Language: %s\\n"' % lang_code)
    lines_buf.append('')

    occurrence = 'code:addons/%s/models/placeholder.py' % module_name

    for line in lines:
        if not line.msgid or not line.msgstr:
            continue
        msgid = _escape_po(line.msgid)
        msgstr = _escape_po(line.msgstr)
        lines_buf.append('#. module: %s' % module_name)
        lines_buf.append('#: %s:0' % occurrence)
        lines_buf.append('msgid "%s"' % msgid)
        lines_buf.append('msgstr "%s"' % msgstr)
        lines_buf.append('')

    content = '\n'.join(lines_buf).encode('utf-8')

    with NamedTemporaryFile(suffix='.po', delete=True) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp.seek(0)
        importer.load(tmp, 'po', lang_code, module=module_name)


def _parse_po_file(path):
    """Parse a PO file and return a list of dicts with msgid/msgstr/comment."""
    entries = []
    entry = {}
    current_key = None

    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for raw_line in fh:
            line = raw_line.rstrip('\n')
            stripped = line.strip()

            if stripped.startswith('#'):
                if current_key:
                    # A comment after content signals end of the previous entry
                    if entry.get('msgid') is not None:
                        entries.append(dict(entry))
                        entry = {}
                        current_key = None
                comment = entry.get('comment', '')
                entry['comment'] = (comment + '\n' + stripped).strip() if comment else stripped
            elif stripped.startswith('msgid "') and stripped.endswith('"'):
                if entry.get('msgid'):
                    entries.append(dict(entry))
                    entry = {}
                current_key = 'msgid'
                entry['msgid'] = _unescape_po(stripped[7:-1])
            elif stripped.startswith('msgstr "') and stripped.endswith('"'):
                current_key = 'msgstr'
                entry['msgstr'] = _unescape_po(stripped[8:-1])
            elif stripped.startswith('"') and stripped.endswith('"') and current_key:
                entry[current_key] = entry.get(current_key, '') + _unescape_po(stripped[1:-1])
            elif not stripped:
                if entry.get('msgid') is not None:
                    entries.append(dict(entry))
                    entry = {}
                current_key = None

    if entry.get('msgid') is not None:
        entries.append(entry)

    # Filter out the header entry (empty msgid) and empty msgids
    return [e for e in entries if e.get('msgid')]


def _write_po_file(path, lang_rec, lines):
    """Write a PO file from translation session lines."""
    buf = []
    buf.append('# Translation file generated by PO Translator\n')
    buf.append('#\n')
    buf.append('msgid ""\n')
    buf.append('msgstr ""\n')
    buf.append('"Content-Type: text/plain; charset=UTF-8\\n"\n')
    buf.append('"Content-Transfer-Encoding: 8bit\\n"\n')
    buf.append('"Language: %s\\n"\n' % lang_rec.code)
    buf.append('\n')

    for line in lines:
        if line.comment:
            for c_line in line.comment.splitlines():
                buf.append('%s\n' % c_line)

        msgid = _escape_po(line.msgid or '')
        msgstr = _escape_po(line.msgstr or '')

        buf.append('msgid "%s"\n' % msgid)
        buf.append('msgstr "%s"\n' % msgstr)
        buf.append('\n')

    with open(path, 'w', encoding='utf-8') as fh:
        fh.writelines(buf)


def _extract_pot_from_module(env, module_name, pot_path):
    """Use Odoo's trans_export to extract all translatable terms from a module's
    source code and write them as a POT file at pot_path."""
    import io
    from odoo.tools.translate import trans_export

    buf = io.BytesIO()
    try:
        trans_export(False, [module_name], buf, 'po', env)
    except Exception:
        _logger.exception('trans_export failed for module %s', module_name)
        return

    content = buf.getvalue()
    if not content:
        return

    with open(pot_path, 'wb') as fh:
        fh.write(content)
    _logger.info('Generated POT file for %s at %s (%d bytes)', module_name, pot_path, len(content))
