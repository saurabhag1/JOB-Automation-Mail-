"""The paper trail: an append-only CSV of every application attempt, and an
HTML report per run with what was sent, what needs you, and clean links for
the jobs you apply to by hand (each with its tailored resume ready)."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from autopilot.db import Database

CSV_FIELDS = ["applied_at", "status", "channel", "tier", "score", "company", "title", "location", "source",
              "job_url", "apply_url", "recipient", "resume", "detail"]


def append_csv(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow(row)


def export_csv(db: Database, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for a in db.applications():
            writer.writerow({"applied_at": a["created_at"], "status": a["status"], "channel": a["channel"],
                             "tier": a["tier"], "score": a["score"], "company": a["company"], "title": a["title"],
                             "location": a["location"], "source": a["source"], "job_url": a["url"],
                             "apply_url": a["apply_url"], "recipient": a["recipient"], "resume": a["resume_path"],
                             "detail": a["detail"]})
    return path


_TIER = {1: "1 · Remote", 2: "2 · India", 3: "3 · Abroad"}
_CSS = """
:root { color-scheme: light dark; --fg:#1b1b1b; --muted:#666; --line:#e3e3e3; --bg:#fff; --card:#f7f7f8; --accent:#2563eb; }
@media (prefers-color-scheme: dark) { :root { --fg:#ececec; --muted:#a0a0a0; --line:#333; --bg:#151515; --card:#1f1f1f; --accent:#7aa2ff; } }
body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, Arial, sans-serif; color: var(--fg); background: var(--bg);
       max-width: 1100px; margin: 24px auto; padding: 0 16px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 28px 0 8px; }
.muted { color: var(--muted); } a { color: var(--accent); }
.stats { display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 110px; }
.stat b { display: block; font-size: 22px; }
table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.wrap { overflow-x: auto; }
"""


def _row_link(url: str, label: str = "open") -> str:
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{label}</a>' if url else ""


def write_report(db: Database, out_dir: Path, since: str, title: str = "Job Autopilot run") -> Path:
    apps = db.applications(since=since)
    counts = Counter(a["status"] for a in apps)
    rows = {"applied": [], "attention": [], "manual": [], "other": []}
    for a in apps:
        bucket = ("applied" if a["status"] in ("submitted", "submitted_unconfirmed", "already_applied")
                  else "attention" if a["status"] == "needs_attention"
                  else "manual" if a["status"] == "manual" else "other")
        rows[bucket].append(a)

    def table(items, extra_header: str, extra) -> str:
        if not items:
            return '<p class="muted">Nothing here.</p>'
        body = "".join(
            f"<tr><td>{html.escape(_TIER.get(a['tier'], str(a['tier'] or '')))}</td>"
            f"<td><b>{html.escape(a['company'] or '')}</b><br>{html.escape(a['title'] or '')}"
            f"<div class=muted>{html.escape(a['location'] or '')}</div></td>"
            f"<td>{html.escape(a['channel'])}</td><td>{extra(a)}</td>"
            f"<td>{_row_link(a['apply_url'] or a['url'], 'apply link')}<br>"
            f"{_row_link(Path(a['resume_path']).parent.as_uri() if a['resume_path'] else '', 'tailored files')}</td></tr>"
            for a in sorted(items, key=lambda a: (a["tier"] or 9, -(a["score"] or 0))))
        return (f'<div class="wrap"><table><tr><th>Priority</th><th>Job</th><th>Channel</th><th>{extra_header}</th>'
                f"<th>Links</th></tr>{body}</table></div>")

    pending = db.pending()
    pending_html = ("".join(f"<li>{html.escape(p['question'])}"
                            f"{' <span class=muted>(' + ', '.join(json.loads(p['options'] or '[]')[:6]) + ')</span>' if json.loads(p['options'] or '[]') else ''}</li>"
                            for p in pending) or "<li class=muted>None.</li>")
    stat = lambda label, n: f'<div class="stat"><b>{n}</b>{label}</div>'  # noqa: E731
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>
<h1>{html.escape(title)}</h1><div class="muted">Since {html.escape(since)} · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div class="stats">{stat('submitted', counts['submitted'] + counts['submitted_unconfirmed'])}{stat('need you', counts['needs_attention'])}
{stat('apply by hand', counts['manual'])}{stat('dry run', counts['dry_run'])}{stat('failed', counts['failed'])}</div>
<h2>Applied</h2>{table(rows['applied'], 'Result', lambda a: html.escape(a['detail'] or a['status']))}
<h2>Needs your attention</h2>{table(rows['attention'], 'Why', lambda a: html.escape(a['detail'] or ''))}
<h2>Apply by hand (tailored resume ready)</h2>{table(rows['manual'], 'Note', lambda a: html.escape(a['detail'] or ''))}
<h2>Other results</h2>{table(rows['other'], 'Result', lambda a: html.escape(f"{a['status']}: {a['detail'] or ''}"))}
<h2>Questions waiting for your answer</h2><p class="muted">Answer once with <code>python -m autopilot answers --pending</code>; they are reused forever.</p><ul>{pending_html}</ul>
</body></html>"""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_{datetime.now().strftime('%Y-%m-%d_%H%M')}.html"
    path.write_text(page, encoding="utf-8")
    return path
