import base64
import json
import logging
import re
import time
from difflib import SequenceMatcher
from urllib.parse import quote, urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .cs_account import _cs_extract_json
from .cs_customer_match_alias import customer_alias_key


_logger = logging.getLogger(__name__)


SHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
ERA_COLUMNS = {
    'A': 'ERA CSM', 'B': 'ERA CSM phone number', 'C': 'ERA CSM email',
    'G': 'Recurring plan', 'H': 'Industry', 'K': 'Active Users',
    'N': 'Adoption', 'O': 'Client Website',
}
SHEET_ACCOUNT_FIELDS = {
    'A': 'sheet_era_csm', 'B': 'sheet_era_csm_phone', 'C': 'sheet_era_csm_email',
    'D': 'sheet_customer_name', 'E': 'sheet_date_of_join', 'F': 'sheet_next_invoice_date',
    'G': 'sheet_recurring_plan', 'H': 'sheet_industry', 'I': 'sheet_number_of_employees',
    'J': 'sheet_number_of_users', 'K': 'sheet_active_users', 'L': 'sheet_stage',
    'M': 'sheet_version', 'N': 'sheet_adoption', 'O': 'sheet_client_website',
    'P': 'sheet_active_implemented_modules', 'Q': 'sheet_potential_expansion',
    'R': 'sheet_next_action', 'S': 'sheet_extra_notes', 'T': 'sheet_expansion_status',
}
MULTI_SELECT_SHEET_FIELDS = {'sheet_active_implemented_modules'}
MATCH_HEADER_ALIASES = {
    'name': {'customer name', 'client name', 'company name'},
    'email': {'customer email', 'client email', 'company email', 'email'},
    'phone': {'customer phone', 'client phone', 'company phone', 'phone', 'mobile'},
    'website': {'client website', 'customer website', 'company website', 'website'},
    'vat': {'vat', 'tax id', 'tax number', 'registration number', 'cr number'},
}
NAME_STOP_WORDS = {
    'company', 'co', 'ltd', 'limited', 'llc', 'inc', 'corp', 'corporation', 'group',
    'holding', 'holdings', 'trading', 'establishment', 'enterprise', 'enterprises',
    'business', 'services', 'service', 'international', 'int', 'sa', 'ksa',
    'factory', 'manufacturing', 'manufacturer', 'industry', 'industrial',
    'arabian', 'arabia', 'for', 'plastic', 'plastics', 'products', 'product',
    'specialized', 'specialised', 'and', 'his', 'partner', 'sons', 'son', 'travel',
    'agency', 'office', 'consulting', 'water', 'health', 'food', 'foods', 'al',
    'commercial', 'modern',
    'شركة', 'شركه', 'مؤسسة', 'مؤسسه', 'مجموعة', 'مجموعه', 'للتجارة', 'للتجاره',
    'التجارية', 'التجاريه', 'التجارة', 'التجاره', 'العالمية', 'العالميه',
    'المحدودة', 'المحدوده', 'محدوده', 'ذمم', 'م م', 'مصنع', 'صناعه', 'لصناعه', 'للصناعه', 'صناعي',
    'صناعيه', 'الصناعه', 'الصناعيه', 'عربي', 'عربيه', 'متخصصه', 'للمنتجات',
    'منتجات', 'بلاستيك', 'بلاستيكيه', 'وكاله', 'وكالة', 'مؤسسه', 'مؤسسة', 'موسسه', 'مكتب',
    'استشارات', 'مياه', 'صحيه', 'مصانع', 'اغذيه', 'للغذيه', 'للاغذيه', 'سفر', 'وشريكه',
}
ARABIC_NAME_TRANSLATION = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا', 'ؤ': 'و', 'ئ': 'ي',
    'ى': 'ي', 'ة': 'ه', 'ـ': '', 'پ': 'ب', 'چ': 'ج', 'ڤ': 'ف', 'گ': 'ك',
})
ARABIC_PHONETIC_TRANSLATION = str.maketrans({
    'ا': 'a', 'ب': 'p', 'ت': 't', 'ث': 's', 'ج': 'j', 'ح': 'h', 'خ': 'k',
    'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z', 'س': 's', 'ش': 'x', 'ص': 's',
    'ض': 'd', 'ط': 't', 'ظ': 'z', 'ع': 'a', 'غ': 'g', 'ف': 'f', 'ق': 'k',
    'ك': 'k', 'ل': 'l', 'م': 'm', 'ن': 'n', 'ه': 'h', 'و': 'o', 'ي': 'i',
})


def _b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def _normalize_text(value):
    return re.sub(r'[^\w]+', ' ', (value or '').casefold()).strip()


def _normalize_customer_name(value):
    text = (value or '').casefold().translate(ARABIC_NAME_TRANSLATION)
    text = re.sub(r'[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]', '', text)
    tokens = []
    for token in re.findall(r'[\w]+', text):
        if token.startswith(('ال', 'al')) and len(token) > 4:
            token = token[2:]
        if token not in NAME_STOP_WORDS and len(token) > 1:
            tokens.append(token)
    return ' '.join(tokens)


