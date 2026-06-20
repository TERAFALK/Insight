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
@import url('https://fonts.googleapis.com/css2?family=Exo+2:wght@300;400;500;600;700&display=swap');
@page { size: A4; margin: 0 }
* { box-sizing: border-box; margin: 0; padding: 0 }
html, body { width: 100% }
body {
  font-family: 'Exo 2', Arial, sans-serif;
  color: #1A1C1F;
  font-size: 11px;
  line-height: 1.55;
  background: #fff;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

/* ── Sidlayout ── */
.page { width: 100% }
.content { padding: 28px 44px 20px }

/* ── Header ── */
.header {
  background: #fff;
  border-bottom: 2.5px solid #0047A3;
  padding: 22px 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-right { text-align: right }
.header-period {
  font-size: 15px;
  font-weight: 700;
  color: #0047A3;
  display: block;
  letter-spacing: -0.01em;
  margin-bottom: 2px;
}
.header-label {
  font-size: 9.5px;
  font-weight: 600;
  color: #9499A2;
  text-transform: uppercase;
  letter-spacing: 0.09em;
}

/* ── Hero ── */
.hero {
  background: #F4F8FF;
  padding: 24px 44px 22px;
  border-bottom: 1px solid #DCE8F8;
}
.hero-tag {
  font-size: 9.5px;
  font-weight: 700;
  color: #0047A3;
  text-transform: uppercase;
  letter-spacing: 0.11em;
  margin-bottom: 6px;
}
.hero-customer {
  font-size: 24px;
  font-weight: 700;
  color: #141414;
  letter-spacing: -0.02em;
  margin-bottom: 5px;
  line-height: 1.2;
}
.hero-meta {
  font-size: 10.5px;
  color: #9499A2;
  margin-bottom: 16px;
}
.hero-chips { display: flex; gap: 6px; flex-wrap: wrap }
.hero-chip {
  font-size: 10px;
  font-weight: 600;
  background: #fff;
  border: 1px solid #B8DBBF;
  color: #15803D;
  padding: 3px 10px;
  border-radius: 20px;
  letter-spacing: 0.01em;
}

/* ── Sektioner ── */
.section {
  margin-bottom: 28px;
  page-break-inside: avoid;
  break-inside: avoid;
}
.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding-bottom: 8px;
  border-bottom: 1.5px solid #E9EBEF;
}
.section-icon {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: #EEF4FF;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.section-icon svg { display: block }
.section-title {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.11em;
  color: #0047A3;
  flex: 1;
}
.section-badge {
  font-size: 9.5px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 10px;
  background: #EEF4FF;
  color: #0047A3;
}

.host-name {
  font-size: 11.5px;
  font-weight: 700;
  color: #141414;
  margin: 16px 0 9px;
  padding-left: 2px;
  border-left: 3px solid #0047A3;
  padding-left: 8px;
}
.subhead {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #9499A2;
  margin: 14px 0 7px;
}

/* ── Tabeller ── */
table { width: 100%; border-collapse: collapse; margin-bottom: 4px }
thead tr { page-break-after: avoid; break-after: avoid }
th {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #9499A2;
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1.5px solid #E9EBEF;
  background: #fff;
}
td {
  padding: 8px 10px;
  font-size: 11px;
  border-bottom: 1px solid #F3F5F8;
  vertical-align: middle;
}
tr:last-child td { border-bottom: none }
tr { page-break-inside: avoid; break-inside: avoid }
.dev-name { font-weight: 600; color: #141414 }
.grp-row { page-break-after: avoid; break-after: avoid }
.grp-row td {
  background: #F6F7F9;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: #5C616B;
  padding: 5px 10px;
  border-bottom: 1px solid #E9EBEF;
}

/* ── Badges ── */
.badge {
  font-size: 9.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 10px;
  white-space: nowrap;
  display: inline-block;
}
.b-ok  { background: #EAF7EF; color: #15803D }
.b-warn { background: #FEF6E7; color: #92400E }
.b-off  { background: #FDECEC; color: #BE123C }
.b-info { background: #EEF4FF; color: #0047A3 }

/* ── Firmware-mono ── */
.fw-mono { font-family: 'Courier New', monospace; font-size: 10px; color: #5C616B }

/* ── WAN-rader ── */
.wan-block { margin-bottom: 2px }
.wan-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid #F3F5F8;
  page-break-inside: avoid;
  break-inside: avoid;
}
.wan-row:last-child { border-bottom: none }
.wan-name { font-weight: 700; width: 80px; flex-shrink: 0; font-size: 11.5px }
.wan-badge {
  font-size: 9.5px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 12px;
  flex-shrink: 0;
  width: 58px;
  text-align: center;
}
.wan-ok { background: #EAF7EF; color: #15803D }
.wan-dn { background: #FDECEC; color: #BE123C }
.upbar-wrap { flex: 1; display: flex; align-items: center; gap: 8px }
.upbar-bg { flex: 1; height: 5px; background: #E9EBEF; border-radius: 3px; overflow: hidden }
.upbar { height: 100%; border-radius: 3px }
.upbar-ok { background: #22C55E }
.upbar-dn { background: #EF4444 }
.wan-pct { font-size: 11.5px; font-weight: 700; color: #141414; width: 40px; text-align: right; flex-shrink: 0 }
.wan-isp { font-size: 10px; color: #9499A2; flex-shrink: 0; min-width: 100px; text-align: right }
.wan-alert {
  background: #FDECEC;
  border-left: 3px solid #EF4444;
  border-radius: 0 6px 6px 0;
  padding: 7px 12px;
  font-size: 10.5px;
  color: #BE123C;
  margin: 4px 0 6px;
  page-break-inside: avoid;
  break-inside: avoid;
}

/* ── Metrics-boxar ── */
.metrics-row { display: flex; gap: 10px; margin-bottom: 20px }
.mbox {
  flex: 1;
  background: #F4F8FF;
  border: 1px solid #DCE8F8;
  border-radius: 10px;
  padding: 14px 16px;
  text-align: center;
  page-break-inside: avoid;
  break-inside: avoid;
}
.mbox-val { font-size: 24px; font-weight: 700; color: #0047A3; line-height: 1; letter-spacing: -0.02em }
.mbox-unit { font-size: 13px; font-weight: 400 }
.mbox-lbl { font-size: 9px; color: #9499A2; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 5px }

/* ── IPS-box ── */
.ips-box {
  background: #F4F8FF;
  border: 1px solid #DCE8F8;
  border-left: 3px solid #0047A3;
  border-radius: 0 8px 8px 0;
  padding: 13px 18px;
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 16px;
  page-break-inside: avoid;
  break-inside: avoid;
}
.ips-val { font-size: 22px; font-weight: 700; color: #0047A3; flex-shrink: 0 }
.ips-lbl { font-size: 10.5px; color: #5C616B }

/* ── Service-ruta ── */
.service-box {
  background: #F1F9F0;
  border: 1px solid #B8DBBF;
  border-radius: 10px;
  padding: 16px 20px;
  page-break-inside: avoid;
  break-inside: avoid;
}
.service-title {
  font-size: 9.5px;
  font-weight: 700;
  color: #15803D;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 10px;
}
.service-items { display: flex; flex-wrap: wrap; gap: 6px }
.service-item {
  font-size: 10px;
  font-weight: 500;
  background: #fff;
  border: 1px solid #B8DBBF;
  border-radius: 20px;
  padding: 3px 11px;
  color: #166534;
}

/* ── Footer ── */
.footer {
  background: #141414;
  padding: 16px 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 20px;
}
.footer-left { font-size: 9.5px; color: rgba(255,255,255,0.5); line-height: 1.6 }
.footer-right { font-size: 9.5px; color: rgba(255,255,255,0.3); text-align: right; line-height: 1.6 }
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
  <div class="section-header">
    <div class="section-icon">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0047A3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>
        <line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
      </svg>
    </div>
    <span class="section-title">Nätverk — UniFi</span>
  </div>

  {% if isp_avg_latency is not none %}
  <div class="metrics-row">
    <div class="mbox">
      <div class="mbox-val">{{ isp_avg_latency }}<span class="mbox-unit"> ms</span></div>
      <div class="mbox-lbl">Snittlatens</div>
    </div>
    <div class="mbox">
      <div class="mbox-val">{{ isp_packet_loss }}<span class="mbox-unit"> %</span></div>
      <div class="mbox-lbl">Paketförlust</div>
    </div>
    <div class="mbox">
      <div class="mbox-val">{{ isp_uptime }}<span class="mbox-unit"> %</span></div>
      <div class="mbox-lbl">ISP-uptime</div>
    </div>
  </div>
  {% endif %}

  {% for host in hosts %}
  {% if hosts|length > 1 %}
  <div class="host-name">{{ host.host_name }}</div>
  {% endif %}

  {% if host.wans %}
  <div class="subhead">WAN-anslutningar</div>
  <div class="wan-block">
  {% for wan in host.wans %}
  <div class="wan-row">
    <span class="wan-name">{{ wan.name }}</span>
    {% if (wan.uptime_percentage or 0) >= 99 %}
      <span class="wan-badge wan-ok">Online</span>
    {% else %}
      <span class="wan-badge wan-dn">Nere</span>
    {% endif %}
    <div class="upbar-wrap">
      <div class="upbar-bg">
        <div class="upbar {% if (wan.uptime_percentage or 0) >= 99 %}upbar-ok{% else %}upbar-dn{% endif %}" style="width:{{ wan.uptime_percentage or 0 }}%"></div>
      </div>
      <span class="wan-pct">{{ wan.uptime_percentage or 0 }}%</span>
    </div>
    <span class="wan-isp">{{ wan.isp_name or '' }}</span>
  </div>
  {% if wan.has_issues %}
  <div class="wan-alert">{{ wan.name }} har haft {{ wan.issue_count }} avbrottstillfällen under perioden</div>
  {% endif %}
  {% endfor %}
  </div>
  {% endif %}

  {% if host.device_groups %}
  <div class="subhead">Enheter</div>
  <table>
    <thead>
      <tr>
        <th>Enhet</th>
        <th>Modell</th>
        <th>Firmware</th>
        <th style="text-align:right">Status</th>
      </tr>
    </thead>
    <tbody>
      {% for grp in host.device_groups %}
      <tr class="grp-row">
        <td colspan="4">{{ grp.label }} &mdash; {{ grp.devices|length }} enheter</td>
      </tr>
      {% for d in grp.devices %}
      <tr>
        <td><div class="dev-name">{{ d.name }}</div></td>
        <td style="color:#5C616B">{{ d.model or '—' }}</td>
        <td>
          <span class="fw-mono">{{ d.fw_short }}</span>&nbsp;&nbsp;{% if d.needs_update %}<span class="badge b-warn">Uppdatering tillgänglig</span>{% else %}<span class="badge b-ok">Senaste</span>{% endif %}
        </td>
        <td style="text-align:right">
          {% if d.is_online %}<span class="badge b-ok">Online</span>{% else %}<span class="badge b-off">Offline</span>{% endif %}
        </td>
      </tr>
      {% endfor %}
      {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if host.ips_rules_count %}
  <div class="ips-box">
    <div class="ips-val">{{ host.ips_rules_count | int }}</div>
    <div class="ips-lbl">Aktiva IPS-regler<br><span style="font-size:9.5px;color:#9499A2">Nätverkstrafiken inspekteras i realtid mot kända hot</span></div>
  </div>
  {% endif %}
  {% endfor %}
</div>
"""

MICROSOFT_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-header">
    <div class="section-icon">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0047A3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
        <rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>
      </svg>
    </div>
    <span class="section-title">Microsoft 365</span>
  </div>
  <div class="metrics-row">
    <div class="mbox">
      <div class="mbox-val">{{ total_licenses }}</div>
      <div class="mbox-lbl">Licenser totalt</div>
    </div>
    <div class="mbox">
      <div class="mbox-val">{{ mfa_enabled_count }}<span class="mbox-unit">/{{ active_users }}</span></div>
      <div class="mbox-lbl">MFA aktiverat</div>
    </div>
    {% if secure_score is not none %}
    <div class="mbox">
      <div class="mbox-val">{{ secure_score }}<span class="mbox-unit">/{{ secure_score_max }}</span></div>
      <div class="mbox-lbl">Secure Score</div>
    </div>
    {% endif %}
  </div>
</div>
"""

ACRONIS_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-header">
    <div class="section-icon">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0047A3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      </svg>
    </div>
    <span class="section-title">Acronis Backup</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Enhet</th>
        <th>Senaste körning</th>
        <th>Status</th>
        <th style="text-align:right">Skyddad data</th>
      </tr>
    </thead>
    <tbody>
      {% for job in jobs %}
      <tr>
        <td><div class="dev-name">{{ job.device_name }}</div></td>
        <td style="color:#5C616B">{{ job.last_run or '—' }}</td>
        <td>
          {% if job.status == 'ok' %}<span class="badge b-ok">OK</span>
          {% elif job.status == 'warning' %}<span class="badge b-warn">Varning</span>
          {% else %}<span class="badge b-off">Fel</span>{% endif %}
        </td>
        <td style="text-align:right;color:#5C616B">{{ job.protected_gb or '—' }} GB</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

CLOUDFACTORY_SECTION_TEMPLATE = """
<div class="section">
  <div class="section-header">
    <div class="section-icon">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#0047A3" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
      </svg>
    </div>
    <span class="section-title">Cloudfactory</span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Produkt</th>
        <th>Antal</th>
        <th>Aktiva</th>
        <th style="text-align:right">Förnyas</th>
      </tr>
    </thead>
    <tbody>
      {% for lic in licenses %}
      <tr>
        <td><div class="dev-name">{{ lic.product_name }}</div></td>
        <td>{{ lic.quantity }}</td>
        <td>{{ lic.active }}</td>
        <td style="text-align:right;color:#5C616B">{{ lic.expires_at or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <style>{{ base_style }}</style>
</head>
<body>
<div class="page">

  <div class="header">
    {{ logo }}
    <div class="header-right">
      <span class="header-period">{{ period_label }}</span>
      <span class="header-label">Månadsrapport</span>
    </div>
  </div>

  <div class="hero">
    <div class="hero-tag">Kundrapport</div>
    <div class="hero-customer">{{ customer_name }}</div>
    <div class="hero-meta">Genererad {{ generated_date }}</div>
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
        <div class="service-title">Ingående tjänster</div>
        <div class="service-items">
          {% for item in service_items %}
          <span class="service-item">{{ item }}</span>
          {% endfor %}
        </div>
      </div>
    </div>
  </div>

  <div class="footer">
    <div class="footer-left">
      Insight &mdash; Managed Network Portal<br>
      Kontakt: support@terafalk.com
    </div>
    <div class="footer-right">
      Automatisk rapport &middot; {{ sender }}<br>
      {{ generated_date }}
    </div>
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
    if not version:
        return "—"
    m = re.search(r"(\d+\.\d+\.\d+)", str(version))
    return m.group(1) if m else str(version)


def _group_devices(devices: list) -> list:
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


def _build_ctx(customer_name: str, period: str, rendered_html_parts: list, included_names: list, service_items: list) -> dict:
    return {
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


def _render_sections(sections: dict, env: Environment) -> tuple[list, list, list]:
    rendered_html_parts: list[str] = []
    included_names: list[str] = []
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
    return rendered_html_parts, included_names, service_items


async def generate_preview_html(customer_name: str, period: str, sections: dict) -> str:
    """Returnerar rapport-HTML utan att generera en PDF eller skriva till disk."""
    env = Environment(loader=BaseLoader())
    parts, names, items = _render_sections(sections, env)
    ctx = _build_ctx(customer_name, period, parts, names, items)
    return env.from_string(PAGE_TEMPLATE).render(**ctx)


async def generate_pdf(customer_name: str, period: str, sections: dict, customer_id: str) -> str:
    """
    Genererar en PDF-rapport byggd uteslutande av de sektioner som finns i
    `sections`. Returnerar filsökvägen.
    """
    from weasyprint import HTML as WeasyprintHTML

    env = Environment(loader=BaseLoader())
    parts, names, items = _render_sections(sections, env)
    ctx = _build_ctx(customer_name, period, parts, names, items)
    html_content = env.from_string(PAGE_TEMPLATE).render(**ctx)

    os.makedirs(settings.REPORTS_OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(settings.REPORTS_OUTPUT_DIR, f"{customer_id}_{period}.pdf")
    WeasyprintHTML(string=html_content).write_pdf(pdf_path)
    return pdf_path
