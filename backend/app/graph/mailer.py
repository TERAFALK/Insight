"""
Central e-postmotor och -design för ALLA utskick från Insight.

All mail (rapporter, ärenden, ordrar) går genom `send_mail` och byggs med
`render_email` + komponenthjälparna nedan, så att varje mejl får exakt samma
TERAFALK-design (logo-header, vitt kort, enhetlig typografi).
"""

import asyncio
import logging

import httpx

from app.core import app_settings

logger = logging.getLogger(__name__)

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

_MAX_SEND_ATTEMPTS = 3

# TERAFALK-logotyp (SVG, base64) — används i headern på alla mejl.
LOGO_B64 = "PHN2ZyBpZD0iTGF5ZXJfMSIgZGF0YS1uYW1lPSJMYXllciAxIiB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA2OTUuMzkgODQuMjQiPjxwYXRoIGQ9Ik0yMzYuMTgsNDU1LjU3djE1SDIwMS43NHY2OWgtMTV2LTY5SDE1Mi4zdi0xNVoiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik0yNjMuMyw0NzguMTN2Ny44aDU0djE1aC01NHYxNS44NGE3Ljc0LDcuNzQsMCwwLDAsNy42OCw3LjY4SDMzMi4zdjE1SDI3MWEyMi42MywyMi42MywwLDAsMS0yMi41Ni0yMi42N1Y0NzguMTNBMjIuNjQsMjIuNjQsMCwwLDEsMjcxLDQ1NS40NUgzMzIuM3YxNUgyNzFBNy43Myw3LjczLDAsMCwwLDI2My4zLDQ3OC4xM1oiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik00MTIuMSw1MjQuNjlsNy42OCwxNUg0MDNsLTcuNjgtMTUtOC0xNS43Mi0uMzYtLjcyYTE0Ljg3LDE0Ljg3LDAsMCwwLTEyLjcyLTcuMmgtMTV2MzguNjNoLTE1di04NGg1M2EyMi41MywyMi41MywwLDAsMSwyMi41NiwyMi41NiwyMi43NSwyMi43NSwwLDAsMS0xMy4yLDIwLjY0LDIwLDIwLDAsMCwxLTYuNDgsMS44Wm0tMTQuODgtMzguNjRhNyw3LDAsMCwwLDMuMTItLjcyLDcuNjIsNy42MiwwLDAsMCw0LjU2LTcsNy45Miw3LjkyLDAsMCwwLTIuMjgtNS41Miw3LjU2LDcuNTYsMCwwLDAtNS40LTIuMTZoLTM4djE1LjQ4WiIgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTE1Mi4zIC00NTUuNDUpIi8+PHBhdGggZD0iTTUxMi43OCw1MzkuNDRINDk2bC03LjY4LTE1LTE4LjM2LTM2LTE4LjM2LDM2LTcuNjgsMTVINDI3LjFsNy42OC0xNSwzNS4xNi02OSwzNS4xNiw2OVoiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjxwYXRoIGQ9Ik02MDQuMSw0NTUuNDV2MTVINTQyLjc4YTcuNzMsNy43MywwLDAsMC03LjY4LDcuNjh2Ny44aDU0djE1SDUzNXYzOC41MUg1MjAuMVY0NzguMTNhMjIuNjQsMjIuNjQsMCwwLDEsMjIuNTYtMjIuNjhaIiB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTUyLjMgLTQ1NS40NSkiLz48cGF0aCBkPSJNNjc4LjM4LDUzOS40NGgtMTYuOGwtNy42OC0xNS0xOC4zNi0zNi0xOC4zNiwzNi03LjY4LDE1SDU5Mi43bDcuNjgtMTUsMzUuMTYtNjksMzUuMTYsNjlaIiB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTUyLjMgLTQ1NS40NSkiLz48cGF0aCBkPSJNNzUxLjcsNTI0LjU3djE1SDcwOS4xYTI2LjA5LDI2LjA5LDAsMCwxLTExLjc2LTIuNzYsMjYuNTksMjYuNTksMCwwLDEtMTIuMTItMTIuMjMsMjYuMTIsMjYuMTIsMCwwLDEtMi43Ni0xMS43NlY0NTUuNTdoMTV2NTguNjhhMTIsMTIsMCwwLDAsMTAuMiwxMC4yWiIgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTE1Mi4zIC00NTUuNDUpIi8+PHBhdGggZD0iTTgxMSw0ODkuMTdsMzYuMzUsNTAuMjdIODI4Ljg1bC0yOS00MC4wNy0yMS4yNCwxOS4zMnYyMC43NWgtMTV2LTg0aDE1djQzbDEyLjM2LTExLjI4LDExLjE2LTEwLjIsMjMuMzktMjEuNDhoMjIuMloiIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xNTIuMyAtNDU1LjQ1KSIvPjwvc3ZnPg=="

# Designtokens — håll dem på ETT ställe så all mail följer samma palett.
_TEXT = "#141414"
_MUTED = "#6b7280"
_BORDER = "#e0e9f5"
_PANEL = "#f7faff"
_ACCENT = "#0047a3"

_DEFAULT_FOOTER = "TERAFALK AB · support@terafalk.com"


# ── Sändning ────────────────────────────────────────────────────────────────────

async def _get_token() -> str:
    from app.graph.sender import _get_token as _base
    return await _base()