def _phonetic_customer_name(value):
    text = _normalize_customer_name(value).translate(ARABIC_PHONETIC_TRANSLATION)
    text = (text.replace('ph', 'f').replace('kh', 'k').replace('sh', 's')
                .replace('th', 's').replace('v', 'f').replace('q', 'k')
                .replace('c', 'k').replace('b', 'p').replace('x', 's'))
    text = re.sub(r'[aeiou]+', '', text)
    text = re.sub(r'(.)\1+', r'\1', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _customer_name_score(left, right):
    left_core, right_core = _normalize_customer_name(left), _normalize_customer_name(right)
    if not left_core or not right_core:
        return 0, ''
    if left_core == right_core:
        return 90, 'normalized name'
    left_phonetic, right_phonetic = _phonetic_customer_name(left), _phonetic_customer_name(right)
    if left_phonetic and left_phonetic == right_phonetic:
        return 88, 'Arabic-English phonetic name'
    phonetic_similarity = SequenceMatcher(None, left_phonetic, right_phonetic).ratio()
    if left_phonetic and right_phonetic and phonetic_similarity >= 0.86:
        return int(phonetic_similarity * 85), 'similar Arabic-English phonetic name'
    left_tokens, right_tokens = set(left_core.split()), set(right_core.split())
    shared = left_tokens & right_tokens
    smallest = min(len(left_tokens), len(right_tokens))
    if shared and len(shared) == smallest and smallest >= 1:
        return 82, 'name subset'
    if len(left_core) >= 4 and len(right_core) >= 4 and (
            left_core in right_core or right_core in left_core):
        return 80, 'name contains'
    union = left_tokens | right_tokens
    token_score = len(shared) / len(union) if union else 0
    sequence_score = SequenceMatcher(None, left_core, right_core).ratio()
    score = max(token_score, sequence_score)
    if score >= 0.72:
        return int(score * 75), 'similar normalized name'
    return 0, ''


def _normalize_phone(value):
    return re.sub(r'\D', '', value or '')[-9:]


def _normalize_website(value):
    host = urlparse(value if '://' in (value or '') else 'https://%s' % (value or '')).netloc
    return host.casefold().removeprefix('www.')


class CsGoogleSheetSync(models.AbstractModel):
    _name = 'cs.google.sheet.sync'
    _description = 'Customer Portfolio Google Sheet Synchronization'

    @api.model
    def _settings(self):
        params = self.env['ir.config_parameter'].sudo()
        return {
            'enabled': params.get_param('era_customer_success.google_sheet_enabled') == 'True',
            'spreadsheet_id': params.get_param('era_customer_success.google_spreadsheet_id'),
            'gid': int(params.get_param('era_customer_success.google_sheet_gid') or 0),
            'credentials': params.get_param('era_customer_success.google_service_account_json'),
            'sharing_approved': params.get_param('era_customer_success.google_sharing_approved') == 'True',
            'approval_scope': params.get_param('era_customer_success.google_approval_scope'),
        }

    @api.model
    def _access_token(self, credentials_json):
        try:
            info = json.loads(credentials_json or '')
            now = int(time.time())
            header = _b64url(json.dumps({'alg': 'RS256', 'typ': 'JWT'}).encode())
            claims = _b64url(json.dumps({
                'iss': info['client_email'], 'scope': SHEETS_SCOPE,
                'aud': info.get('token_uri', 'https://oauth2.googleapis.com/token'),
                'iat': now, 'exp': now + 3600,
            }, separators=(',', ':')).encode())
            unsigned = '%s.%s' % (header, claims)
            key = serialization.load_pem_private_key(info['private_key'].encode(), password=None)
            signature = key.sign(unsigned.encode(), padding.PKCS1v15(), hashes.SHA256())
            assertion = '%s.%s' % (unsigned, _b64url(signature))
            response = requests.post(
                info.get('token_uri', 'https://oauth2.googleapis.com/token'), timeout=20,
                data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion})
            response.raise_for_status()
            return response.json()['access_token']
        except Exception as error:
            raise UserError(_('Google authentication failed: %s', error))

    @api.model
    def _request(self, method, url, token, **kwargs):
        response = requests.request(method, url, timeout=30, headers={
            'Authorization': 'Bearer %s' % token, 'Content-Type': 'application/json'}, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    @api.model
    def _sheet_title(self, spreadsheet_id, gid, token):
        metadata = self._request('GET', 'https://sheets.googleapis.com/v4/spreadsheets/%s' % spreadsheet_id, token)
        for sheet in metadata.get('sheets', []):
            props = sheet.get('properties', {})
            if props.get('sheetId') == gid:
                return props.get('title')
        raise UserError(_('Google Sheet tab with gid %s was not found.', gid))

    @api.model
    def _red_header_columns(self, spreadsheet_id, gid, token):
        url = ('https://sheets.googleapis.com/v4/spreadsheets/%s'
               '?includeGridData=true&fields=sheets(properties(sheetId),data.rowData.values.effectiveFormat.textFormat.foregroundColor)'
               % spreadsheet_id)
        metadata = self._request('GET', url, token)
        for sheet in metadata.get('sheets', []):
            if sheet.get('properties', {}).get('sheetId') != gid:
                continue
            values = ((sheet.get('data') or [{}])[0].get('rowData') or [{}])[0].get('values', [])
            red_columns = set()
            for index, cell in enumerate(values[:20]):
                color = cell.get('effectiveFormat', {}).get('textFormat', {}).get('foregroundColor', {})
                red, green, blue = color.get('red', 0), color.get('green', 0), color.get('blue', 0)
                if red >= 0.65 and red >= green * 1.35 and red >= blue * 1.35:
                    red_columns.add(chr(ord('A') + index))
            return red_columns
        raise UserError(_('Google Sheet tab with gid %s was not found.', gid))

    @api.model
    def _validation_options_by_cell(self, spreadsheet_id, title, start_row, end_row, token):
        if end_row < start_row:
            return {}
        sheet_range = "'%s'!A%s:T%s" % (title.replace("'", "''"), start_row, end_row)
        url = ('https://sheets.googleapis.com/v4/spreadsheets/%s'
               '?includeGridData=true&ranges=%s&fields='
               'sheets(data(rowData(values(dataValidation(condition(type,values))))))'
               % (spreadsheet_id, quote(sheet_range, safe='')))
        metadata = self._request('GET', url, token)
        row_data = (((metadata.get('sheets') or [{}])[0].get('data') or [{}])[0]
                    .get('rowData') or [])
        options_by_cell = {}
        range_cache = {}
        for row_offset, row in enumerate(row_data):
            for column_index, cell in enumerate((row or {}).get('values', [])[:20]):
                condition = cell.get('dataValidation', {}).get('condition', {})
                options = self._validation_condition_options(
                    condition, spreadsheet_id, title, token, range_cache)
                if options:
                    column = chr(ord('A') + column_index)
                    options_by_cell[(start_row + row_offset, column)] = options
        return options_by_cell

    @api.model
    def _validation_condition_options(self, condition, spreadsheet_id, title, token, cache=None):
        condition_type = condition.get('type')
        raw_values = [item.get('userEnteredValue', '')
                      for item in condition.get('values', [])]
        if condition_type == 'ONE_OF_LIST':
            return [value for value in raw_values if value != '']
        if condition_type != 'ONE_OF_RANGE' or not raw_values:
            return []
        source_range = raw_values[0].lstrip('=')
        if '!' not in source_range:
            source_range = "'%s'!%s" % (title.replace("'", "''"), source_range)
        cache = cache if cache is not None else {}
        if source_range not in cache:
            base = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values' % spreadsheet_id
            data = self._request(
                'GET', '%s/%s' % (base, quote(source_range, safe='')), token)
            cache[source_range] = [str(value) for row in data.get('values', [])
                                   for value in row if value not in ('', None)]
        return cache[source_range]

    @api.model
    def _validated_dropdown_value(self, value, options, column, header='', multiple=False):
        if not options or value in ('', False, None):
            return value
        if multiple:
            raw_values = value if isinstance(value, (list, tuple, set)) else re.split(
                r'\s*[,;\n]\s*', str(value))
            validated = []
            for raw_value in raw_values:
                if raw_value in ('', False, None):
                    continue
                option = self._validated_dropdown_value(
                    raw_value, options, column, header, multiple=False)
                if option not in validated:
                    validated.append(option)
            return ', '.join(map(str, validated))
        text = str(value).strip()
        exact = [option for option in options if str(option).strip().casefold() == text.casefold()]
        if exact:
            return exact[0]
        normalized = _normalize_text(text)
        normalized_matches = [option for option in options
                              if _normalize_text(str(option)) == normalized]
        if len(normalized_matches) == 1:
            return normalized_matches[0]
        raise UserError(_(
            'Value "%s" is not allowed for Google Sheet column %s (%s). Allowed values: %s',
            value, column, header or column, ', '.join(map(str, options))))

    @api.model
    def _matching_columns(self, headers):
        columns = {}
        for index, header in enumerate(headers):
            normalized = _normalize_text(header)
            for key, aliases in MATCH_HEADER_ALIASES.items():
                if normalized in aliases:
                    columns[key] = index
        return columns

    @api.model
    def _approved_alias_account(self, customer_name, accounts):
        key = customer_alias_key(customer_name)
        if not key:
            return self.env['cs.account']
        alias = self.env['cs.customer.match.alias'].sudo().search([
            ('company_id', '=', self.env.company.id),
            ('alias_key', '=', key), ('state', '=', 'approved'),
            ('active', '=', True), ('account_id', '!=', False),
        ], limit=1)
        return alias.account_id if alias.account_id in accounts else self.env['cs.account']

    @api.model
    def _remember_customer_alias(self, customer_name, account=False, confidence=0, reason=''):
        key = customer_alias_key(customer_name)
        if not key:
            return self.env['cs.customer.match.alias']
        Alias = self.env['cs.customer.match.alias'].sudo()
        alias = Alias.search([
            ('company_id', '=', self.env.company.id), ('alias_key', '=', key),
        ], limit=1)
        values = {
            'alias_name': customer_name,
            'confidence': confidence,
            'reason': reason,
            'last_seen_on': fields.Datetime.now(),
        }
        if account and not (alias.source == 'manual' and alias.state == 'approved'):
            values.update({'account_id': account.id, 'state': 'approved', 'source': 'automatic'})
        elif not account and not alias:
            values.update({'state': 'pending', 'source': 'automatic'})
        if alias:
            alias.write(values)
            return alias
        return Alias.create(dict(values, company_id=self.env.company.id))

    @api.model
    def _account_operational_aliases(self, accounts):
        aliases = {account.id: [] for account in accounts}
        if not accounts:
            return aliases
        account_by_partner = {
            account.partner_id.commercial_partner_id.id: account for account in accounts
        }
        partner_ids = list(account_by_partner)
        for account in accounts:
            partner = account.partner_id.commercial_partner_id
            if partner.ref:
                aliases[account.id].append(partner.ref)
            aliases[account.id].extend(
                partner.child_ids.filtered('is_company').mapped('name'))
        if 'project.project' in self.env.registry:
            projects = self.env['project.project'].sudo().search([
                ('partner_id', 'child_of', partner_ids),
            ])
            for project in projects:
                account = account_by_partner.get(project.partner_id.commercial_partner_id.id)
                if account and project.name:
                    aliases[account.id].append(project.name)
        orders = self.env['sale.order'].sudo().search([
            ('partner_id', 'child_of', partner_ids),
            ('client_order_ref', '!=', False),
        ])
        for order in orders:
            account = account_by_partner.get(order.partner_id.commercial_partner_id.id)
            if account and order.client_order_ref:
                aliases[account.id].append(order.client_order_ref)
        return {account_id: list(dict.fromkeys(filter(None, names)))
                for account_id, names in aliases.items()}

    @api.model
    def _match_account(self, row, columns, accounts, account_aliases=None):
        values = {key: row[index] if index < len(row) else '' for key, index in columns.items()}
        alias_account = self._approved_alias_account(values.get('name'), accounts)
        if alias_account:
            return alias_account, 100, 'approved alias'
        account_aliases = account_aliases if account_aliases is not None else self._account_operational_aliases(accounts)
        scored = []
        for account in accounts:
            partner = account.partner_id.commercial_partner_id
            score, reasons = 0, []
            if values.get('email') and partner.email and values['email'].strip().casefold() == partner.email.strip().casefold():
                score += 100
                reasons.append('email')
            if values.get('website') and partner.website and _normalize_website(values['website']) == _normalize_website(partner.website):
                score += 80
                reasons.append('website')
            if values.get('phone') and partner.phone and _normalize_phone(values['phone']) == _normalize_phone(partner.phone):
                score += 80
                reasons.append('phone')
            if values.get('vat') and partner.vat and _normalize_text(values['vat']) == _normalize_text(partner.vat):
                score += 100
                reasons.append('VAT')
            candidate_names = [partner.name] + account_aliases.get(account.id, [])
            name_score, name_reason, best_name = max(
                ((*_customer_name_score(values.get('name'), name), name)
                 for name in candidate_names),
                key=lambda item: item[0], default=(0, '', ''))
            if name_score:
                score += name_score
                reasons.append('%s%s' % (
                    name_reason,
                    ' via operational alias' if best_name != partner.name else ''))
            if score:
                scored.append((score, account.id, account, ', '.join(reasons)))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        if not scored or scored[0][0] < 80 or (len(scored) > 1 and scored[0][0] - scored[1][0] < 20):
            return False, 0, 'manual review required'
        return scored[0][2], scored[0][0], scored[0][3]

    @api.model
    def _match_account_with_ai(self, row, columns, accounts, account_aliases=None):
        account_aliases = account_aliases if account_aliases is not None else self._account_operational_aliases(accounts)
        account, confidence, reason = self._match_account(
            row, columns, accounts, account_aliases=account_aliases)
        if account:
            return account, confidence, reason
        values = {key: row[index] if index < len(row) else '' for key, index in columns.items()}
        scored = []
        for candidate in accounts:
            partner = candidate.partner_id.commercial_partner_id
            name_score, _reason = _customer_name_score(values.get('name'), partner.name)
            if name_score:
                scored.append((name_score, candidate.id, candidate, 'candidate'))
        # Different scripts (Arabic/English) can have no lexical overlap. Give AI
        # a bounded broad candidate set rather than silently skipping that customer.
        if not scored:
            scored = [(0, candidate.id, candidate, 'broad candidate')
                      for candidate in accounts[:100]]
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return self._ai_match_account(values, scored, accounts, account_aliases=account_aliases)

    @api.model
    def _ai_match_account(self, row_values, scored, accounts, account_aliases=None):
        agent = self.env.ref(
            'era_customer_success.cs_customer_match_agent_v2', raise_if_not_found=False)
        if not agent:
            return False, 0, 'manual review required'
        if not scored:
            return False, 0, 'no candidate; manual review required'
        broad = any(item[3] == 'broad candidate' for item in scored)
        candidates = [item[2] for item in scored[:100 if broad else 20]]
        candidate_lines = []
        for account in candidates:
            partner = account.partner_id.commercial_partner_id
            candidate_lines.append(json.dumps({
                'account_id': account.id,
                'name': partner.name or '',
                'email': partner.email or '',
                'phone': partner.phone or '',
                'website': partner.website or '',
                'vat': partner.vat or '',
                'known_aliases': (account_aliases or {}).get(account.id, []),
            }, ensure_ascii=False))
        prompt = 'SHEET ROW:\n%s\nCANDIDATES:\n%s' % (
            json.dumps(row_values, ensure_ascii=False), '\n'.join(candidate_lines))
        try:
            response = agent.with_user(self.env.ref('base.user_root')).get_direct_response(prompt=prompt)
            result = _cs_extract_json(response[0] if response else '')
        except Exception:
            return False, 0, 'AI unavailable; manual review required'
        if not isinstance(result, dict):
            return False, 0, 'invalid AI result; manual review required'
        try:
            account_id = int(result.get('account_id') or 0)
            confidence = int(result.get('confidence') or 0)
        except (TypeError, ValueError):
            return False, 0, 'invalid AI result; manual review required'
        account = accounts.filtered(lambda candidate: candidate.id == account_id)[:1]
        if account and account not in candidates:
            account = accounts.browse()
        if not account or confidence < 90:
            return False, confidence, 'low-confidence AI result; manual review required'
        return account, confidence, 'AI: %s' % (result.get('reason') or 'multi-signal match')

    @api.model
    def _account_values(self, account):
        adoption = self.env['cs.adoption.assessment'].sudo().search([
            ('cs_account_id', '=', account.id), ('state', '=', 'confirmed')],
            order='assessment_date desc, id desc', limit=1)
        subscription = self.env['sale.order'].sudo().search([
            ('partner_id.commercial_partner_id', '=', account.partner_id.id),
            ('is_subscription', '=', True), ('subscription_state', '=', '3_progress')],
            order='next_invoice_date asc, id desc', limit=1)
        plan = ''
        for field_name in ('plan_id', 'recurring_plan_id'):
            if field_name in subscription._fields and subscription[field_name]:
                plan = subscription[field_name].display_name
                break
        active_users = adoption.active_users_30d if adoption and adoption.active_users_30d else ''
        return {
            'A': account.csm_user_id.name or '',
            'B': account.csm_user_id.phone or '',
            'C': account.csm_user_id.email or '',
            'G': plan,
            'H': account.partner_id.industry_id.name or '',
            'K': active_users,
            'N': ('%.2f%%' % account.latest_adoption_score) if account.latest_adoption_date else '',
            'O': account.partner_id.website or '',
        }

    @api.model
    def _sheet_row_values(self, row):
        values = {}
        for index, column in enumerate(SHEET_ACCOUNT_FIELDS):
            value = row[index] if index < len(row) else ''
            if column in ('E', 'F') and value:
                value = str(value).strip()
            elif column in ('I', 'J', 'K') and value:
                try:
                    value = int(float(value))
                except (TypeError, ValueError):
                    value = False
            values[SHEET_ACCOUNT_FIELDS[column]] = value
        return values

    @api.model
    def _account_values_from_sheet_row(self, row, allowed_columns, clear_missing=False):
        sheet_values = self._sheet_row_values(row)
        values = {}
        for column, field_name in SHEET_ACCOUNT_FIELDS.items():
            if column not in allowed_columns:
                continue
            value = sheet_values.get(field_name)
            if clear_missing:
                values[field_name] = value if value not in ('', None) else False
            elif value not in ('', False, None):
                values[field_name] = value
        return values

    @api.model
    def _approved_account_field_names(self):
        settings = self._settings()
        approved_columns = {
            column.strip().upper()
            for column in (settings.get('approval_scope') or '').split(',')
            if column.strip()
        }
        return {field_name for column, field_name in SHEET_ACCOUNT_FIELDS.items()
                if column in approved_columns}

    @api.model
    def _approved_dropdown_options(self, scan_end_row=200):
        settings = self._settings()
        if not all((settings.get('spreadsheet_id'), settings.get('credentials'))):
            return {}
        approved_columns = {
            column.strip().upper()
            for column in (settings.get('approval_scope') or '').split(',')
            if column.strip()
        }
        if not approved_columns:
            return {}
        token = self._access_token(settings['credentials'])
        title = self._sheet_title(settings['spreadsheet_id'], settings['gid'], token)
        red_columns = self._red_header_columns(
            settings['spreadsheet_id'], settings['gid'], token)
        allowed_columns = approved_columns & red_columns
        validations = self._validation_options_by_cell(
            settings['spreadsheet_id'], title, 2, scan_end_row, token)
        by_field = {}
        for (_row_number, column), options in validations.items():
            field_name = SHEET_ACCOUNT_FIELDS.get(column)
            if column not in allowed_columns or not field_name:
                continue
            entry = by_field.setdefault(field_name, {
                'column': column,
                'options': [],
                'multiple': field_name in MULTI_SELECT_SHEET_FIELDS,
            })
            for option in options:
                if option not in entry['options']:
                    entry['options'].append(option)
        return by_field

    @api.model
    def action_sync(self):
        settings = self._settings()
        if not settings.get('sharing_approved') or not (settings.get('approval_scope') or '').strip():
            raise UserError(_('Approve and document the information scope before sharing data with Odoo through Google Sheet.'))
        if not all((settings['spreadsheet_id'], settings['credentials'])):
            raise UserError(_('Configure the Google Spreadsheet ID and service-account credentials first.'))
        token = self._access_token(settings['credentials'])
        title = self._sheet_title(settings['spreadsheet_id'], settings['gid'], token)
        red_columns = self._red_header_columns(settings['spreadsheet_id'], settings['gid'], token)
        approved_columns = {
            column.strip().upper() for column in (settings.get('approval_scope') or '').split(',')
            if column.strip()
        }
        if not approved_columns:
            raise UserError(_('Scan the Google Sheet scope before approving synchronization.'))
        if approved_columns - red_columns:
            raise UserError(_(
                'Synchronization stopped: approved columns %s are not red in the current Google Sheet. '
                'Review the sheet colors and obtain approval before changing the scope.',
                ', '.join(sorted(approved_columns - red_columns))))
        escaped = title.replace("'", "''")
        base = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values' % settings['spreadsheet_id']
        rows = self._request('GET', "%s/'%s'!A1:T" % (base, escaped), token).get('values', [])
        if not rows:
            raise UserError(_('The Google Sheet is empty.'))
        headers = rows[0] + [''] * (20 - len(rows[0]))
        for column, expected in ERA_COLUMNS.items():
            if column not in approved_columns:
                continue
            index = ord(column) - ord('A')
            if headers[index].strip() != expected:
                raise UserError(_('Column %s must be "%s" before synchronization.', column, expected))
        if headers[3].strip() != 'Customer Name':
            raise UserError(_('Column D must be "Customer Name" before synchronization.'))
        matching_columns = self._matching_columns(headers)
        if 'name' not in matching_columns:
            matching_columns['name'] = 3
        updates = []
        validations = self._validation_options_by_cell(
            settings['spreadsheet_id'], title, 2, len(rows), token)
        matched = 0
        accounts = self.env['cs.account'].sudo().search([('company_id', '=', self.env.company.id)])
        account_aliases = self._account_operational_aliases(accounts)
        match_details = []
        for row_number, row in enumerate(rows[1:], start=2):
            account, confidence, reason = self._match_account_with_ai(
                row, matching_columns, accounts, account_aliases=account_aliases)
            if not account:
                continue
            matched += 1
            match_details.append('Row %s -> %s (%s%%: %s)' % (
                row_number, account.partner_id.display_name, confidence, reason))
            account_values = self._account_values_from_sheet_row(
                row, approved_columns & red_columns)
            if account_values:
                account_values['sheet_last_synced_on'] = fields.Datetime.now()
                account.sudo().write(account_values)
            for column, value in self._account_values(account).items():
                if column in approved_columns and column in red_columns and value not in ('', False, None):
                    value = self._validated_dropdown_value(
                        value, validations.get((row_number, column), []),
                        column, headers[ord(column) - ord('A')],
                        multiple=column == 'P')
                    updates.append({'range': "'%s'!%s%s" % (escaped, column, row_number), 'values': [[value]]})
        if updates:
            self._request('POST', '%s:batchUpdate' % base, token, json={
                'valueInputOption': 'USER_ENTERED', 'data': updates})
        self.env['cs.google.sheet.sync.log'].sudo().create({
            'company_id': self.env.company.id, 'spreadsheet_id': settings['spreadsheet_id'],
            'matched_accounts': matched, 'updated_cells': len(updates), 'state': 'success',
            'details': '\n'.join(match_details)})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Google Sheet synchronized'),
            'message': _('%s accounts matched; %s ERA-owned cells updated.', matched, len(updates)),
            'type': 'success', 'sticky': False}}

    @api.model
    def _run_match_all_sheet_customers(self, log=False):
        settings = self._settings()
        if not all((settings['spreadsheet_id'], settings['credentials'])):
            raise UserError(_('Configure the Google Spreadsheet ID and service-account credentials first.'))
        token = self._access_token(settings['credentials'])
        title = self._sheet_title(settings['spreadsheet_id'], settings['gid'], token)
        escaped = title.replace("'", "''")
        base = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values' % settings['spreadsheet_id']
        rows = self._request('GET', "%s/'%s'!A1:T" % (base, escaped), token).get('values', [])
        if not rows:
            raise UserError(_('The Google Sheet is empty.'))
        headers = rows[0] + [''] * (20 - len(rows[0]))
        if headers[3].strip() != 'Customer Name':
            raise UserError(_('Column D must be "Customer Name" before matching.'))
        matching_columns = self._matching_columns(headers)
        matching_columns.setdefault('name', 3)
        accounts = self.env['cs.account'].sudo().search([
            ('company_id', '=', self.env.company.id),
        ])
        account_aliases = self._account_operational_aliases(accounts)
        updates, details = [], []
        matched = unmatched = skipped = 0
        for row_number, row in enumerate(rows[1:], start=2):
            customer_name = row[3].strip() if len(row) > 3 and row[3] else ''
            if not customer_name:
                skipped += 1
                continue
            account, confidence, reason = self._match_account_with_ai(
                row, matching_columns, accounts, account_aliases=account_aliases)
            if account:
                matched += 1
                status = 'x'
                details.append('Row %s | %s -> %s (%s%%: %s)' % (
                    row_number, customer_name, account.partner_id.display_name,
                    confidence, reason))
                self._remember_customer_alias(
                    customer_name, account, confidence, reason)
            else:
                unmatched += 1
                status = ''
                details.append('Row %s | %s -> UNMATCHED (%s)' % (
                    row_number, customer_name, reason))
                self._remember_customer_alias(
                    customer_name, False, confidence, reason)
            # Column A is a compact marker only: x for a successful match, blank otherwise.
            updates.append({
                'range': "'%s'!A%s" % (escaped, row_number),
                'values': [[status]],
            })
        if updates:
            self._request('POST', '%s:batchUpdate' % base, token, json={
                'valueInputOption': 'USER_ENTERED', 'data': updates})
        result = {
            'matched_accounts': matched,
            'updated_cells': len(updates),
            'details': '\n'.join(details),
        }
        if log:
            log.write(dict(result, state='success'))
        else:
            self.env['cs.google.sheet.sync.log'].sudo().create({
                'company_id': self.env.company.id,
                'spreadsheet_id': settings['spreadsheet_id'],
                'job_type': 'matching', 'state': 'success', **result,
            })
        return result

    @api.model
    def action_queue_match_all_sheet_customers(self):
        queued = self.env['cs.google.sheet.sync.log'].sudo().search_count([
            ('job_type', '=', 'matching'), ('state', 'in', ('queued', 'running')),
        ])
        if queued:
            raise UserError(_('An Excel customer matching job is already queued or running.'))
        log = self.env['cs.google.sheet.sync.log'].sudo().create({
            'company_id': self.env.company.id,
            'spreadsheet_id': self._settings().get('spreadsheet_id'),
            'job_type': 'matching', 'state': 'queued',
            'details': _('Waiting for background matching.'),
        })
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Excel customer matching queued'),
            'message': _('Matching runs in the background. Open Google synchronization logs to view job %s and its result.', log.id),
            'type': 'info', 'sticky': False}}

    @api.model
    def _cron_process_customer_match_jobs(self):
        job = self.env['cs.google.sheet.sync.log'].sudo().search([
            ('job_type', '=', 'matching'), ('state', '=', 'queued'),
        ], order='create_date, id', limit=1)
        if not job:
            return True
        job.write({'state': 'running', 'details': _('Matching Excel customers in the background.')})
        try:
            with self.env.cr.savepoint():
                self._run_match_all_sheet_customers(log=job)
        except Exception as error:
            _logger.exception('Excel customer matching job %s failed: %s', job.id, error)
            job.write({'state': 'failed', 'details': str(error)})
        return True

    @api.model
    def action_sync_account(self, account, use_ai=True, all_fields=False):
        account.ensure_one()
        settings = self._settings()
        if not settings.get('sharing_approved') or not (settings.get('approval_scope') or '').strip():
            raise UserError(_('Approve the Google Sheet information scope before filling this customer record.'))
        if not all((settings['spreadsheet_id'], settings['credentials'])):
            raise UserError(_('Configure the Google Spreadsheet ID and service-account credentials first.'))
        token = self._access_token(settings['credentials'])
        title = self._sheet_title(settings['spreadsheet_id'], settings['gid'], token)
        red_columns = self._red_header_columns(settings['spreadsheet_id'], settings['gid'], token)
        approved_columns = {column.strip().upper() for column in settings['approval_scope'].split(',') if column.strip()}
        if not all_fields and approved_columns - red_columns:
            raise UserError(_('The approved Google Sheet title colors changed. Scan and approve the scope again.'))
        escaped = title.replace("'", "''")
        base = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values' % settings['spreadsheet_id']
        rows = self._request('GET', "%s/'%s'!A1:T" % (base, escaped), token).get('values', [])
        if not rows:
            raise UserError(_('The Google Sheet is empty.'))
        headers = rows[0] + [''] * (20 - len(rows[0]))
        if headers[3].strip() != 'Customer Name':
            raise UserError(_('Column D must be "Customer Name" before synchronization.'))
        matching_columns = self._matching_columns(headers)
        matching_columns.setdefault('name', 3)
        account_match, confidence, reason = False, 0, ''
        matched_row = None
        accounts = self.env['cs.account'].sudo().browse(account.id)
        account_aliases = self._account_operational_aliases(accounts)
        for row in rows[1:]:
            matcher = self._match_account_with_ai if use_ai else self._match_account
            candidate, confidence, reason = matcher(
                row, matching_columns, accounts, account_aliases=account_aliases)
            if candidate:
                account_match = candidate
                matched_row = row
                break
        if not account_match or matched_row is None:
            raise UserError(_('No sufficiently confident Google Sheet row matched this customer.'))
        allowed_columns = set(SHEET_ACCOUNT_FIELDS) if all_fields else approved_columns & red_columns
        values = self._account_values_from_sheet_row(
            matched_row, allowed_columns, clear_missing=all_fields)
        written_fields = sorted(values)
        if not written_fields:
            raise UserError(_('The matched Google Sheet row contains no values to import.'))
        values['sheet_last_synced_on'] = fields.Datetime.now()
        account.sudo().write(values)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Customer Sheet fields filled'),
            'message': _('Customer matched with %s%% confidence (%s). %s fields filled: %s.',
                         confidence, reason, len(written_fields), ', '.join(written_fields)),
            'type': 'success', 'sticky': False,
            'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    @api.model
    def action_send_account(self, account):
        account.ensure_one()
        settings = self._settings()
        if not settings.get('sharing_approved') or not (settings.get('approval_scope') or '').strip():
            raise UserError(_('Approve the Google Sheet information scope before sending this customer record.'))
        if not all((settings['spreadsheet_id'], settings['credentials'])):
            raise UserError(_('Configure the Google Spreadsheet ID and service-account credentials first.'))
        token = self._access_token(settings['credentials'])
        title = self._sheet_title(settings['spreadsheet_id'], settings['gid'], token)
        red_columns = self._red_header_columns(settings['spreadsheet_id'], settings['gid'], token)
        approved_columns = {column.strip().upper() for column in settings['approval_scope'].split(',') if column.strip()}
        if approved_columns - red_columns:
            raise UserError(_('The approved Google Sheet title colors changed. Scan and approve the scope again.'))
        escaped = title.replace("'", "''")
        base = 'https://sheets.googleapis.com/v4/spreadsheets/%s/values' % settings['spreadsheet_id']
        rows = self._request('GET', "%s/'%s'!A1:T" % (base, escaped), token).get('values', [])
        if not rows:
            raise UserError(_('The Google Sheet is empty.'))
        headers = rows[0] + [''] * (20 - len(rows[0]))
        if headers[3].strip() != 'Customer Name':
            raise UserError(_('Column D must be "Customer Name" before synchronization.'))
        matching_columns = self._matching_columns(headers)
        matching_columns.setdefault('name', 3)
        matched_row = None
        row_number = None
        single_account = self.env['cs.account'].sudo().browse(account.id)
        account_aliases = self._account_operational_aliases(single_account)
        for number, row in enumerate(rows[1:], 2):
            candidate, confidence, reason = self._match_account_with_ai(
                row, matching_columns, single_account,
                account_aliases=account_aliases)
            if candidate:
                matched_row, row_number = row, number
                break
        if matched_row is None:
            raise UserError(_('No sufficiently confident Google Sheet row matched this customer.'))
        validations = self._validation_options_by_cell(
            settings['spreadsheet_id'], title, row_number, row_number, token)
        updates = []
        for column, field_name in SHEET_ACCOUNT_FIELDS.items():
            value = account[field_name]
            if column not in approved_columns or column not in red_columns or value in ('', False, None):
                continue
            value = self._validated_dropdown_value(
                value, validations.get((row_number, column), []),
                column, headers[ord(column) - ord('A')],
                multiple=column == 'P')
            updates.append({
                'range': "'%s'!%s%s" % (escaped, column, row_number),
                'values': [[value]],
            })
        if not updates:
            raise UserError(_('This customer has no non-empty approved red fields to send to Excel.'))
        self._request('POST', '%s:batchUpdate' % base, token, json={
            'valueInputOption': 'USER_ENTERED', 'data': updates})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Customer data sent to Excel'),
            'message': _('%s approved fields sent.', len(updates)),
            'type': 'success', 'sticky': False}}

    @api.model
    def _cron_sync(self):
        if self._settings()['enabled']:
            self.action_sync()


class CsGoogleSheetSyncLog(models.Model):
    _name = 'cs.google.sheet.sync.log'
    _description = 'Google Sheet Synchronization Log'
    _order = 'create_date desc'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    job_type = fields.Selection([
        ('sync', 'Synchronization'), ('matching', 'Customer Matching'),
    ], required=True, default='sync', readonly=True)
    spreadsheet_id = fields.Char(readonly=True)
    matched_accounts = fields.Integer(readonly=True)
    updated_cells = fields.Integer(readonly=True)
    state = fields.Selection([
        ('queued', 'Queued'), ('running', 'Running'),
        ('success', 'Success'), ('failed', 'Failed'),
    ], readonly=True)
    details = fields.Text(readonly=True)
