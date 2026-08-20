"""Digest email rendering — HTML and plaintext.

Email HTML is not web HTML. Gmail strips <style> blocks, Outlook renders through
Word, and flexbox/grid are unavailable. So this is table-based layout with
inline styles only — verbose, but it is the one approach that renders correctly
everywhere.

Layout follows the spec: the TOTAL first, then one block per new role showing
the company name and logo.

Logos resolve to a favicon service (icons.duckduckgo.com), overridable per
company via WatchedCompany.logoUrl. NOT Clearbit: logo.clearbit.com no longer
resolves — the free logo API was retired — so every image would have been
broken. Clients that block remote images fall back to a lettermark built from
CSS only, so the digest never looks broken either way.
"""

from __future__ import annotations

import html
from collections import OrderedDict
from datetime import datetime

# Palette — muted, high-contrast, readable in both light and dark clients.
_INK = "#111827"
_MUTED = "#6b7280"
_LINE = "#e5e7eb"
_ACCENT = "#2563eb"
_BG = "#f6f7f9"
_CARD = "#ffffff"

# Deterministic lettermark colours, so a company keeps the same badge each time.
_MARK_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#0891b2", "#16a34a"]


def logo_url(posting: dict) -> str | None:
    """Explicit override, else a favicon by domain, else None (lettermark).

    Email cannot retry a failed image the way the web UI does, so there is one
    shot: the favicon service that answered most reliably when checked.
    """
    override = posting.get("companyLogoUrl")
    if override:
        return override
    domain = (posting.get("companyDomain") or "").strip().lower()
    if not domain:
        return None
    return f"https://icons.duckduckgo.com/ip3/{domain}.ico"


