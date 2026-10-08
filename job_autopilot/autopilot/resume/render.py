"""Render a (tailored) resume dict and a cover letter to PDF via headless Chromium."""

from __future__ import annotations

import copy
import html as _html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from pypdf import PdfReader

from autopilot import browser
from autopilot.text import highlight_html

_ENV = Environment(loader=FileSystemLoader(str(Path(__file__).parent)),
                   autoescape=select_autoescape(["html", "j2"]))


def _bullet_html(bullet: dict, keys) -> Markup:
    label = f"<strong>{_html.escape(bullet['label'])}:</strong> " if bullet.get("label") else ""
    return Markup(label + highlight_html(bullet["text"], keys))


def _view(resume: dict, keys) -> dict:
    view = copy.deepcopy(resume)
    view["summary_html"] = Markup(highlight_html(resume.get("summary", ""), keys))
    for group in view.get("skills", []):
        group["items_html"] = Markup(", ".join(highlight_html(i, keys) for i in group.get("items", [])))
    for block in view.get("experience", []) + view.get("projects", []):
        for bullet in block.get("bullets", []):
            bullet["html"] = _bullet_html(bullet, keys)
    return view


def render_html(resume: dict, highlight=(), compact: bool = False) -> str:
    return _ENV.get_template("template.html.j2").render(r=_view(resume, highlight), compact=compact)


def html_to_pdf(html: str, out: Path) -> int:
    """Print *html* to *out* (A4); returns the page count."""
    out.parent.mkdir(parents=True, exist_ok=True)
    page = browser.headless_browser().new_page()
    try:
        page.set_content(html, wait_until="load")
        page.pdf(path=str(out), format="A4", print_background=True, prefer_css_page_size=True)
    finally:
        page.close()
    return len(PdfReader(str(out)).pages)


def render_resume_pdf(resume: dict, out_pdf: Path, highlight=(), max_pages: int = 2) -> int:
    """Render, tightening the layout and then dropping the least relevant
    project (projects arrive sorted by relevance) until it fits *max_pages*."""
    resume = copy.deepcopy(resume)
    pages, html = 0, ""
    for attempt in range(5):
        html = render_html(resume, highlight, compact=attempt > 0)
        pages = html_to_pdf(html, out_pdf)
        if pages <= max_pages:
            break
        if attempt >= 1 and len(resume.get("projects", [])) > 2:
            resume["projects"] = resume["projects"][:-1]
    out_pdf.with_suffix(".html").write_text(html, encoding="utf-8")
    return pages


_LETTER = """<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 22mm 22mm; }}
body {{ font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.5; color: #161616; }}
.head {{ margin-bottom: 18px; }} .head b {{ font-size: 13pt; }} p {{ margin: 0 0 10px; }}
</style></head><body><div class="head"><b>{name}</b><br>{contact}</div>{body}</body></html>"""


def render_letter_pdf(name: str, contact_line: str, body_text: str, out_pdf: Path) -> None:
    paragraphs = "".join(f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>"
                         for p in body_text.split("\n\n") if p.strip())
    html_to_pdf(_LETTER.format(name=_html.escape(name), contact=_html.escape(contact_line),
                               body=paragraphs), out_pdf)
