# -*- coding: utf-8 -*-
import base64
import json
import logging
import re
import secrets
import time
from typing import Optional

import requests

from odoo import _, models
from odoo.exceptions import UserError

try:
    import jwt
except ImportError:  # pragma: no cover - runtime dependency check
    jwt = None

try:
    from cryptography.hazmat.primitives import serialization
except ImportError:  # pragma: no cover - runtime dependency check
    serialization = None

_logger = logging.getLogger(__name__)


class GosiClient(models.AbstractModel):
    _name = 'gosi.client'
    _description = 'GOSI API Client'

    _cached_access_token: Optional[str] = None
    _cached_access_token_exp: float = 0.0

    def _get_config(self):
        ICP = self.env['ir.config_parameter'].sudo()
        base_url = ICP.get_param('era_gosi.base_url', default='https://api.gosi.gov.sa')
        proxy_url = ICP.get_param('era_gosi.proxy_url')
        proxy_token = ICP.get_param('era_gosi.proxy_token')
        registration_number = ICP.get_param('era_gosi.registration_number')
        client_id = ICP.get_param('era_gosi.client_id')
        client_secret = ICP.get_param('era_gosi.client_secret')
        private_key = ICP.get_param('era_gosi.private_key')
        private_key_password = ICP.get_param('era_gosi.private_key_password')
        x_api_key = ICP.get_param('era_gosi.x_api_key', default='L6LK4GEAAIVrQbVo4AOVrQk781kvIT3X')
        try:
            timeout_seconds = int(ICP.get_param('era_gosi.timeout_seconds', default=30))
        except ValueError:
            timeout_seconds = 30

        use_proxy = bool(proxy_url and proxy_url.strip())
        missing = []
        if not use_proxy:
            if not registration_number:
                missing.append(_('registration number'))
            if not client_id:
                missing.append(_('client id'))
            if not client_secret:
                missing.append(_('client secret'))
            if not private_key:
                missing.append(_('private key'))
            if missing:
                raise UserError(
                    _('Configure GOSI %s in Settings > General Settings.') % ', '.join(missing)
                )

        return {
            'base_url': (base_url or '').rstrip('/'),
            'registration_number': (registration_number or '').strip(),
            'client_id': (client_id or '').strip(),
            'client_secret': (client_secret or '').strip(),
            'private_key': private_key or '',
            'private_key_password': private_key_password or None,
            'x_api_key': x_api_key.strip(),
            'timeout': timeout_seconds if timeout_seconds > 0 else 30,
            'proxy_url': (proxy_url or '').strip(),
            'proxy_token': (proxy_token or '').strip(),
        }

    def _normalize_private_key(self, private_key: str) -> str:
        if not private_key:
            return private_key
        key = private_key.strip().replace('\\n', '\n')
        key = key.replace('\r\n', '\n').replace('\r', '\n')
        if '-----BEGIN' in key and '-----END' in key:
            match = re.search(r'(-----BEGIN [^-]+-----)(.*?)(-----END [^-]+-----)', key, re.DOTALL)
            if match:
                header, body, footer = match.group(1), match.group(2), match.group(3)
                body = re.sub(r'\s+', '', body)
                if body:
                    wrapped = '\n'.join(body[i:i + 64] for i in range(0, len(body), 64))
                    return f'{header}\n{wrapped}\n{footer}'
        return key

    def _b64url_uint(self, value: int) -> str:
        if value == 0:
            return 'AA'
        length = (value.bit_length() + 7) // 8
        data = value.to_bytes(length, 'big')
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    def _load_private_key(self, private_key_pem: str, password: Optional[str]):
        if not serialization:
            raise UserError(_('Missing python dependency: cryptography'))
        try:
            return serialization.load_pem_private_key(
                private_key_pem.encode('utf-8'),
                password=password.encode('utf-8') if password else None,
            )
        except TypeError as exc:
            message = str(exc)
            if 'password was not given' in message.lower():
                raise UserError(_('GOSI private key is encrypted; set the private key password in Settings.'))
            raise
        except Exception as exc:
            _logger.exception('Failed to parse GOSI private key')
            raise UserError(_('Invalid GOSI private key: %s') % exc)

    def _build_public_jwk(self, private_key) -> dict:
        public_numbers = private_key.public_key().public_numbers()
        return {
            'kty': 'RSA',
            'n': self._b64url_uint(public_numbers.n),
            'e': self._b64url_uint(public_numbers.e),
        }

    def _build_dpop(self, method: str, url: str, private_key: str, password: Optional[str] = None) -> str:
        if not jwt:
            raise UserError(_('Missing python dependency: PyJWT (import name: jwt)'))

        private_key_pem = self._normalize_private_key(private_key)
        private_key_obj = self._load_private_key(private_key_pem, password)
        payload = {
            'jti': secrets.token_urlsafe(16),
            'htm': method.upper(),
            'htu': url,
            'iat': int(time.time()),
        }
        headers = {
            'typ': 'dpop+jwt',
            'alg': 'RS256',
            'jwk': self._build_public_jwk(private_key_obj),
        }
        token = jwt.encode(payload, private_key_obj, algorithm='RS256', headers=headers)
        if isinstance(token, bytes):
            token = token.decode('utf-8')
        return token

    def _decode_response(self, response, service_label: str):
        content_type = (response.headers.get('Content-Type') or '').lower()
        body_text = response.text or ''
        if not body_text.strip():
            if response.status_code < 400:
                return {
                    'status_code': response.status_code,
                    'data': None,
                }
            raise UserError(_('%s returned an empty response (status %s).') % (service_label, response.status_code))

        try:
            data = response.json()
        except json.JSONDecodeError:
            _logger.error(
                '%s returned non-JSON response (status=%s content-type=%s): %s',
                service_label,
                response.status_code,
                content_type,
                body_text[:2000],
            )
            raise UserError(
                _('%s returned a non-JSON response (status %s, content-type %s).')
                % (service_label, response.status_code, content_type or 'unknown')
            )

        if response.status_code >= 400:
            if isinstance(data, dict):
                message = data.get('message') or data.get('error') or data
                details = data.get('details') if isinstance(data, dict) else None
                recommendations = data.get('recommendations') if isinstance(data, dict) else None
                if details or recommendations:
                    parts = [str(message)]
                    if details:
                        parts.append(f'Details: {details}')
                    if recommendations:
                        parts.append('Recommendations: ' + '; '.join(recommendations))
                    raise UserError(_('%s error: %s') % (service_label, ' | '.join(parts)))
            else:
                message = data
            raise UserError(_('%s error: %s') % (service_label, message))

        if isinstance(data, dict):
            data['status_code'] = response.status_code
            return data
        return {
            'status_code': response.status_code,
            'data': data,
        }

    def _proxy_request(self, path: str, payload: Optional[dict] = None):
        cfg = self._get_config()
        proxy_url = cfg.get('proxy_url')
        if not proxy_url:
            raise UserError(_('GOSI proxy URL is not configured.'))
        url = f"{proxy_url.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        if cfg.get('proxy_token'):
            headers['X-GOSI-Proxy-Token'] = cfg['proxy_token']

        _logger.info('GOSI proxy request POST %s', url)
        try:
            response = requests.request(
                method='POST',
                url=url,
                headers=headers,
                json=payload or {},
                timeout=cfg['timeout'],
            )
        except Exception as exc:
            _logger.exception('GOSI proxy request failed to %s', url)
            raise UserError(_('GOSI proxy request failed: %s') % exc)

        return self._decode_response(response, 'GOSI proxy')

    def _request(self, method: str, path: str, payload: Optional[dict] = None, token: Optional[str] = None):
        cfg = self._get_config()
        url = f"{cfg['base_url']}/{path.lstrip('/')}"
        dpop = self._build_dpop(method, url, cfg['private_key'], cfg.get('private_key_password'))
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'x-apikey': cfg['x_api_key'],
            'DPoP': dpop,
        }
        if token:
            headers['Authorization'] = f"Bearer {token}"

        _logger.info('GOSI request %s %s', method.upper(), url)
        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers,
                json=payload if payload and method.upper() != 'GET' else None,
                timeout=cfg['timeout'],
            )
        except Exception as exc:
            _logger.exception('GOSI request failed to %s', url)
            raise UserError(_('GOSI request failed: %s') % exc)

        if response.status_code == 401:
            raise UserError(_('GOSI authentication failed (401). Check credentials and DPoP configuration.'))
        if response.status_code == 403:
            raise UserError(_('GOSI request forbidden (403). Ensure access is enabled for this endpoint.'))
        if response.status_code >= 500:
            raise UserError(_('GOSI service is temporarily unavailable (status %s).') % response.status_code)
        return self._decode_response(response, 'GOSI')

    def _get_cached_access_token(self) -> Optional[str]:
        now = time.time()
        cached_token = type(self)._cached_access_token
        cached_exp = type(self)._cached_access_token_exp
        if cached_token and cached_exp - 10 > now:
            return cached_token
        return None

    def get_access_token(self, registration_number: Optional[str] = None, force: bool = False) -> str:
        cfg = self._get_config()
        reg = (registration_number or cfg.get('registration_number') or '').strip()
        if not reg and not cfg.get('proxy_url'):
            raise UserError(_('Configure GOSI registration number in Settings.'))

        if not force:
            cached = self._get_cached_access_token()
            if cached:
                return cached

        payload = {
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
        }
        if cfg.get('proxy_url'):
            proxy_payload = {
                'registration_number': reg,
                'force': bool(force),
                'client_id': cfg.get('client_id'),
                'client_secret': cfg.get('client_secret'),
                'private_key': cfg.get('private_key'),
                'private_key_password': cfg.get('private_key_password'),
            }
            data = self._proxy_request('/gosi/proxy/access-token', proxy_payload)
        else:
            data = self._request('POST', f'/v1/establishment/{reg}/access-token', payload)
        token = data.get('access_token') or data.get('accessToken') or data.get('token')
        if not token:
            raise UserError(_('GOSI did not return an access token.'))

        expires_in = data.get('expires_in') or data.get('expiresIn')
        try:
            expires_in = int(expires_in) if expires_in else None
        except (TypeError, ValueError):
            expires_in = None
        if expires_in:
            type(self)._cached_access_token = token
            type(self)._cached_access_token_exp = time.time() + expires_in

        return token

    def get_contributor_deductions(
        self,
        contributor_id: str,
        registration_number: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        if not contributor_id:
            raise UserError(_('Provide a contributor identifier (NIN or Iqama).'))

        cfg = self._get_config()
        reg = (registration_number or cfg.get('registration_number') or '').strip()
        if not reg and not cfg.get('proxy_url'):
            raise UserError(_('Configure GOSI registration number in Settings.'))
        if cfg.get('proxy_url'):
            proxy_payload = {
                'registration_number': reg,
                'contributor_id': contributor_id,
                'client_id': cfg.get('client_id'),
                'client_secret': cfg.get('client_secret'),
                'private_key': cfg.get('private_key'),
                'private_key_password': cfg.get('private_key_password'),
            }
            return self._proxy_request('/gosi/proxy/contributor', proxy_payload)
        token = access_token or self.get_access_token(registration_number=reg)
        return self._request('GET', f'/v2/establishment/{reg}/contributor/{contributor_id}', token=token)
