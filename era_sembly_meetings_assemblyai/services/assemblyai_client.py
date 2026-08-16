# -*- coding: utf-8 -*-
"""Small asynchronous AssemblyAI REST client.

Odoo owns the queue and persists the remote id between cron runs, so the
blocking SDK polling loop is deliberately not used here. Parameter names are
from AssemblyAI's live OpenAPI reference, checked on 2026-08-15.
"""
import json
import random
import time

import requests


RETRYABLE = {429, 500, 502, 503, 504}


class AssemblyAIError(Exception):
    def __init__(self, message, status=None, retryable=False, uncertain=False,
                 retry_after=None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


class AssemblyAIClient:
    """Upload, submit, poll and delete without retaining the API key in logs."""

    def __init__(self, api_key, region='us', timeout=60, upload_timeout=900,
                 session=None, sleep=time.sleep):
        if not (api_key or '').strip():
            raise AssemblyAIError("AssemblyAI API key is not configured")
        if region not in ('us', 'eu'):
            raise AssemblyAIError("AssemblyAI region must be us or eu")
        self.base_url = ('https://api.eu.assemblyai.com' if region == 'eu'
                         else 'https://api.assemblyai.com')
        self.timeout = (10, timeout)
        self.upload_timeout = (10, upload_timeout)
        self.session = session or requests.Session()
        # AssemblyAI expects the raw key, never "Bearer <key>".
        self.session.headers.update({'Authorization': api_key.strip()})
        self.sleep = sleep

    @staticmethod
    def _detail(response):
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and (data.get('error') or data.get('message')):
            return str(data.get('error') or data.get('message'))[:500]
        return (response.text or response.reason or 'Unknown API error')[:500]

    @staticmethod
    def _retry_after(response):
        value = response.headers.get('Retry-After')
        try:
            return max(0, int(value)) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _raise(self, response):
        status = response.status_code
        raise AssemblyAIError(
            "AssemblyAI API %s: %s" % (status, self._detail(response)),
            status=status, retryable=status in RETRYABLE,
            retry_after=self._retry_after(response))

    @staticmethod
    def _json(response):
        try:
            data = response.json()
        except ValueError as exc:
            raise AssemblyAIError("AssemblyAI returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise AssemblyAIError("AssemblyAI returned an invalid response")
        return data

    def _request(self, method, path, attempts=3, **kwargs):
        url = self.base_url + path
        for attempt in range(attempts):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.ReadTimeout as exc:
                raise AssemblyAIError(
                    "AssemblyAI response timed out", retryable=method in ('GET', 'DELETE'),
                    uncertain=method == 'POST') from exc
            except (requests.ConnectTimeout, requests.ConnectionError) as exc:
                if method == 'POST' or attempt + 1 >= attempts:
                    raise AssemblyAIError(
                        "AssemblyAI connection failed", retryable=method != 'POST',
                        uncertain=method == 'POST') from exc
                self.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code < 400:
                return response
            if response.status_code in RETRYABLE and attempt + 1 < attempts:
                delay = self._retry_after(response)
                self.sleep(delay if delay is not None
                           else random.uniform(0, min(2 ** attempt, 30)))
                continue
            self._raise(response)
        raise AssemblyAIError("AssemblyAI request failed")  # pragma: no cover

    def upload_file(self, stream):
        """Upload seekable raw bytes and return AssemblyAI's private URL."""
        start = stream.tell()
        stream.seek(0, 2)
        size = stream.tell() - start
        stream.seek(start)
        if size <= 0:
            raise AssemblyAIError("The media file is empty")
        try:
            response = self._request(
                'POST', '/v2/upload', attempts=1, data=stream,
                headers={'Content-Type': 'application/octet-stream'},
                timeout=self.upload_timeout)
        except AssemblyAIError as exc:
            # An unknown upload may leave a temporary media object, but it
            # cannot create a billable transcript. Retrying is therefore safer
            # than freezing the meeting in the submit-only uncertain state.
            exc.uncertain = False
            exc.retryable = True
            raise
        upload_url = self._json(response).get('upload_url')
        if not upload_url:
            raise AssemblyAIError("AssemblyAI upload returned no upload_url")
        return upload_url

    def submit(self, upload_url):
        response = self._request(
            'POST', '/v2/transcript', attempts=1,
            json={
                'audio_url': upload_url,
                'speech_models': ['universal-2'],
                'language_code': 'ar',
                'speaker_labels': True,
                'multichannel': False,
            }, timeout=self.timeout)
        transcript_id = self._json(response).get('id')
        if not transcript_id:
            raise AssemblyAIError("AssemblyAI submit returned no transcript id")
        return transcript_id

    def get_transcript(self, transcript_id):
        data = self._json(self._request(
            'GET', '/v2/transcript/%s' % transcript_id,
            timeout=self.timeout))
        if data.get('status') not in ('queued', 'processing', 'completed', 'error'):
            raise AssemblyAIError("AssemblyAI returned an unknown transcript status")
        return data

    def find_transcript(self, upload_url, created_on=None):
        """Recover a submit whose response was lost, keyed by private media URL."""
        dates = list(dict.fromkeys(created_on or [None]))
        for day in dates:
            before_id = None
            exhausted = True
            for _page in range(100):
                params = {'limit': 200}
                if day:
                    params['created_on'] = str(day)
                if before_id:
                    params['before_id'] = before_id
                data = self._json(self._request(
                    'GET', '/v2/transcript', params=params,
                    timeout=self.timeout))
                transcripts = data.get('transcripts') or []
                for transcript in transcripts:
                    if transcript.get('audio_url') == upload_url:
                        return transcript
                if len(transcripts) < 200:
                    exhausted = False
                    break
                before_id = transcripts[-1].get('id')
                if not before_id:
                    exhausted = False
                    break
            if exhausted:
                raise AssemblyAIError(
                    "Recovery search exceeded 20,000 transcripts; refusing "
                    "to risk duplicate billing", uncertain=True)
        return None

    def delete_transcript(self, transcript_id):
        self._request('DELETE', '/v2/transcript/%s' % transcript_id,
                      timeout=self.timeout)
        return True