def _mark_color(name: str) -> str:
    return _MARK_COLORS[sum(ord(c) for c in name) % len(_MARK_COLORS)]


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def group_by_company(postings: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Group postings under their company, preserving the query's ordering."""
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for posting in postings:
        grouped.setdefault(posting.get("companyName") or "Unknown", []).append(posting)
    return grouped


def _fmt_date(value: object) -> str | None:
    """Render a posted date as 'today' / 'Aug 19' — never a raw timestamp."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    today = datetime.now(moment.tzinfo).date() if moment.tzinfo else datetime.now().date()
    delta = (today - moment.date()).days
    if delta <= 0:
        return "posted today"
    if delta == 1:
        return "posted yesterday"
    return f"posted {moment.strftime('%b %-d')}"


def subject_line(postings: list[dict]) -> str:
    """e.g. "7 new roles — Anthropic, Ramp, Linear +2"."""
    if not postings:
        return "Job Board — nothing new"
    grouped = group_by_company(postings)
    names = list(grouped)
    count = len(postings)
    noun = "role" if count == 1 else "roles"
    shown = ", ".join(names[:3])
    if len(names) > 3:
        shown += f" +{len(names) - 3}"
    return f"{count} new {noun} — {shown}"


def _logo_cell(company: str, url: str | None) -> str:
    """44px logo, or a coloured lettermark when no logo resolves.

    The <img> carries the lettermark's colour as its own background, so a client
    that blocks remote images still shows a coloured block with the initial in
    the alt text rather than a broken-image icon.
    """
    initial = _esc(company[:1].upper() or "?")
    color = _mark_color(company)
    if url:
        return (
            f'<img src="{_esc(url)}" width="44" height="44" alt="{initial}" '
            f'style="width:44px;height:44px;border-radius:10px;object-fit:contain;'
            f'background:{color};color:#ffffff;font:600 18px/44px -apple-system,'
            f'Segoe UI,Helvetica,Arial,sans-serif;text-align:center;display:block;" />'
        )
    return (
        f'<div style="width:44px;height:44px;border-radius:10px;background:{color};'
        f'color:#ffffff;font:600 18px/44px -apple-system,Segoe UI,Helvetica,Arial,'
        f'sans-serif;text-align:center;">{initial}</div>'
    )


def _role_row(posting: dict) -> str:
    title = _esc(posting.get("title"))
    url = _esc(posting.get("url") or "#")
    role = _esc(posting.get("matchedRole"))
    bits = [b for b in (_esc(posting.get("location")) or None, _fmt_date(posting.get("postedAt"))) if b]
    meta = " &middot; ".join(bits)
    return f"""
              <tr>
                <td style="padding:10px 0 10px 0;border-top:1px solid {_LINE};">
                  <a href="{url}" style="color:{_INK};font-size:15px;font-weight:600;text-decoration:none;">{title}</a>
                  <div style="margin-top:3px;font-size:12px;color:{_MUTED};">
                    <span style="display:inline-block;padding:1px 7px;border-radius:99px;background:{_BG};color:{_ACCENT};font-weight:600;">{role}</span>
                    {f'<span style="margin-left:6px;">{meta}</span>' if meta else ''}
                  </div>
                </td>
                <td align="right" style="padding:10px 0;border-top:1px solid {_LINE};white-space:nowrap;vertical-align:top;">
                  <a href="{url}" style="color:{_ACCENT};font-size:13px;font-weight:600;text-decoration:none;">View &rarr;</a>
                </td>
              </tr>"""


def _company_block(company: str, postings: list[dict]) -> str:
    count = len(postings)
    rows = "".join(_role_row(p) for p in postings)
    return f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
               style="background:{_CARD};border:1px solid {_LINE};border-radius:12px;margin-bottom:14px;">
          <tr>
            <td style="padding:16px 18px 4px 18px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td width="44" style="vertical-align:middle;">{_logo_cell(company, logo_url(postings[0]))}</td>
                  <td style="padding-left:12px;vertical-align:middle;">
                    <div style="font-size:16px;font-weight:700;color:{_INK};">{_esc(company)}</div>
                    <div style="font-size:12px;color:{_MUTED};">{count} new {'role' if count == 1 else 'roles'}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:4px 18px 14px 18px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">{rows}
              </table>
            </td>
          </tr>
        </table>"""


def render_digest_html(postings: list[dict], *, app_url: str = "http://localhost:3001/job-board") -> str:
    """Full HTML digest: total banner, then one card per company."""
    grouped = group_by_company(postings)
    count = len(postings)
    companies = len(grouped)

    if count == 0:
        headline = "No new roles"
        sub = "Nothing new since the last digest."
        blocks = ""
    else:
        headline = f"{count} new {'role' if count == 1 else 'roles'}"
        sub = f"across {companies} {'company' if companies == 1 else 'companies'}"
        blocks = "".join(_company_block(name, items) for name, items in grouped.items())

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{_BG};">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:{_BG};padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"
             style="max-width:560px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
        <tr>
          <td align="center" style="background:{_CARD};border:1px solid {_LINE};border-radius:12px;padding:26px 18px;margin-bottom:14px;">
            <div style="font-size:32px;font-weight:800;color:{_INK};line-height:1.1;">{headline}</div>
            <div style="font-size:14px;color:{_MUTED};margin-top:6px;">{sub}</div>
          </td>
        </tr>
        <tr><td style="height:14px;line-height:14px;">&nbsp;</td></tr>
        <tr><td>{blocks}</td></tr>
        <tr>
          <td align="center" style="padding:8px 0 0 0;">
            <a href="{_esc(app_url)}" style="color:{_ACCENT};font-size:13px;font-weight:600;text-decoration:none;">Open the Job Board &rarr;</a>
            <div style="font-size:11px;color:{_MUTED};margin-top:10px;">Monitoring your watched career pages every 3 hours.</div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def render_digest_text(postings: list[dict], *, app_url: str = "http://localhost:3001/job-board") -> str:
    """Plaintext alternative. Required: html-only mail scores badly with filters."""
    if not postings:
        return f"No new roles since the last digest.\n\n{app_url}\n"

    grouped = group_by_company(postings)
    count = len(postings)
    lines = [
        f"{count} new {'role' if count == 1 else 'roles'} across "
        f"{len(grouped)} {'company' if len(grouped) == 1 else 'companies'}",
        "=" * 46,
        "",
    ]
    for company, items in grouped.items():
        lines.append(f"{company} ({len(items)})")
        for posting in items:
            bits = [b for b in (posting.get("location"), _fmt_date(posting.get("postedAt"))) if b]
            meta = f"  [{' · '.join(str(b) for b in bits)}]" if bits else ""
            lines.append(f"  - {posting.get('title')}{meta}")
            lines.append(f"    {posting.get('url')}")
        lines.append("")
    lines.append(app_url)
    return "\n".join(lines)
