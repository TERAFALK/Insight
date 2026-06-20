"""
PDF-generering med WeasyPrint. Bygger rapporten sektion för sektion utifrån
vilka integrationer som faktiskt finns i `sections` — ingen sektion renderas
om motsvarande integration saknas eller inte är verifierad för kunden.
"""

import os
import re
from datetime import datetime

from jinja2 import Environment, BaseLoader

from app.core.config import settings

INSIGHT_LOGO_SVG = """<span style="font-family:'Exo 2',Arial,sans-serif;font-size:22px;font-weight:700;color:#0047A3;letter-spacing:-0.03em">Insight</span>"""

BASE_STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Exo+2:wght@300;400;600;700&display=swap');
@page{size:A4;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:100%}
body{font-family:'Exo 2',Arial,sans-serif;color:#141414;font-size:11.5px;line-height:1.5;background:#fff}
.page{width:100%}
.header{background:#fff;border-bottom:3px solid #0047A3;padding:26px 44px;display:flex;align-items:center;justify-content:space-between}
.header-right{text-align:right;color:#999;font-size:10px;text-transform:uppercase;letter-spacing:0.07em}
.header-right .period{font-size:15px;font-weight:700;color:#0047A3;display:block;margin-bottom:3px;letter-spacing:0}
.hero{background:#F4F8FF;padding:26px 44px;border-bottom:1px solid #dde8f5}
.hero-customer{font-size:23px;font-weight:700;color:#141414;margin-bottom:3px}
.hero-sub{font-size:11px;color:#888;margin-bottom:16px}
.hero-chips{display:flex;gap:7px;flex-wrap:wrap}
.hero-chip{font-size:10px;font-weight:700;background:#fff;border:1px solid #BBF7D0;color:#166534;padding:4px 11px;border-radius:12px}
.content{padding:30px 44px}
.section{margin-bottom:30px}
.section-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.12em;color:#0047A3;border-bottom:1px solid #dde8f5;padding-bottom:8px;margin-bottom:16px}
.host-name{font-size:12px;font-weight:700;color:#141414;margin:18px 0 10px}
.subhead{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;color:#aaa;margin:16px 0 8px}
table{width:100%;border-collapse:collapse}
th{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:#aaa;padding:7px 12px;text-align:left;border-bottom:1px solid #e2eaf4}
td{padding:9px 12px;font-size:11.5px;border-bottom:1px solid #f2f6fb;vertical-align:middle}
tr:last-child td{border-bottom:none}
.dev-name{font-weight:600}
.grp-row td{background:#f7faff;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.09em;color:#6b86b0;padding:6px 12px}
.badge-sm{font-size:9.5px;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap}
.b-ok{background:#ECFDF5;color:#15803D}
.b-warn{background:#FFFBEB;color:#92400E}
.b-off{background:#FFF1F2;color:#BE123C}
.pl-tag{font-size:9.5px;font-weight:700;padding:2px 8px;border-radius:5px}
.fw-mono{font-family:'Courier New',monospace;font-size:10.5px;color:#555}
.wan-row{display:flex;align-items:center;gap:14px;padding:11px 0;border-bottom:1px solid #f2f6fb}
.wan-row:last-child{border-bottom:none}
.wan-name{font-weight:700;width:54px;flex-shrink:0;font-size:12px}
.wan-badge{font-size:10px;font-weight:700;padding:2px 9px;border-radius:12px;flex-shrink:0;width:60px;text-align:center}
.wan-ok{background:#ECFDF5;color:#15803D}
.wan-dn{background:#FFF1F2;color:#BE123C}
.upbar-bg{flex:1;height:5px;background:#e8eef7;border-radius:3px;overflow:hidden}
.upbar{height:100%;border-radius:3px}
.upbar-ok{background:#22C55E}
.upbar-dn{background:#EF4444}
.wan-pct{font-size:12px;font-weight:700;width:44px;text-align:right;flex-shrink:0}
.wan-isp{font-size:10px;color:#999;flex-shrink:0;min-width:90px;text-align:right}
.wan-alert{background:#FFF1F2;border:1px solid #FECDD3;border-radius:6px;padding:8px 12px;font-size:11px;color:#BE123C;margin-top:6px}
.metrics-row{display:flex;gap:12px;margin-top:8px}
.mbox{flex:1;background:#F4F8FF;border-radius:8px;padding:14px 16px;text-align:center}
.mbox-val{font-size:22px;font-weight:700;color:#0047A3}
.mbox-lbl{font-size:9px;color:#999;font-weight:700;text-transform:uppercase;letter-spacing:0.07em;margin-top:3px}
.ips-box{background:#F4F8FF;border-left:3px solid #0047A3;padding:12px 16px;border-radius:0 6px 6px 0;margin-top:12px}
.ips-val{font-size:18px;font-weight:700;color:#0047A3}
.ips-lbl{font-size:10px;color:#888;margin-top:2px}
.service-box{background:#F1F9F0;border:1px solid #BBF7D0;border-radius:8px;padding:16px 18px}
.service-title{font-size:11px;font-weight:700;color:#15803D;margin-bottom:9px;text-transform:uppercase;letter-spacing:0.06em}
.service-items{display:flex;flex-wrap:wrap;gap:7px}
.service-item{font-size:10px;background:#fff;border:1px solid #BBF7D0;border-radius:12px;padding:4px 11px;color:#166534}
.footer{background:#141414;padding:18px 44px;display:flex;align-items:center;justify-content:space-between;margin-top:10px}
.footer-left{font-size:10px;color:rgba(255,255,255,0.55)}
.footer-right{font-size:10px;color:rgba(255,255,255,0.35);text-align:right}
"""

# ── UniFi-sektion ─────────────────────────────────────────────────────────────

PL_COLORS = {
    "network": "background:#EEF4FF;color:#0047A3",
    "protect": "background:#F0F9FF;color:#0369a1",
    "access":  "background:#F1F9F0;color:#166534",
    "talk":    "background:#FDF4FF;color:#7e22ce",
    "connect": "background:#FFFBEB;color:#92400E",
}

UNIFI_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-title">Nätverk — UniFi</div>

  {% if isp_avg_latency is not none %}
  <div class="metrics-row" style="margin-bottom:22px">
    <div class="mbox"><div class="mbox-val">{{ isp_avg_latency }}<span style="font-size:13px"> ms</span></div><div class="mbox-lbl">Snittlatens</div></div>
    <div class="mbox"><div class="mbox-val">{{ isp_packet_loss }}<span style="font-size:13px"> %</span></div><div class="mbox-lbl">Paketförlust</div></div>
    <div class="mbox"><div class="mbox-val">{{ isp_uptime }}<span style="font-size:13px"> %</span></div><div class="mbox-lbl">ISP-uptime</div></div>
  </div>
  {% endif %}

  {% for host in hosts %}
  {% if hosts|length > 1 %}
  <div class="host-name">{{ host.host_name }}</div>
  {% endif %}

  {% if host.wans %}
  <div class="subhead">WAN-anslutningar</div>
  {% for wan in host.wans %}
  <div class="wan-row">
    <span class="wan-name">{{ wan.name }}</span>
    {% if (wan.uptime_percentage or 0) >= 99 %}
      <span class="wan-badge wan-ok">Online</span>
    {% else %}
      <span class="wan-badge wan-dn">Nere</span>
    {% endif %}
    <div class="upbar-bg"><div class="upbar {% if (wan.uptime_percentage or 0) >= 99 %}upbar-ok{% else %}upbar-dn{% endif %}" style="width:{{ wan.uptime_percentage or 0 }}%"></div></div>
    <span class="wan-pct">{{ wan.uptime_percentage or 0 }}%</span>
    <span class="wan-isp">{{ wan.isp_name or '' }}</span>
  </div>
  {% if wan.has_issues %}
  <div class="wan-alert">{{ wan.name }} har haft {{ wan.issue_count }} avbrottstillfällen under perioden</div>
  {% endif %}
  {% endfor %}
  {% endif %}

  {% if host.device_groups %}
  <div class="subhead">Enheter</div>
  <table>
    <thead><tr><th>Enhet</th><th>Modell</th><th>Firmware</th><th style="text-align:right">Status</th></tr></thead>
    <tbody>
      {% for grp in host.device_groups %}
      <tr class="grp-row"><td colspan="4">{{ grp.label }} · {{ grp.devices|length }} st</td></tr>
      {% for d in grp.devices %}
      <tr>
        <td><div class="dev-name">{{ d.name }}</div></td>
        <td style="color:#666">{{ d.model or '—' }}</td>
        <td><span class="fw-mono">{{ d.fw_short }}</span> &nbsp;{% if d.needs_update %}<span class="badge-sm b-warn">Uppdatering</span>{% else %}<span class="badge-sm b-ok">Senaste</span>{% endif %}</td>
        <td style="text-align:right">{% if d.is_online %}<span class="badge-sm b-ok">Online</span>{% else %}<span class="badge-sm b-off">Offline</span>{% endif %}</td>
      </tr>
      {% endfor %}
      {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if host.ips_rules_count %}
  <div class="ips-box">
    <div class="ips-val">{{ host.ips_rules_count | int }}</div>
    <div class="ips-lbl">Aktiva IPS-regler · trafiken inspekteras i realtid</div>
  </div>
  {% endif %}
  {% endfor %}
</div>
"""

# ── Sektioner för ej implementerade integrationer (visas ALDRIG med fejkdata —
#    om en integration inte är verifierad finns den helt enkelt inte i `sections`
#    och renderas aldrig. Dessa mallar används bara om/när adaptrarna är klara.) ──

MICROSOFT_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-title">🪟 Microsoft 365</div>
  <div class="metrics-row">
    <div class="mbox"><div class="mbox-val">{{ total_licenses }}</div><div class="mbox-lbl">Licenser</div></div>
    <div class="mbox"><div class="mbox-val">{{ mfa_enabled_count }}/{{ active_users }}</div><div class="mbox-lbl">MFA aktiverat</div></div>
    {% if secure_score is not none %}
    <div class="mbox"><div class="mbox-val">{{ secure_score }}/{{ secure_score_max }}</div><div class="mbox-lbl">Secure Score</div></div>
    {% endif %}
  </div>
</div>
"""

ACRONIS_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-title">🛡 Acronis Backup</div>
  <table>
    <thead><tr><th>Enhet</th><th>Senaste körning</th><th>Status</th><th>Skyddad data</th></tr></thead>
    <tbody>
      {% for job in jobs %}
      <tr>
        <td><div class="dev-name">{{ job.device_name }}</div></td>
        <td>{{ job.last_run or '—' }}</td>
        <td>{% if job.status == 'ok' %}<span class="badge-sm b-ok">OK</span>{% elif job.status == 'warning' %}<span class="badge-sm b-warn">Varning</span>{% else %}<span class="badge-sm" style="background:#FFF1F2;color:#BE123C">Fel</span>{% endif %}</td>
        <td>{{ job.protected_gb or '—' }} GB</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

CLOUDFACTORY_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-title">📦 Cloudfactory</div>
  <table>
    <thead><tr><th>Produkt</th><th>Antal</th><th>Aktiva</th><th>Förnyas</th></tr></thead>
    <tbody>
      {% for lic in licenses %}
      <tr>
        <td><div class="dev-name">{{ lic.product_name }}</div></td>
        <td>{{ lic.quantity }}</td>
        <td>{{ lic.active }}</td>
        <td>{{ lic.expires_at or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="sv">
<head><meta charset="UTF-8"><style>{{ base_style }}</style></head>
<body>
<div class="page">
  <div class="header">
    {{ logo }}
    <div class="header-right"><span class="period">{{ period_label }}</span>Rapport från Insight</div>
  </div>
  <div class="hero">
    <div class="hero-customer">{{ customer_name }}</div>
    <div class="hero-sub">Rapport genererad {{ generated_date }} · Insight</div>
    <div class="hero-chips">
      {% for name in included_integration_names %}
      <span class="hero-chip">{{ name }}</span>
      {% endfor %}
    </div>
  </div>
  <div class="content">
    {{ rendered_sections | safe }}
    <div class="section">
      <div class="service-box">
        <div class="service-title">Managed Services</div>
        <div class="service-items">
          {% for item in service_items %}
          <span class="service-item">{{ item }}</span>
          {% endfor %}
        </div>
      </div>
    </div>
  </div>
  <div class="footer">
    <div class="footer-left">Insight · {{ sender }}</div>
    <div class="footer-right">Automatisk rapport · {{ sender }}<br>{{ generated_date }}</div>
  </div>
</div>
</body>
</html>"""


def _month_label(period: str) -> str:
    names = {
        "01": "Januari", "02": "Februari", "03": "Mars", "04": "April",
        "05": "Maj", "06": "Juni", "07": "Juli", "08": "Augusti",
        "09": "September", "10": "Oktober", "11": "November", "12": "December",
    }
    year, month_num = period.split("-")
    return f"{names.get(month_num, month_num)} {year}"


_PL_LABELS = {
    "network": "Network", "protect": "Protect", "access": "Access",
    "talk": "Talk", "connect": "Connect",
}
_PL_ORDER = ["network", "protect", "access", "talk", "connect"]


def _short_fw(version) -> str:
    """Plockar ut en läsbar version ur långa firmware-strängar, t.ex.
    'UVC.S5L.v5.3.94.67.7398deb.260609.0803' → '5.3.94'."""
    if not version:
        return "—"
    m = re.search(r"(\d+\.\d+\.\d+)", str(version))
    return m.group(1) if m else str(version)


def _group_devices(devices: list) -> list:
    """Grupperar enheter per produktlinje (Network, Protect, Access ...)."""
    by_line: dict = {}
    for d in devices:
        d["fw_short"] = _short_fw(d.get("firmware_version"))
        pl = (d.get("product_line") or "network").lower()
        by_line.setdefault(pl, []).append(d)
    ordered = [l for l in _PL_ORDER if l in by_line] + [
        l for l in by_line if l not in _PL_ORDER
    ]
    return [
        {
            "label": _PL_LABELS.get(pl, pl.capitalize()),
            "color": PL_COLORS.get(pl, PL_COLORS["network"]),
            "devices": by_line[pl],
        }
        for pl in ordered
    ]


def _render_unifi(data: dict, env: Environment) -> tuple[str, list[str]]:
    hosts = data.get("hosts", [])
    for host in hosts:
        host["device_groups"] = _group_devices(host.get("devices", []))
    service_items = ["Firmware-patchning", "24/7 övervakning", "IPS-uppdateringar", "WAN-monitoring", "Fri felsökningstid"]

    html = env.from_string(UNIFI_SECTION_TEMPLATE).render(
        hosts=hosts,
        isp_avg_latency=data.get("isp_avg_latency"),
        isp_packet_loss=data.get("isp_packet_loss"),
        isp_uptime=data.get("isp_uptime"),
    )
    return html, service_items


def _render_microsoft(data: dict, env: Environment) -> tuple[str, list[str]]:
    html = env.from_string(MICROSOFT_SECTION_TEMPLATE).render(**data)
    return html, ["Licenshantering", "MFA-övervakning", "Säkerhetsrapportering"]


def _render_acronis(data: dict, env: Environment) -> tuple[str, list[str]]:
    html = env.from_string(ACRONIS_SECTION_TEMPLATE).render(**data)
    return html, ["Backup-övervakning", "Återställningstest"]


def _render_cloudfactory(data: dict, env: Environment) -> tuple[str, list[str]]:
    html = env.from_string(CLOUDFACTORY_SECTION_TEMPLATE).render(**data)
    return html, ["Licenshantering"]


_RENDERERS = {
    "unifi": ("UniFi", _render_unifi),
    "microsoft": ("Microsoft 365", _render_microsoft),
    "acronis": ("Acronis Backup", _render_acronis),
    "cloudfactory": ("Cloudfactory", _render_cloudfactory),
}


async def generate_preview_html(customer_name: str, period: str, sections: dict) -> str:
    """Returnerar rapport-HTML utan att generera en PDF eller skriva till disk."""
    env = Environment(loader=BaseLoader())
    rendered_html_parts = []
    included_names = []
    service_items: list[str] = []

    order = ["unifi", "microsoft", "acronis", "cloudfactory"]
    keys_in_order = [k for k in order if k in sections] + [k for k in sections if k not in order]

    for key in keys_in_order:
        if key not in _RENDERERS:
            continue
        display_name, renderer = _RENDERERS[key]
        try:
            html, items = renderer(sections[key], env)
            rendered_html_parts.append(html)
            included_names.append(display_name)
            service_items.extend(items)
        except Exception:
            continue

    ctx = {
        "base_style": BASE_STYLE,
        "logo": INSIGHT_LOGO_SVG,
        "customer_name": customer_name,
        "period_label": _month_label(period),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "included_integration_names": included_names,
        "rendered_sections": "\n".join(rendered_html_parts),
        "service_items": sorted(set(service_items), key=service_items.index),
        "sender": settings.GRAPH_SENDER,
    }
    return env.from_string(PAGE_TEMPLATE).render(**ctx)


async def generate_pdf(customer_name: str, period: str, sections: dict, customer_id: str) -> str:
    """
    Genererar en PDF-rapport byggd uteslutande av de sektioner som finns i
    `sections` (en nyckel per verifierad och datahämtad integration).
    Returnerar filsökvägen.
    """
    from weasyprint import HTML as WeasyprintHTML

    env = Environment(loader=BaseLoader())

    rendered_html_parts = []
    included_names = []
    service_items: list[str] = []

    # Stabil ordning: unifi, microsoft, acronis, cloudfactory, sen ev. okänt
    order = ["unifi", "microsoft", "acronis", "cloudfactory"]
    keys_in_order = [k for k in order if k in sections] + [k for k in sections if k not in order]

    for key in keys_in_order:
        if key not in _RENDERERS:
            continue
        display_name, renderer = _RENDERERS[key]
        try:
            html, items = renderer(sections[key], env)
            rendered_html_parts.append(html)
            included_names.append(display_name)
            service_items.extend(items)
        except Exception:
            continue

    ctx = {
        "base_style": BASE_STYLE,
        "logo": INSIGHT_LOGO_SVG,
        "customer_name": customer_name,
        "period_label": _month_label(period),
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "included_integration_names": included_names,
        "rendered_sections": "\n".join(rendered_html_parts),
        "service_items": sorted(set(service_items), key=service_items.index),
    }

    html_content = env.from_string(PAGE_TEMPLATE).render(**ctx)

    os.makedirs(settings.REPORTS_OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(settings.REPORTS_OUTPUT_DIR, f"{customer_id}_{period}.pdf")
    WeasyprintHTML(string=html_content).write_pdf(pdf_path)
    return pdf_path