def _is_transient(status: int | None) -> bool:
    """Nätverksfel (status None), 429 och 5xx är värda att försöka igen. 401 hanteras separat."""
    return status is None or status == 429 or status >= 500


async def send_mail(
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    *,
    sender: str | None = None,
    attachments: list[dict] | None = None,
) -> None:
    """Skickar ett mejl via Microsoft Graph med retry/backoff.

    Raises httpx.HTTPStatusError om alla försök misslyckas.
    """
    if not app_settings.get("graph_tenant_id"):
        logger.warning("Graph ej konfigurerat — hoppar över mejl till %s", to_email)
        return

    sender = sender or app_settings.get("support_inbox") or "support@terafalk.com"
    url = GRAPH_SEND_URL.format(sender=sender)

    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"address": to_email, "name": to_name}}],
    }
    if attachments:
        message["attachments"] = attachments
    payload = {"message": message, "saveToSentItems": False}

    last_exc: Exception | None = None
    for attempt in range(_MAX_SEND_ATTEMPTS):
        try:
            token = await _get_token()
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(url, json=payload, headers={"Authorization": f"Bearer {token}"})

            if r.status_code == 401:
                # Token kan ha blivit ogiltig — töm cachen och försök igen med ny token
                from app.graph.sender import invalidate_token
                invalidate_token()
                r.raise_for_status()

            r.raise_for_status()
            logger.info("Mejl skickat till %s: %s", to_email, subject)
            return

        except httpx.HTTPStatusError as e:
            last_exc = e
            status = e.response.status_code
            retryable = status == 401 or _is_transient(status)
            if attempt < _MAX_SEND_ATTEMPTS - 1 and retryable:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except httpx.TransportError as e:
            last_exc = e
            if attempt < _MAX_SEND_ATTEMPTS - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise

    if last_exc:
        raise last_exc


def pdf_attachment(filename: str, content_b64: str) -> dict:
    """Bygger en Graph-bilaga för en PDF (contentBytes ska vara base64)."""
    return file_attachment(filename, content_b64, "application/pdf")


def file_attachment(filename: str, content_b64: str, mime_type: str = "application/octet-stream") -> dict:
    """Bygger en generisk Graph-fileAttachment (contentBytes ska vara base64)."""
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": filename,
        "contentType": mime_type,
        "contentBytes": content_b64,
    }


# ── Design / mall ───────────────────────────────────────────────────────────────

def render_email(content_html: str, *, footer_note: str = _DEFAULT_FOOTER, preheader: str = "") -> str:
    """Lindar valfritt innehåll i den gemensamma TERAFALK-mallen (rapportstil)."""
    pre = (
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{preheader}</div>'
        if preheader else ""
    )
    return f"""\
<div style="background:#eef2f8;padding:24px 0">
  {pre}
  <div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;color:{_TEXT}">
    <div style="background:#fff;padding:22px 28px;border:1px solid {_BORDER};border-radius:8px 8px 0 0">
      <img src="data:image/svg+xml;base64,{LOGO_B64}" width="130" height="16" alt="TERAFALK" style="display:block">
    </div>
    <div style="background:#fff;padding:28px;border:1px solid {_BORDER};border-top:none;border-radius:0 0 8px 8px">
      {content_html}
      <p style="margin:28px 0 0;padding-top:16px;border-top:1px solid {_BORDER};font-size:12px;color:{_MUTED}">{footer_note}</p>
    </div>
  </div>
</div>"""


def heading(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:17px;font-weight:700;color:{_TEXT}">{text}</p>'


def paragraph(text: str) -> str:
    return f'<p style="margin:0 0 16px;font-size:14px;line-height:1.6;color:{_TEXT}">{text}</p>'


def info_card(rows: list[tuple[str, str]]) -> str:
    """En ljus infobox med etikett/värde-par."""
    inner = ""
    for i, (label, value) in enumerate(rows):
        mt = "margin-top:12px" if i else ""
        inner += (
            f'<div style="font-size:12px;color:{_MUTED};{mt}">{label}</div>'
            f'<div style="font-size:14px;font-weight:600;color:{_TEXT};margin-top:2px">{value}</div>'
        )
    return (
        f'<div style="background:{_PANEL};border:1px solid {_BORDER};border-radius:8px;'
        f'padding:16px;margin:0 0 16px">{inner}</div>'
    )


def quote_block(html: str) -> str:
    """För citerat meddelandeinnehåll (t.ex. ett ärendesvar)."""
    return (
        f'<div style="background:{_PANEL};border-left:3px solid {_ACCENT};padding:12px 16px;'
        f'border-radius:0 8px 8px 0;margin:0 0 16px;font-size:14px;line-height:1.6;color:{_TEXT}">{html}</div>'
    )


def button(label: str, url: str) -> str:
    return (
        f'<div style="margin:0 0 16px"><a href="{url}" '
        f'style="display:inline-block;background:{_ACCENT};color:#fff;text-decoration:none;'
        f'font-size:14px;font-weight:600;padding:11px 22px;border-radius:7px">{label}</a></div>'
    )


def portal_url() -> str:
    """Bas-URL till portalen (utan avslutande slash), eller tom sträng om ej satt."""
    return (app_settings.get("portal_url") or "").rstrip("/")


def ticket_button(ticket_id: str) -> str:
    """Knapp som länkar in i portalen till ett ärende, om portal_url är konfigurerad."""
    base = portal_url()
    if not base:
        return ""
    return button("Öppna ärendet i portalen", f"{base}/#ticket/{ticket_id}")
