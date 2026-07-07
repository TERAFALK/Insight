"""Domänövervakning utan externa beroenden.

Alla nätverksanrop går via httpx (redan ett projektberoende) eller Python-stdlib:
  - DNS TXT (DMARC/SPF) via DNS-over-HTTPS (dns.google)
  - Förnyelsedatum + registrar via RDAP (rdap.org)
  - SSL-certifikatets utgång via ssl/socket
  - Webbplatsstatus via httpx

Varje delkontroll är isolerad — ett fel i en påverkar inte de andra.
"""

import asyncio
import logging
import socket
import ssl
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

_DOH_URL = "https://dns.google/resolve"
_RDAP_URL = "https://rdap.org/domain/"


async def _dns_txt(client: httpx.AsyncClient, name: str) -> list[str]:
    """Hämtar TXT-poster för ett namn via DNS-over-HTTPS."""
    r = await client.get(_DOH_URL, params={"name": name, "type": "TXT"}, timeout=8)
    r.raise_for_status()
    data = r.json()
    out = []
    for ans in data.get("Answer", []):
        if ans.get("type") == 16:  # TXT
            # DoH returnerar strängen med citattecken; sammanfoga ev. chunkar
            txt = ans.get("data", "").strip()
            txt = txt.replace('" "', "").strip('"')
            out.append(txt)
    return out


def _eval_dmarc(txts: list[str]) -> tuple[str, str]:
    for t in txts:
        if t.lower().startswith("v=dmarc1"):
            policy = ""
            for part in t.split(";"):
                part = part.strip()
                if part.lower().startswith("p="):
                    policy = part.split("=", 1)[1].strip().lower()
            if policy in ("quarantine", "reject"):
                return "ok", policy
            return "weak", policy or "none"
    return "missing", ""


def _eval_spf(txts: list[str]) -> str:
    for t in txts:
        if t.lower().startswith("v=spf1"):
            return "ok"
    return "missing"


def _parse_rdap_date(s: str) -> date | None:
    if not s:
        return None
    try:
        # ISO 8601, ev. med tidszon
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


async def _rdap(client: httpx.AsyncClient, domain: str) -> tuple[date | None, str]:
    """Returnerar (förnyelsedatum, registrar) via RDAP om tillgängligt."""
    try:
        r = await client.get(_RDAP_URL + domain, timeout=10, follow_redirects=True)
        if r.status_code != 200:
            return None, ""
        data = r.json()
    except Exception:
        return None, ""
    expiry = None
    for ev in data.get("events", []):
        if ev.get("eventAction") == "expiration":
            expiry = _parse_rdap_date(ev.get("eventDate", ""))
    registrar = ""
    for ent in data.get("entities", []):
        if "registrar" in (ent.get("roles") or []):
            vcard = ent.get("vcardArray")
            if isinstance(vcard, list) and len(vcard) > 1:
                for item in vcard[1]:
                    if isinstance(item, list) and item and item[0] == "fn":
                        registrar = item[3] if len(item) > 3 else ""
    return expiry, registrar


def _ssl_expiry_blocking(host: str) -> date | None:
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=8) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            cert = ssock.getpeercert()
    not_after = cert.get("notAfter")
    if not not_after:
        return None
    # Format: 'Jun  1 12:00:00 2027 GMT'
    return datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").date()


def _host_from(domain: str, website_url: str) -> str:
    if website_url:
        h = website_url.split("://", 1)[-1].split("/", 1)[0]
        return h or domain
    return domain


async def check_domain(name: str, monitor_type: str = "domain", website_url: str = "") -> dict:
    """Kör alla kontroller för en domän och returnerar ett resultat-dict."""
    result: dict = {
        "expiry_date": None, "registrar": "",
        "dmarc_status": "", "dmarc_policy": "", "spf_status": "",
        "ssl_expiry": None, "site_status": None, "check_error": "",
    }
    errors = []
    try:
        await _run_checks(name, monitor_type, website_url, result, errors)
    except Exception as e:  # yttersta skyddsnät — får aldrig fälla anropet
        errors.append(f"Oväntat fel: {e}")

    result["check_error"] = "; ".join(errors)[:480]
    return result


async def _run_checks(name: str, monitor_type: str, website_url: str, result: dict, errors: list) -> None:
    async with httpx.AsyncClient() as client:
        # DMARC
        try:
            result["dmarc_status"], result["dmarc_policy"] = _eval_dmarc(
                await _dns_txt(client, "_dmarc." + name)
            )
        except Exception as e:
            result["dmarc_status"] = "error"
            errors.append(f"DMARC: {e}")
        # SPF
        try:
            result["spf_status"] = _eval_spf(await _dns_txt(client, name))
        except Exception as e:
            result["spf_status"] = "error"
            errors.append(f"SPF: {e}")
        # Förnyelse + registrar (RDAP)
        try:
            result["expiry_date"], result["registrar"] = await _rdap(client, name)
        except Exception as e:
            errors.append(f"RDAP: {e}")

        if monitor_type == "site":
            host = _host_from(name, website_url)
            url = website_url or ("https://" + name)
            # HTTP-status
            try:
                r = await client.get(url, timeout=10, follow_redirects=True)
                result["site_status"] = r.status_code
            except Exception as e:
                errors.append(f"HTTP: {e}")
            # SSL-utgång
            try:
                result["ssl_expiry"] = await asyncio.to_thread(_ssl_expiry_blocking, host)
            except Exception as e:
                errors.append(f"SSL: {e}")
