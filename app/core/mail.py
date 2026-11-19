import logging
from email.utils import formataddr

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(Exception):
    """Raised when an email provider request fails."""


def _sanitize_header_text(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


def _format_address(name: str, address: str) -> str:
    clean_addr = _sanitize_header_text(address)
    if "<" in clean_addr or not name:
        return clean_addr
    return formataddr((_sanitize_header_text(name), clean_addr))


class MailClient:
    """Provider-agnostic transactional email client (console | senviok)."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _build_payload(
        self,
        *,
        to_email: str,
        to_name: str,
        subject: str,
        html: str,
    ) -> dict[str, str]:
        return {
            "from": _format_address(self.settings.MAIL_FROM_NAME, self.settings.DEFAULT_FROM_EMAIL),
            "to": _format_address(to_name, to_email),
            "subject": _sanitize_header_text(subject),
            "html": html,
        }

    def _senviok_headers(self) -> dict[str, str]:
        if not self.settings.MAIL_API_KEY:
            raise EmailDeliveryError(
                "MAIL_API_KEY must be configured when MAIL_PROVIDER='senviok'."
            )
        return {
            "Authorization": f"Bearer {self.settings.MAIL_API_KEY}",
            "Content-Type": "application/json",
        }

    def _parse_senviok_response(self, resp: httpx.Response) -> str:
        if resp.status_code >= 400:
            raise EmailDeliveryError(f"Senviok API error: HTTP {resp.status_code}: {resp.text}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise EmailDeliveryError("Senviok API returned invalid JSON.") from exc
        return str(body.get("id", "")) if isinstance(body, dict) else ""

    async def send(
        self,
        *,
        to_email: str,
        to_name: str = "",
        subject: str,
        html: str,
    ) -> str:
        provider = self.settings.MAIL_PROVIDER.lower()
        payload = self._build_payload(
            to_email=to_email, to_name=to_name, subject=subject, html=html
        )
        if provider == "console":
            logger.info("Console email to %s | subject=%s", payload["to"], payload["subject"])
            return "console-stub"
        if provider == "senviok":
            base_url = self.settings.MAIL_BASE_URL.rstrip("/")
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=15) as http:
                    resp = await http.post(
                        "/v1/emails", json=payload, headers=self._senviok_headers()
                    )
            except httpx.HTTPError as exc:
                raise EmailDeliveryError(f"Senviok request failed: {exc}") from exc
            return self._parse_senviok_response(resp)
        raise EmailDeliveryError(f"Unsupported MAIL_PROVIDER: {self.settings.MAIL_PROVIDER}")

    def send_sync(
        self,
        *,
        to_email: str,
        to_name: str = "",
        subject: str,
        html: str,
    ) -> str:
        provider = self.settings.MAIL_PROVIDER.lower()
        payload = self._build_payload(
            to_email=to_email, to_name=to_name, subject=subject, html=html
        )
        if provider == "console":
            logger.info("Console email to %s | subject=%s", payload["to"], payload["subject"])
            return "console-stub"
        if provider == "senviok":
            base_url = self.settings.MAIL_BASE_URL.rstrip("/")
            try:
                with httpx.Client(base_url=base_url, timeout=15) as http:
                    resp = http.post("/v1/emails", json=payload, headers=self._senviok_headers())
            except httpx.HTTPError as exc:
                raise EmailDeliveryError(f"Senviok request failed: {exc}") from exc
            return self._parse_senviok_response(resp)
        raise EmailDeliveryError(f"Unsupported MAIL_PROVIDER: {self.settings.MAIL_PROVIDER}")


mail_client = MailClient(get_settings())
