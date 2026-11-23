from __future__ import annotations

import base64
import binascii
import io

import httpx
from PIL import Image, UnidentifiedImageError

from .config import IntegrationSettings


class OcrError(Exception):
    """Raised when the OCR service is not configured or the call fails.

    Carries a short machine-readable reason; never the credential itself.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# A teacher may only hand the service a small synthetic teaching checklist; anything larger is
# rejected before the call so the endpoint cannot be abused as a general-purpose file proxy.
# The limit applies to the DECODED bytes, not the base64 text.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
# The declared type (data URL prefix) must match what the bytes actually decode to.
_DECLARED_FORMATS = {
    "data:image/png;base64,": "PNG",
    "data:image/jpeg;base64,": "JPEG",
    "data:image/webp;base64,": "WEBP",
}


def validate_image_data_url(image_data_url: str) -> str:
    """Return the data URL only if it survives strict checking; raise OcrError otherwise.

    Strict checks, in order: declared type is one we accept; the base64 payload decodes
    (validate=True rejects stray characters); the decoded image is at most 2 MiB; the bytes
    really parse as an image; and the parsed format matches the declared one. Nothing here
    touches the network, so a rejected image costs zero upstream calls.
    """
    if not isinstance(image_data_url, str):
        raise OcrError("ocr_invalid_image")
    declared = next((f for prefix, f in _DECLARED_FORMATS.items() if image_data_url.startswith(prefix)), None)
    if declared is None:
        raise OcrError("ocr_invalid_image")
    payload = image_data_url.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise OcrError("ocr_invalid_image") from exc
    if len(raw) > _MAX_IMAGE_BYTES:
        raise OcrError("ocr_image_too_large")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
            actual_format = image.format
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise OcrError("ocr_invalid_image") from exc
    if actual_format != declared:
        raise OcrError("ocr_image_format_mismatch")
    return image_data_url


class OcrClient:
    """Read-only OCR client used only for the teacher-side checklist text draft.

    Sends the image to the OpenAI-compatible chat endpoint and returns the transcription as a
    plain string. It never writes anything: the draft is returned to the teacher for manual
    checking and never becomes a case fact or a scoring input.
    """

    def __init__(self, settings: IntegrationSettings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.ocr_enabled

    def extract_draft(self, image_data_url: str) -> str:
        """Return the transcription of the image; never an empty or fabricated completion."""
        if not self.enabled:
            raise OcrError("ocr_unconfigured")
        validate_image_data_url(image_data_url)
        try:
            resp = httpx.post(
                self.settings.ocr_base_url,
                json={"model": self.settings.ocr_model, "messages": [
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                        {"type": "text", "text": "请逐字转写图中的检查单文字，缺字或缺项处如实标注，不要补造。"},
                    ]},
                ]},
                headers={"Authorization": f"Bearer {self.settings.ocr_api_key}"},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise OcrError("ocr_network_error") from exc
        if resp.status_code != 200:
            raise OcrError("ocr_http_error")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise OcrError("ocr_bad_response") from exc
        if not isinstance(content, str) or not content.strip():
            raise OcrError("ocr_bad_response")
        return content
