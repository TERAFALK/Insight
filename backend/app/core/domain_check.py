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
import re
import socket
import ssl
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

_DOH_URL = "https://dns.google/resolve"
_RDAP_URL = "https://rdap.org/domain/"

# Vanliga DKIM-selektorer som testas när ingen är angiven (körs parallellt, gräns nedan).
_DKIM_SELECTORS = [
    # Microsoft 365 / Outlook
    "selector1", "selector2",
    # Google Workspace
    "google",
    # Generiska / webbhotell / cPanel
    "default", "dkim", "mail", "email", "smtp", "mx",
    "k1", "k2", "k3", "s1", "s2", "key1", "key2", "s1024", "s2048",
    # Amazon SES
    "amazonses",
    # SendGrid
    "sendgrid", "smtpapi",
    # Mailchimp / Mandrill
    "mandrill", "mte1", "mte2",
    # Mailgun
    "mailo", "mg", "pic", "krs",
    # Zoho
    "zoho", "zmail",
    # Proton Mail
    "protonmail", "protonmail2", "protonmail3",
    # Postmark / Fastmail
    "pm", "fm1", "fm2", "fm3", "mesmtp",
    # Mailjet / SMTP2GO / Brevo
    "mailjet", "s2go", "em",
    # Yahoo / AOL
    "y1", "y2", "yp1", "ecdsa1",
    # Salesforce/Pardot / Zendesk / Freshdesk / Klaviyo / HubSpot
    "pardot", "sf", "zendesk1", "zendesk2", "fd", "kl", "kl2", "hs1", "hs2",
    # Svenska / EU-webbhotell (Loopia, One.com, OVH, Binero)
    "loopia", "one", "ovhmx", "ovh", "binero",
]

# Max antal parallella DKIM-DNS-uppslag (undviker att spamma DoH-tjänsten).
_DKIM_CONCURRENCY = 10

# WHOIS-servrar för TLD:er som saknar RDAP (annars slås de upp via IANA).
_WHOIS_FALLBACK = {
    "se": "whois.iis.se", "nu": "whois.iis.nu",
    "dk": "whois.dk-hostmaster.dk", "no": "whois.norid.no",
    "fi": "whois.fi", "is": "whois.isnic.is",
}


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


def _whois_query(server: str, query: str) -> str:
    with socket.create_connection((server, 43), timeout=10) as s:
        s.sendall((query + "\r\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.decode("utf-8", "replace")


def _parse_whois_date(text: str) -> date | None:
    # Leta efter en rad med expiry/paid-till/renewal och plocka ut ett datum
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("expir", "paid-till", "renewal date", "renewal:")):
            m = re.search(r"(\d{4})[-.](\d{2})[-.](\d{2})", line)
            if m:
                try:
                    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass
            m = re.search(r"(\d{2})[-/](\w{3})[-/](\d{4})", line)  # 09-Mar-2031
            if m:
                try:
                    return datetime.strptime(m.group(0).replace("/", "-"), "%d-%b-%Y").date()
                except ValueError:
                    pass
    return None


def _parse_whois_registrar(text: str) -> str:
    for line in text.splitlines():
        m = re.match(r"\s*registrar:\s*(.+)", line, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _whois_lookup_blocking(domain: str) -> tuple[date | None, str]:
    """WHOIS-fallback (port 43) för TLD:er utan RDAP. Returnerar (förnyelse, registrar)."""
    tld = domain.rsplit(".", 1)[-1].lower()
    server = _WHOIS_FALLBACK.get(tld)
    if not server:
        try:
            iana = _whois_query("whois.iana.org", tld)
            m = re.search(r"whois:\s*(\S+)", iana)
            if m:
                server = m.group(1)
        except Exception:
            server = None
    if not server:
        return None, ""
    try:
        resp = _whois_query(server, domain)
    except Exception:
        return None, ""
    return _parse_whois_date(resp), _parse_whois_registrar(resp)


async def _check_dkim(client: httpx.AsyncClient, domain: str, selector: str) -> tuple[str, str]:
    """Returnerar (status, hittad_selektor). Testar angiven selektor, annars vanliga."""
    selectors = [selector] if selector else _DKIM_SELECTORS
    sem = asyncio.Semaphore(_DKIM_CONCURRENCY)

    async def _probe(sel: str) -> str | None:
        async with sem:
            try:
                txts = await _dns_txt(client, f"{sel}._domainkey.{domain}")
            except Exception:
                return None
        for t in txts:
            low = t.lower()
            if "v=dkim1" in low or "k=rsa" in low or "p=" in low:
                return sel
        return None

    results = await asyncio.gather(*[_probe(s) for s in selectors], return_exceptions=True)
    # Behåll ordningen (mest sannolika selektorer först)
    for r in results:
        if isinstance(r, str) and r:
            return "ok", r
    return "missing", ""


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


async def check_domain(name: str, monitor_type: str = "domain", website_url: str = "", dkim_selector: str = "") -> dict:
    """Kör alla kontroller för en domän och returnerar ett resultat-dict."""
    result: dict = {
        "expiry_date": None, "registrar": "",
        "dmarc_status": "", "dmarc_policy": "", "spf_status": "",
        "dkim_status": "", "dkim_selector": dkim_selector,
        "ssl_expiry": None, "site_status": None, "check_error": "",
    }
    errors = []
    try:
        await _run_checks(name, monitor_type, website_url, dkim_selector, result, errors)
    except Exception as e:  # yttersta skyddsnät — får aldrig fälla anropet
        errors.append(f"Oväntat fel: {e}")

    result["check_error"] = "; ".join(errors)[:480]
    return result


async def _run_checks(name: str, monitor_type: str, website_url: str, dkim_selector: str,
                      result: dict, errors: list) -> None:
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
        # DKIM
        try:
            result["dkim_status"], found_sel = await _check_dkim(client, name, dkim_selector)
            if found_sel:
                result["dkim_selector"] = found_sel
        except Exception as e:
            result["dkim_status"] = "error"
            errors.append(f"DKIM: {e}")
        # Förnyelse + registrar (RDAP → WHOIS-fallback)
        try:
            result["expiry_date"], result["registrar"] = await _rdap(client, name)
        except Exception as e:
            errors.append(f"RDAP: {e}")
        if not result["expiry_date"] or not result["registrar"]:
            try:
                w_exp, w_reg = await asyncio.to_thread(_whois_lookup_blocking, name)
                if not result["expiry_date"] and w_exp:
                    result["expiry_date"] = w_exp
                if not result["registrar"] and w_reg:
                    result["registrar"] = w_reg
            except Exception as e:
                errors.append(f"WHOIS: {e}")

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
