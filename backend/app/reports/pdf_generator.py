"""
PDF-generering med WeasyPrint. Bygger rapporten sektion för sektion utifrån
vilka integrationer som faktiskt finns i `sections`.
"""

import os
import re

from app.core.time_utils import now_stockholm

from jinja2 import Environment, BaseLoader

from app.core import app_settings
from app.core.config import settings

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

/* ── Header ── */
.header {
  padding: 24px 48px 22px;
  border-bottom: 2px solid #E4E8EE;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.logo-wrap { display: flex; flex-direction: column; gap: 4px }
.logo-sub {
  font-size: 9px;
  font-weight: 600;
  color: #9499A2;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.header-right { text-align: right }
.header-period {
  font-size: 18px;
  font-weight: 700;
  color: #1A1C1F;
  letter-spacing: -0.02em;
  display: block;
}
.header-label {
  font-size: 9px;
  font-weight: 600;
  color: #9499A2;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-top: 2px;
  display: block;
}

/* ── Hero ── */
.hero {
  padding: 22px 48px 20px;
  background: #F6F8FB;
  border-bottom: 1px solid #E4E8EE;
}
.hero-customer {
  font-size: 22px;
  font-weight: 700;
  color: #141414;
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.hero-meta {
  font-size: 10px;
  color: #9499A2;
  margin-top: 4px;
}

/* ── Body ── */
.body { padding: 30px 48px 24px }

/* ── Sektionsrubrik ── */
.section-title {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #fff;
  background: #0047A3;
  padding: 10px 16px;
  border-radius: 4px;
  margin-bottom: 18px;
  margin-top: 0;
  break-before: auto;
  break-after: avoid;
}
.section-break {
  break-before: page;
  height: 30px;
}

/* ── Mellanrubrik ── */
.host-label {
  font-size: 11.5px;
  font-weight: 700;
  color: #1A1C1F;
  margin: 18px 0 10px;
}
.sub-label {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #9499A2;
  margin: 12px 0 6px;
}

/* ── Metrics ── */
.metrics { display: flex; gap: 8px; margin-bottom: 18px }
.metric {
  flex: 1;
  border: 1px solid #E4E8EE;
  border-radius: 8px;
  padding: 12px 14px;
  text-align: center;
}
.metric-val {
  font-size: 22px;
  font-weight: 700;
  color: #0047A3;
  letter-spacing: -0.02em;
  line-height: 1;
}
.metric-unit { font-size: 12px; font-weight: 400; color: #5C616B }
.metric-lbl {
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #9499A2;
  margin-top: 5px;
}

/* ── WAN ── */
.wan-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid #F0F2F5;
  page-break-inside: avoid;
  break-inside: avoid;
}
.wan-row:last-child { border-bottom: none }
.wan-name { font-weight: 600; font-size: 11.5px; width: 90px; flex-shrink: 0 }
.wan-status {
  font-size: 9.5px;
  font-weight: 700;
  padding: 2px 9px;
  border-radius: 10px;
  flex-shrink: 0;
  width: 54px;
  text-align: center;
}
.s-ok  { background: #EAF7EF; color: #15803D }
.s-warn { background: #FEF6E7; color: #92400E }
.s-err  { background: #FDECEC; color: #BE123C }
.upbar-bg { flex: 1; height: 4px; background: #E4E8EE; border-radius: 2px; overflow: hidden }
.upbar { height: 100%; border-radius: 2px }
.upbar-ok { background: #22C55E }
.upbar-dn { background: #EF4444 }
.wan-pct { font-size: 11px; font-weight: 700; width: 38px; text-align: right; flex-shrink: 0 }
.wan-isp { font-size: 10px; color: #9499A2; flex-shrink: 0; min-width: 80px; text-align: right }

.wan-alert {
  font-size: 10px;
  color: #92400E;
  background: #FEF6E7;
  border-left: 2px solid #F59E0B;
  padding: 5px 10px;
  margin: 2px 0 4px;
  border-radius: 0 4px 4px 0;
  page-break-inside: avoid;
  break-inside: avoid;
}

/* ── Tabell ── */
table { width: 100%; border-collapse: collapse; margin-bottom: 2px }
thead { display: table-header-group }
thead tr { break-after: avoid }
th {
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #9499A2;
  padding: 5px 10px;
  text-align: left;
  border-bottom: 1.5px solid #E4E8EE;
}
td {
  padding: 7px 10px;
  font-size: 11px;
  border-bottom: 1px solid #F3F5F8;
  vertical-align: middle;
}
tr:last-child td { border-bottom: none }
tr { break-inside: avoid }
.grp-row { break-after: avoid }
.grp-row td {
  background: #F6F8FB;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: #5C616B;
  padding: 4px 10px;
  border-top: 1px solid #E4E8EE;
  border-bottom: 1px solid #E4E8EE;
}
.dev-name { font-weight: 600 }
.fw-mono { font-family: 'Courier New', monospace; font-size: 10px; color: #5C616B }
.badge {
  font-size: 9px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 9px;
  white-space: nowrap;
  display: inline-block;
}

"""

UNIFI_SECTION_TEMPLATE = """
<div class="section-title">Nätverk &mdash; UniFi</div>

{% if isp_avg_latency is not none %}
<div class="metrics">
  <div class="metric">
    <div class="metric-val">{{ isp_avg_latency }}<span class="metric-unit"> ms</span></div>
    <div class="metric-lbl">Snittlatens</div>
  </div>
  <div class="metric">
    <div class="metric-val">{{ isp_packet_loss }}<span class="metric-unit"> %</span></div>
    <div class="metric-lbl">Paketförlust</div>
  </div>
  <div class="metric">
    <div class="metric-val">{{ isp_uptime }}<span class="metric-unit"> %</span></div>
    <div class="metric-lbl">ISP-uptime</div>
  </div>
</div>
{% endif %}

{% for host in hosts %}

{% if hosts|length > 1 %}
<div class="host-label">{{ host.host_name }}</div>
{% endif %}

{% if host.wans %}
<div class="sub-label">WAN-anslutningar</div>
{% for wan in host.wans %}
<div class="wan-row">
  <span class="wan-name">{{ wan.name }}</span>
  {% if (wan.uptime_percentage or 0) >= 99 %}
    <span class="wan-status s-ok">Online</span>
  {% else %}
    <span class="wan-status s-err">Nere</span>
  {% endif %}
  <div class="upbar-bg">
    <div class="upbar {% if (wan.uptime_percentage or 0) >= 99 %}upbar-ok{% else %}upbar-dn{% endif %}" style="width:{{ wan.uptime_percentage or 0 }}%"></div>
  </div>
  <span class="wan-pct">{{ wan.uptime_percentage or 0 }}%</span>
  <span class="wan-isp">{{ wan.isp_name or '' }}</span>
</div>
{% if wan.has_issues %}
<div class="wan-alert">{{ wan.name }} har haft {{ wan.issue_count }} avbrottstillfällen under perioden</div>
{% endif %}
{% endfor %}
{% endif %}

{% if host.device_groups %}
<div class="sub-label" style="margin-top:14px">Enheter</div>
<table>
  <thead>
    <tr>
      <th>Enhet</th>
      <th>Modell</th>
      <th>Firmware</th>
      <th style="text-align:right">Status</th>
    </tr>
  </thead>
  {% for grp in host.device_groups %}
  <tbody style="page-break-inside:avoid;break-inside:avoid">
    <tr class="grp-row">
      <td colspan="4">{{ grp.label }} &mdash; {{ grp.devices|length }} st</td>
    </tr>
    {% for d in grp.devices %}
    <tr>
      <td><span class="dev-name">{{ d.name }}</span></td>
      <td style="color:#5C616B">{{ d.model or '&mdash;' }}</td>
      <td>
        <span class="fw-mono">{{ d.fw_short }}</span>&nbsp;
        {% if d.needs_update %}
          <span class="badge s-warn">Uppdatering</span>
        {% else %}
          <span class="badge s-ok">Senaste</span>
        {% endif %}
      </td>
      <td style="text-align:right">
        {% if d.is_online %}
          <span class="badge s-ok">Online</span>
        {% else %}
          <span class="badge s-err">Offline</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
  {% endfor %}
</table>
{% endif %}

{% endfor %}
"""

MICROSOFT_SECTION_TEMPLATE = """
<div class="section-break"></div>
<div class="section-title">Microsoft 365</div>
<div class="metrics">
  <div class="metric">
    <div class="metric-val">{{ total_users }}</div>
    <div class="metric-lbl">Användare totalt</div>
  </div>
  <div class="metric">
    <div class="metric-val">{{ enabled_users }}</div>
    <div class="metric-lbl">Aktiva användare</div>
  </div>
  {% if mfa_total > 0 %}
  <div class="metric">
    <div class="metric-val">{{ mfa_pct }}<span class="metric-unit"> %</span></div>
    <div class="metric-lbl">MFA-täckning</div>
  </div>
  {% endif %}
  {% if secure_score is not none %}
  <div class="metric">
    <div class="metric-val">{{ secure_score }}<span class="metric-unit">/{{ secure_score_max }}</span></div>
    <div class="metric-lbl">Secure Score</div>
  </div>
  {% endif %}
</div>

{% if licenses %}
<div class="sub-label">Licenser</div>
<table>
  <thead>
    <tr>
      <th>Licens</th>
      <th style="text-align:right">Tilldelade</th>
      <th style="text-align:right">Totalt</th>
      <th style="text-align:right">Nyttjandegrad</th>
    </tr>
  </thead>
  <tbody>
    {% for l in licenses %}
    <tr>
      <td><span class="dev-name">{{ l.name }}</span></td>
      <td style="text-align:right">{{ l.assigned }}</td>
      <td style="text-align:right">{% if l.total < 9999 %}{{ l.total }}{% else %}&mdash;{% endif %}</td>
      <td style="text-align:right">{% if l.total > 0 and l.total < 9999 %}{{ (l.assigned / l.total * 100) | round | int }}%{% else %}&mdash;{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if users %}
<div class="sub-label" style="margin-top:14px">Användare</div>
<table>
  <thead>
    <tr><th>Namn</th><th>E-post</th><th>Licenser</th><th style="text-align:center">Status</th><th style="text-align:center">MFA</th></tr>
  </thead>
  <tbody>
    {% for u in users %}
    <tr>
      <td><span class="dev-name">{{ u.name }}</span></td>
      <td style="color:#5C616B;font-size:10px">{{ u.email }}</td>
      <td style="font-size:10px;color:#5C616B">{{ u.licenses | join(', ') if u.licenses else '&mdash;' }}</td>
      <td style="text-align:center">
        {% if u.enabled %}<span class="badge s-ok">Aktiv</span>{% else %}<span class="badge" style="background:#F3F5F8;color:#5C616B">Inaktiv</span>{% endif %}
      </td>
      <td style="text-align:center">
        {% if not u.licenses %}<span style="color:#9499A2">N/A</span>
        {% elif u.mfa is not defined or u.mfa is none %}<span style="color:#9499A2">&mdash;</span>
        {% elif u.mfa %}<span class="badge s-ok">Ja</span>
        {% else %}<span class="badge s-err">Nej</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if admin_roles %}
<div class="sub-label" style="margin-top:14px">Admin-roller</div>
<table>
  <thead><tr><th>Roll</th><th>Medlemmar</th><th style="text-align:center">Risknivå</th></tr></thead>
  <tbody>
    {% for r in admin_roles %}
    <tr>
      <td><span class="dev-name">{{ r.role }}</span></td>
      <td style="font-size:10px;color:#5C616B">{{ r.members | join(', ') }}</td>
      <td style="text-align:center">
        {% if r.high_risk %}<span class="badge s-err">Hög</span>{% else %}<span class="badge" style="background:#F3F5F8;color:#5C616B">Normal</span>{% endif %}
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if inactive_licensed_users %}
<div class="sub-label" style="margin-top:14px">Inaktiva licensierade konton ({{ inactive_licensed_users | length }} st)</div>
<table>
  <thead><tr><th>Namn</th><th>E-post</th><th>Licenser</th><th style="text-align:right">Inaktiv sedan</th></tr></thead>
  <tbody>
    {% for u in inactive_licensed_users %}
    <tr>
      <td><span class="dev-name">{{ u.name }}</span></td>
      <td style="color:#5C616B;font-size:10px">{{ u.email }}</td>
      <td style="font-size:10px;color:#5C616B">{{ u.licenses | join(', ') }}</td>
      <td style="text-align:right">{% if u.never_signed_in %}<span class="badge s-warn">Aldrig inloggad</span>{% else %}{{ u.last_signin_days }} dagar{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if onedrive_users %}
<div class="sub-label" style="margin-top:14px">OneDrive-användning &mdash; totalt {{ onedrive_total_gb }} GB</div>
<table>
  <thead><tr><th>Användare</th><th style="text-align:right">Använt (GB)</th><th style="text-align:right">Senast aktiv</th></tr></thead>
  <tbody>
    {% for u in onedrive_users %}
    <tr>
      <td><span class="dev-name">{{ u.name }}</span><span style="color:#9499A2;font-size:10px;margin-left:6px">{{ u.email }}</span></td>
      <td style="text-align:right;font-weight:600">{{ u.used_gb }}</td>
      <td style="text-align:right;color:#5C616B;font-size:10px">{{ u.last_activity or '&mdash;' }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if sharepoint_sites %}
<div class="sub-label" style="margin-top:14px">SharePoint-sajter &mdash; totalt {{ sharepoint_total_gb }} GB</div>
<table>
  <thead><tr><th>Sajt</th><th>Ägare</th><th style="text-align:right">Använt (GB)</th><th style="text-align:right">Senast aktiv</th></tr></thead>
  <tbody>
    {% for s in sharepoint_sites %}
    <tr>
      <td><span class="dev-name">{{ s.name }}</span></td>
      <td style="color:#5C616B;font-size:10px">{{ s.owner }}</td>
      <td style="text-align:right;font-weight:600">{{ s.used_gb }}</td>
      <td style="text-align:right;color:#5C616B;font-size:10px">{{ s.last_activity or '&mdash;' }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

{% if intune_devices %}
<div class="sub-label" style="margin-top:14px">Intune-enheter ({{ intune_devices | length }} st)</div>
<table>
  <thead><tr><th>Enhet</th><th>Användare</th><th>OS</th><th style="text-align:center">Kompatibilitet</th><th style="text-align:right">Synkad</th></tr></thead>
  <tbody>
    {% for d in intune_devices %}
    <tr>
      <td><span class="dev-name">{{ d.name }}</span></td>
      <td style="color:#5C616B;font-size:10px">{{ d.user }}</td>
      <td style="font-size:10px;color:#5C616B">{{ d.os }}</td>
      <td style="text-align:center">
        {% if d.compliance_key == 'compliant' %}<span class="badge s-ok">Godkänd</span>
        {% elif d.compliance_key == 'noncompliant' %}<span class="badge s-err">Ej godkänd</span>
        {% else %}<span class="badge" style="background:#F3F5F8;color:#5C616B">{{ d.compliance }}</span>{% endif %}
      </td>
      <td style="text-align:right;color:#5C616B;font-size:10px">{% if d.last_sync_days is not none %}{{ d.last_sync_days }} dagar sedan{% else %}&mdash;{% endif %}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}
"""

ACRONIS_SECTION_TEMPLATE = """
<div class="section-break"></div>
<div class="section-title">Acronis Backup</div>
<table>
  <thead>
    <tr>
      <th>Enhet</th>
      <th>Senaste körning</th>
      <th>Status</th>
      <th style="text-align:right">Skyddad data</th>
    </tr>
  </thead>
  {% for job in jobs %}
  <tbody style="page-break-inside:avoid;break-inside:avoid">
    <tr>
      <td><span class="dev-name">{{ job.device_name }}</span></td>
      <td style="color:#5C616B">{{ job.last_run or '&mdash;' }}</td>
      <td>
        {% if job.status == 'ok' %}<span class="badge s-ok">OK</span>
        {% elif job.status == 'warning' %}<span class="badge s-warn">Varning</span>
        {% else %}<span class="badge s-err">Fel</span>{% endif %}
      </td>
      <td style="text-align:right;color:#5C616B">{{ job.protected_gb or '&mdash;' }} GB</td>
    </tr>
  </tbody>
  {% endfor %}
</table>
"""


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <style>{{ base_style }}</style>
</head>
<body>

  <div class="header">
    <div class="logo-wrap">
      <svg viewBox="0 0 695.39 84.24" width="140" height="17" xmlns="http://www.w3.org/2000/svg">
        <path fill="#141414" d="M236.18,455.57v15H201.74v69h-15v-69H152.3v-15Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M263.3,478.13v7.8h54v15h-54v15.84a7.74,7.74,0,0,0,7.68,7.68H332.3v15H271a22.63,22.63,0,0,1-22.56-22.67V478.13A22.64,22.64,0,0,1,271,455.45H332.3v15H271A7.73,7.73,0,0,0,263.3,478.13Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M412.1,524.69l7.68,15H403l-7.68-15-8-15.72-.36-.72a14.87,14.87,0,0,0-12.72-7.2h-15v38.63h-15v-84h53a22.53,22.53,0,0,1,22.56,22.56,22.75,22.75,0,0,1-13.2,20.64,20,20,0,0,1-6.48,1.8Zm-14.88-38.64a7,7,0,0,0,3.12-.72,7.62,7.62,0,0,0,4.56-7,7.92,7.92,0,0,0-2.28-5.52,7.56,7.56,0,0,0-5.4-2.16h-38v15.48Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M512.78,539.44H496l-7.68-15-18.36-36-18.36,36-7.68,15H427.1l7.68-15,35.16-69,35.16,69Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M604.1,455.45v15H542.78a7.73,7.73,0,0,0-7.68,7.68v7.8h54v15H535v38.51H520.1V478.13a22.64,22.64,0,0,1,22.56-22.68Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M678.38,539.44h-16.8l-7.68-15-18.36-36-18.36,36-7.68,15H592.7l7.68-15,35.16-69,35.16,69Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M751.7,524.57v15H709.1a26.09,26.09,0,0,1-11.76-2.76,26.59,26.59,0,0,1-12.12-12.23,26.12,26.12,0,0,1-2.76-11.76V455.57h15v58.68a12,12,0,0,0,10.2,10.2Z" transform="translate(-152.3 -455.45)"/>
        <path fill="#141414" d="M811,489.17l36.35,50.27H828.85l-29-40.07-21.24,19.32v20.75h-15v-84h15v43l12.36-11.28,11.16-10.2,23.39-21.48h22.2Z" transform="translate(-152.3 -455.45)"/>
      </svg>
      <div class="logo-sub">Insight</div>
    </div>
    <div class="header-right">
      <span class="header-period">{{ period_label }}</span>
      <span class="header-label">Månadsrapport</span>
    </div>
  </div>

  <div class="hero">
    <div class="hero-customer">{{ customer_name }}</div>
    <div class="hero-meta">Genererad {{ generated_date }}</div>
  </div>

  <div class="body">
    {{ rendered_sections | safe }}
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
        {"label": _PL_LABELS.get(pl, pl.capitalize()), "devices": by_line[pl]}
        for pl in ordered
    ]


def _render_unifi(data: dict, env: Environment) -> str:
    hosts = data.get("hosts", [])
    for host in hosts:
        host["device_groups"] = _group_devices(host.get("devices", []))
    return env.from_string(UNIFI_SECTION_TEMPLATE).render(
        hosts=hosts,
        isp_avg_latency=data.get("isp_avg_latency"),
        isp_packet_loss=data.get("isp_packet_loss"),
        isp_uptime=data.get("isp_uptime"),
    )


def _render_microsoft(data: dict, env: Environment) -> str:
    mfa_total = data.get("mfa_total") or 0
    mfa_registered = data.get("mfa_registered") or 0
    mfa_pct = round(mfa_registered / mfa_total * 100) if mfa_total > 0 else 0

    users = sorted(data.get("users") or [], key=lambda u: u.get("name", "").lower())

    return env.from_string(MICROSOFT_SECTION_TEMPLATE).render(
        total_users=data.get("total_users") or 0,
        enabled_users=data.get("enabled_users") or 0,
        licenses=data.get("licenses") or [],
        users=users,
        mfa_total=mfa_total,
        mfa_registered=mfa_registered,
        mfa_pct=mfa_pct,
        secure_score=round(data["secure_score"]) if data.get("secure_score") is not None else None,
        secure_score_max=round(data["secure_score_max"]) if data.get("secure_score_max") is not None else None,
        admin_roles=data.get("admin_roles") or [],
        inactive_licensed_users=data.get("inactive_licensed_users") or [],
        onedrive_users=data.get("onedrive_users") or [],
        onedrive_total_gb=data.get("onedrive_total_gb") or 0,
        sharepoint_sites=data.get("sharepoint_sites") or [],
        sharepoint_total_gb=data.get("sharepoint_total_gb") or 0,
        intune_devices=data.get("intune_devices") or [],
    )


def _render_acronis(data: dict, env: Environment) -> str:
    return env.from_string(ACRONIS_SECTION_TEMPLATE).render(**data)


_RENDERERS = {
    "unifi": _render_unifi,
    "microsoft": _render_microsoft,
    "acronis": _render_acronis,
}


def _render_all(sections: dict, env: Environment) -> str:
    order = ["unifi", "microsoft", "acronis"]
    keys = [k for k in order if k in sections] + [k for k in sections if k not in order]
    parts = []
    for key in keys:
        if key not in _RENDERERS:
            continue
        try:
            parts.append(_RENDERERS[key](sections[key], env))
        except Exception:
            continue
    return "\n".join(parts)


def _ctx(customer_name: str, period: str, rendered: str) -> dict:
    return {
        "base_style": BASE_STYLE,
        "customer_name": customer_name,
        "period_label": _month_label(period),
        "generated_date": now_stockholm().strftime("%Y-%m-%d"),
        "rendered_sections": rendered,
        "sender": app_settings.get("graph_sender"),
    }


async def generate_preview_html(customer_name: str, period: str, sections: dict) -> str:
    env = Environment(loader=BaseLoader())
    rendered = _render_all(sections, env)
    return env.from_string(PAGE_TEMPLATE).render(**_ctx(customer_name, period, rendered))


async def generate_pdf(customer_name: str, period: str, sections: dict, customer_id: str) -> str:
    from weasyprint import HTML as WeasyprintHTML
    env = Environment(loader=BaseLoader())
    rendered = _render_all(sections, env)
    html_content = env.from_string(PAGE_TEMPLATE).render(**_ctx(customer_name, period, rendered))
    os.makedirs(settings.REPORTS_OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(settings.REPORTS_OUTPUT_DIR, f"{customer_id}_{period}.pdf")
    WeasyprintHTML(string=html_content).write_pdf(pdf_path)
    return pdf_path
