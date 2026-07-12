from odoo.addons.ai.utils.llm_api_service import LLMApiService as BaseLLMApiService

# Whisper infers the audio format from the upload filename's extension. The
# base service uploads recordings as bare "audio" (no extension), which makes
# webm/opus recordings fail with "Invalid file format" even though webm is a
# supported container. We restore the extension from the declared content-type
# so webm — and any other supported format — is accepted.
_AUDIO_MIME_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/oga": "oga",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


class LLMApiService(BaseLLMApiService):
    def _request(
        self,
        method: str,
        endpoint: str,
        headers: dict[str, str],
        body: dict,
        data: dict | None = None,
        files: dict | None = None,
        params: dict | None = None,
        base_url: str | None = None,
        timeout: int = 30,
    ) -> dict:
        files = self._ensure_audio_filename_extension(files)
        return super()._request(
            method,
            endpoint,
            headers,
            body,
            data=data,
            files=files,
            params=params,
            base_url=base_url,
            timeout=600,
        )

    @staticmethod
    def _ensure_audio_filename_extension(files):
        """Give an extension-less uploaded audio file a proper filename.

        Whisper determines the format from the filename extension; the base
        service sends bare "audio", so webm/opus recordings are rejected. When
        the filename has no extension we derive one from the content-type.
        """
        if not files:
            return files
        entry = files.get("file")
        if not (isinstance(entry, (tuple, list)) and len(entry) >= 3):
            return files
        filename, payload, content_type = entry[0], entry[1], entry[2]
        if filename and "." in filename:
            return files
        base_type = (content_type or "").split(";")[0].strip().lower()
        ext = _AUDIO_MIME_EXTENSIONS.get(base_type)
        if not ext:
            return files
        patched = dict(files)
        patched["file"] = (f"audio.{ext}", payload, content_type)
        return patched
