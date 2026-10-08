"""Turn the master resume PDF into structured JSON (data/master_resume.json).

pypdf's *layout* mode keeps each visual line intact - bullets, right-aligned
dates, centred headings - so a line classifier is enough for a conventional
one-column resume. The JSON is the source of truth afterwards: review it once,
fix anything the parser got wrong, and every tailored resume is built from it.
Nothing is ever added that is not in the PDF; tailoring only reorders and
rephrases these facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

from autopilot.text import EMAIL_RE

SECTION_NAMES = {
    "summary": ["professional summary", "summary", "profile summary", "profile", "career objective",
                "objective", "about me"],
    "skills": ["technical skill set", "technical skills", "skills", "skill set", "core competencies",
               "key skills", "technical expertise"],
    "experience": ["work experience", "experience", "professional experience", "employment history",
                   "work history", "employment"],
    "projects": ["projects", "key projects", "personal projects", "academic projects"],
    "education": ["education", "academic qualifications", "qualifications", "academics"],
    "certifications": ["certifications", "certificates", "licenses certifications",
                       "licenses and certifications", "certification"],
    "extras": ["extracurricular activities", "extra curricular activities", "activities", "achievements",
               "awards", "volunteering", "leadership", "community"],
}
_HEADING_LOOKUP = {name: section for section, names in SECTION_NAMES.items() for name in names}

BULLET_CHARS = "●•▪◦○■➢✓►–-*"
_BULLET = re.compile(rf"^\s*[{re.escape(BULLET_CHARS)}]\s+")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RANGE = re.compile(rf"(?i)({_MONTH}\s*\d{{4}}|\b(?:19|20)\d{{2}}\b).*?(present|current|till date|"
                         rf"{_MONTH}\s*\d{{4}}|\b(?:19|20)\d{{2}}\b)|({_MONTH}\s*\d{{4}})")
_INLINE_SUMMARY = re.compile(r"(?i)^\s*(professional\s+summary|summary|profile)\s*:\s*(.*)$")
_LABELLED_BULLET = re.compile(r"^([A-Z][^:]{2,60}?):\s+(\S.*)$")
_URL = re.compile(r"https?://[^\s|,]+")
_PHONE = re.compile(r"(\+?\d[\d\s\-]{8,}\d)")


class ResumeParseError(Exception):
    pass


def tidy(text: str) -> str:
    """Collapse PDF spacing artefacts: 'Pune , India' -> 'Pune, India'."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s+([,;:])", r"\1", text)
    text = re.sub(r"\s+\.(?=\s|$)", ".", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\.\.$", ".", text)
    return text


def pdf_lines(pdf: Path) -> tuple[list[str], list[str]]:
    """(visual lines, link URIs) from every page."""
    reader = PdfReader(str(pdf))
    lines, links = [], []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout", layout_mode_space_vertically=False) or ""
        lines.extend(line.rstrip() for line in text.splitlines() if line.strip())
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            uri = (obj.get("/A") or {}).get("/URI")
            if uri and uri not in links:
                links.append(str(uri))
    return lines, links


def pdf_plain_text(pdf: Path) -> str:
    lines, _ = pdf_lines(pdf)
    return "\n".join(lines)


def _heading_of(line: str) -> str | None:
    """Section key if *line* is a heading such as 'WORK EXPERIENCE'."""
    stripped = line.strip()
    letters = re.sub(r"[^A-Za-z]", "", stripped)
    if not letters or len(stripped.split()) > 5:
        return None
    norm = re.sub(r"[^a-z]+", " ", stripped.lower()).strip()
    section = _HEADING_LOOKUP.get(norm)
    # Headings are written in capitals (or Title Case and alone on the line).
    if section and (letters.isupper() or stripped.endswith(":") or len(norm.split()) <= 3):
        return section
    return None


def _split_dates(line: str) -> tuple[str, str]:
    """'Company, City        Dec 2025 - Present' -> ('Company, City', 'Dec 2025 - Present')."""
    parts = re.split(r"\s{3,}", line.strip())
    if len(parts) >= 2 and _DATE_RANGE.search(parts[-1]):
        return tidy(" ".join(parts[:-1])), tidy(parts[-1])
    return tidy(line), ""


def _split_items(text: str) -> list[str]:
    """Split 'AWS (EC2, S3), GCP.' on commas outside parentheses."""
    text = text.strip().rstrip(".")
    if text.count("(") > text.count(")"):
        # An unclosed '(' would swallow everything after it ("AWS (EC2, ... (ALB),GCP").
        # Close it before the final ', Item' so that item stays separate.
        m = re.search(r"\),\s*([^,()]{1,30})$", text)
        if m:
            return _split_items(text[: m.start() + 1] + ")") + [m.group(1).strip()]
    items, depth, buf = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth <= 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    cleaned = []
    for item in items:
        item = re.sub(r"^\s*and\s+", "", item.strip()).strip(" .")
        if item:
            cleaned.append(item)
    return cleaned


def _bullet(text: str, bid: str) -> dict:
    text = tidy(text)
    m = _LABELLED_BULLET.match(text)
    if m and len(m.group(1).split()) <= 6:
        return {"id": bid, "label": m.group(1).strip(), "text": m.group(2).strip()}
    return {"id": bid, "label": "", "text": text}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse_pdf(pdf: Path) -> dict:
    lines, links = pdf_lines(pdf)
    if not lines:
        raise ResumeParseError(f"No text found in {pdf} (is it a scanned image?)")

    header: list[str] = []
    sections: dict[str, list[str]] = {key: [] for key in SECTION_NAMES}
    current = None
    for line in lines:
        m = _INLINE_SUMMARY.match(line)
        if m and current in (None, "summary"):
            current = "summary"
            if m.group(2).strip():
                sections["summary"].append(m.group(2))
            continue
        heading = _heading_of(line)
        if heading:
            current = heading
            continue
        if current is None:
            header.append(line)
        else:
            sections[current].append(line)

    resume = _parse_header(header, links)
    resume["summary"] = tidy(" ".join(sections["summary"]))
    resume["skills"] = _parse_skills(sections["skills"])
    resume["experience"] = _parse_experience(sections["experience"])
    resume["projects"] = _parse_projects(sections["projects"])
    resume["education"] = _parse_dated_lines(sections["education"])
    resume["certifications"] = _parse_dated_lines(sections["certifications"])
    resume["extras"] = _parse_groups(sections["extras"])
    resume["links"] = links
    if not resume["experience"] or not any(e["bullets"] for e in resume["experience"]):
        raise ResumeParseError("Could not find work experience bullets in the PDF.")
    return resume


def _parse_header(lines: list[str], links: list[str]) -> dict:
    name = tidy(lines[0]) if lines else ""
    joined = " ".join(lines[1:])
    contact = {"email": "", "phone": "", "location": "", "linkedin": "", "github": "", "portfolio": ""}
    if m := EMAIL_RE.search(joined):
        contact["email"] = m.group(0)
    # Look for the phone only after its label so dates/URLs can't be mistaken for it.
    phone_zone = re.search(r"(?i)(contact|phone|mobile|tel)\s*:?\s*(.{0,40})", joined)
    if phone_zone and (m := _PHONE.search(phone_zone.group(2))):
        contact["phone"] = tidy(m.group(1))
    if m := re.search(r"(?i)(?:address|location)\s*:\s*([^|]+)", joined):
        contact["location"] = tidy(m.group(1))
    urls = _URL.findall(joined) + links
    for url in urls:
        url = url.rstrip("/.") + ("/" if url.endswith("/") else "")
        low = url.lower()
        if "linkedin.com/in/" in low and not contact["linkedin"]:
            contact["linkedin"] = url
        elif "github.com/" in low and not contact["github"]:
            contact["github"] = url
    if m := re.search(r"(?i)portfolio\s*:\s*(https?://\S+)", joined):
        contact["portfolio"] = m.group(1).rstrip("|")

    headline, tagline = "", []
    for line in lines[1:]:
        if re.search(r"(?i)(address|email|contact|phone|linkedin|github|portfolio)\s*:", line):
            continue
        text = tidy(line)
        if "|" in text and not headline:
            headline = " | ".join(p.strip() for p in text.split("|") if p.strip())
        elif text:
            tagline.append(text)
    return {"name": name, "contact": contact, "headline": headline, "tagline": " ".join(tagline)}


def _collect_bullets(lines: list[str]):
    """Yield ('header', text, raw) / ('bullet', text, raw) with wrapped
    continuation lines folded into the bullet they belong to."""
    items: list[list] = []
    for raw in lines:
        if _BULLET.match(raw):
            items.append(["bullet", _BULLET.sub("", raw), raw])
        elif items and items[-1][0] == "bullet" and _indent(raw) >= 4 and not _DATE_RANGE.search(raw[-30:]):
            items[-1][1] += " " + raw.strip()
        else:
            items.append(["header", raw.strip(), raw])
    return items


def _parse_skills(lines: list[str]) -> list[dict]:
    skills = []
    for n, (kind, text, _) in enumerate(_collect_bullets(lines), 1):
        text = tidy(text)
        if ":" in text:
            category, _, rest = text.partition(":")
        else:
            category, rest = "", text
        skills.append({"id": f"s{n}", "category": category.strip(), "items": _split_items(rest)})
    return skills


def _parse_experience(lines: list[str]) -> list[dict]:
    roles: list[dict] = []
    for kind, text, raw in _collect_bullets(lines):
        if kind == "header":
            left, dates = _split_dates(raw)
            if dates:
                company, _, location = left.partition(",")
                roles.append({"id": f"e{len(roles) + 1}", "org_line": left, "company": company.strip(),
                              "location": location.strip(), "title": "", "dates": dates, "bullets": []})
            elif roles and not roles[-1]["title"] and not re.match(r"(?i)roles?\s*&", text):
                roles[-1]["title"] = tidy(text)
            continue
        if roles:
            role = roles[-1]
            role["bullets"].append(_bullet(text, f"{role['id']}.b{len(role['bullets']) + 1}"))
    return roles


def _parse_projects(lines: list[str]) -> list[dict]:
    projects: list[dict] = []
    for kind, text, raw in _collect_bullets(lines):
        if kind == "header":
            name, _, tech = tidy(text).partition(" | ")
            projects.append({"id": f"p{len(projects) + 1}", "name": name.strip(), "tech": tech.strip(),
                             "bullets": []})
        elif projects:
            proj = projects[-1]
            proj["bullets"].append(_bullet(text, f"{proj['id']}.b{len(proj['bullets']) + 1}"))
    return projects


def _parse_dated_lines(lines: list[str]) -> list[dict]:
    out = []
    for _, text, raw in _collect_bullets(lines):
        left, dates = _split_dates(_BULLET.sub("", raw))
        out.append({"text": left, "dates": dates})
    return out


def _parse_groups(lines: list[str]) -> list[dict]:
    groups: list[dict] = []
    for kind, text, _ in _collect_bullets(lines):
        if kind == "header" or not groups:
            groups.append({"title": tidy(text) if kind == "header" else "", "bullets": []})
            if kind == "header":
                continue
        groups[-1]["bullets"].append(tidy(text))
    return groups


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def sha1_of(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def build_master(pdf: Path) -> dict:
    resume = parse_pdf(pdf)
    resume["_source"] = {
        "pdf": str(pdf),
        "sha1": sha1_of(pdf),
        "parsed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "note": "Review this file once. Fix anything misread; tailoring only reorders and rephrases it.",
    }
    return resume


def save_master(resume: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(resume, fh, indent=2, ensure_ascii=False)


def load_master(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def master_is_stale(resume: dict, pdf: Path) -> bool:
    return pdf.exists() and resume.get("_source", {}).get("sha1") != sha1_of(pdf)


def resume_text(resume: dict) -> str:
    """Every fact in the master resume as one string (for term/number checks)."""
    parts = [resume.get("headline", ""), resume.get("tagline", ""), resume.get("summary", "")]
    for group in resume.get("skills", []):
        parts.append(f"{group.get('category', '')}: {', '.join(group.get('items', []))}")
    for role in resume.get("experience", []):
        parts += [role.get("org_line", ""), role.get("title", "")]
        parts += [f"{b.get('label', '')}: {b['text']}" for b in role.get("bullets", [])]
    for proj in resume.get("projects", []):
        parts += [proj.get("name", ""), proj.get("tech", "")]
        parts += [f"{b.get('label', '')}: {b['text']}" for b in proj.get("bullets", [])]
    parts += [c.get("text", "") for c in resume.get("certifications", [])]
    parts += [e.get("text", "") for e in resume.get("education", [])]
    for group in resume.get("extras", []):
        parts.append(group.get("title", ""))
        parts += group.get("bullets", [])
    return "\n".join(p for p in parts if p)
