"""Generic, label-driven form filling with Playwright.

A scan script runs inside the page, tags every visible control with a
data-ap-id attribute and works out its question text the way a screen reader
would (label[for], aria-labelledby, aria-label, fieldset legend, nearby label
text). Python then answers each question through the AnswerBook and fills the
control by its tag. Nothing here is specific to one ATS, which is what lets
the same code drive Greenhouse, Lever, Ashby and LinkedIn's Easy Apply modal.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from autopilot.apply.answers import AnswerBook, Question, choose_option
from autopilot.models import Job

SCAN_JS = r"""
(root) => {
  const scope = root || document;
  const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    if (el.type === 'file') return true;           // often hidden behind an "Attach" button
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') return false;
    if (r.width > 0 && r.height > 0) return true;
    // visually-hidden radios/checkboxes whose label is what you click
    return (el.type === 'radio' || el.type === 'checkbox') && el.labels && el.labels.length > 0;
  };
  const textOf = (id) => { const n = document.getElementById(id); return n ? (n.innerText || n.textContent) : ''; };
  const ownLabel = (el) => {
    let t = '';
    if (el.labels && el.labels.length) t = Array.from(el.labels).map(l => l.innerText || l.textContent).join(' ');
    if (!t && el.getAttribute('aria-labelledby')) t = el.getAttribute('aria-labelledby').split(/\s+/).map(textOf).join(' ');
    if (!t && el.getAttribute('aria-label')) t = el.getAttribute('aria-label');
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText || l.textContent; }
    return clean(t);
  };
  // The question a control answers. For radios/checkboxes/buttons the
  // control's own option text ("Yes", "Less than 1 year") must be skipped.
  const questionText = (el, ownText) => {
    const own = clean(ownText || '');
    const ownBox = el.closest('label');
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector(':scope > legend, :scope > label, :scope > div > legend');
      if (lg && !lg.contains(el) && clean(lg.innerText) && clean(lg.innerText) !== own) return clean(lg.innerText);
    }
    const group = el.closest('[role="radiogroup"], [role="group"]');
    if (group) {
      const t = group.getAttribute('aria-label') ||
        (group.getAttribute('aria-labelledby') ? group.getAttribute('aria-labelledby').split(/\s+/).map(textOf).join(' ') : '');
      if (clean(t)) return clean(t);
    }
    let node = el.parentElement;
    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
      const cands = node.querySelectorAll(':scope > label, :scope > legend, :scope > div > label, :scope > [class*="label" i], ' +
        ':scope > [class*="question" i], :scope > [class*="title" i], :scope > p, :scope > span, :scope > h3, :scope > h4');
      for (const c of cands) {
        if (c.contains(el) || (ownBox && ownBox.contains(c))) continue;
        if (c.querySelector('input, select, textarea, button')) continue;
        const t = clean(c.innerText || c.textContent);
        if (t && t !== own && t.length < 400) return t;
      }
    }
    return '';
  };
  const requiredLabel = (el) => {
    const lab = (el.labels && el.labels[0]) || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
    return !!(lab && /required/i.test(lab.className || ''));
  };
  const required = (el, label) => el.required || el.getAttribute('aria-required') === 'true' ||
    /[*✱]\s*$/.test(label || '') || requiredLabel(el);
  const kindOf = (el) => {
    const role = el.getAttribute('role');
    if (role === 'radio') return 'radio';
    if (role === 'checkbox') return 'checkbox';
    if (el.tagName === 'SELECT') return 'select';
    if (el.tagName === 'TEXTAREA') return 'textarea';
    if (role === 'combobox' || el.getAttribute('aria-autocomplete')) return 'combobox';
    if (el.isContentEditable) return 'textarea';
    return (el.type || 'text').toLowerCase();
  };
  window.__apSeq = window.__apSeq || 0;
  const tag = (el) => { if (!el.dataset.apId) el.dataset.apId = String(++window.__apSeq); return el.dataset.apId; };
  const els = scope.querySelectorAll('input, select, textarea, [role="combobox"], [role="radio"], [role="checkbox"], [contenteditable="true"]');
  const out = []; const groups = {};
  for (const el of els) {
    const kind = kindOf(el);
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(kind)) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true' || !visible(el)) continue;
    if (el.closest('[aria-hidden="true"]') && kind !== 'file') continue;
    const id = tag(el);
    if (kind === 'radio' || kind === 'checkbox') {
      const container = el.closest('fieldset, [role="radiogroup"], [role="group"]');
      const name = el.name || (container ? tag(container) : 'solo' + id);
      const key = kind + ':' + name;
      const opt = ownLabel(el) || el.value || '';
      const checked = el.checked || el.getAttribute('aria-checked') === 'true';
      if (!groups[key]) {
        groups[key] = {apid: id, kind, name, label: questionText(el, opt), required: false, options: [], checked: [], value: ''};
        out.push(groups[key]);
      }
      const g = groups[key];
      g.options.push({text: clean(opt), value: el.value || '', apid: id});
      if (checked) g.checked.push(clean(opt));
      g.required = g.required || required(el, g.label);
      continue;
    }
    const label = ownLabel(el) || questionText(el) || el.placeholder || el.name || el.id || '';
    const options = kind === 'select' ? Array.from(el.options).map(o => ({text: clean(o.text), value: o.value})) : [];
    out.push({apid: id, kind, name: el.name || el.id || '', label: clean(label), required: required(el, label),
              options, checked: [], value: kind === 'file' ? (el.files && el.files.length ? 'file' : '') :
              (el.isContentEditable ? clean(el.innerText) : (el.value || '')), accept: el.accept || ''});
  }
  // Yes/No answered with toggle buttons (Ashby): <button aria-pressed> siblings.
  const seenBoxes = new Set();
  for (const btn of scope.querySelectorAll('button[aria-pressed]')) {
    const box = btn.parentElement;
    if (!box || seenBoxes.has(box) || !visible(btn)) continue;
    seenBoxes.add(box);
    const buttons = Array.from(box.querySelectorAll(':scope > button[aria-pressed]'));
    if (buttons.length < 2 || buttons.length > 6) continue;
    const label = questionText(box, '');
    const hidden = box.querySelector('input');
    out.push({apid: tag(box), kind: 'buttons', name: hidden ? hidden.name : '', label,
              required: /required/i.test((box.parentElement && box.parentElement.querySelector('label') || {}).className || '') ||
                        /[*✱]\s*$/.test(label),
              options: buttons.map(b => ({text: clean(b.innerText), value: b.dataset.option || '', apid: tag(b)})),
              checked: buttons.filter(b => b.getAttribute('aria-pressed') === 'true').map(b => clean(b.innerText)),
              value: ''});
  }
  for (const g of Object.values(groups)) {
    // A lone checkbox is usually a consent line: its own label is the question.
    if (g.kind === 'checkbox' && g.options.length === 1 && (!g.label || g.label.length < 3)) g.label = g.options[0].text;
    if (g.kind === 'checkbox' && g.options.length > 1) g.kind = 'checkbox_group';
    if (!g.label && g.options.length) g.label = g.options.map(o => o.text).join(' / ');
  }
  return out;
}
"""

CHALLENGE_SELECTORS = (
    'iframe[src*="recaptcha"][src*="bframe"]', 'iframe[title*="challenge" i]',
    'iframe[src*="hcaptcha"][src*="challenge"]', 'iframe[src*="challenges.cloudflare.com"]',
    '#px-captcha', 'text=/verify you are human/i',
)
TEXT_KINDS = ("text", "email", "tel", "url", "number", "textarea", "date", "password")


@dataclass
class Field:
    apid: str
    kind: str
    name: str
    label: str
    required: bool
    options: list[dict] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    value: str = ""
    accept: str = ""

    @property
    def is_empty(self) -> bool:
        if self.kind in ("radio", "checkbox_group", "buttons"):
            return not self.checked
        if self.kind == "checkbox":
            return not self.checked and self.required
        if self.kind == "select":
            return not self.value or self.value.lower() in ("select an option", "select", "")
        return not (self.value or "").strip()


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    unknown_required: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def scan(page, root=None) -> list[Field]:
    """Fields inside *root* (a Locator) or the whole page/frame."""
    raw = root.evaluate(SCAN_JS) if root is not None else page.evaluate(SCAN_JS, None)
    return [Field(**{k: v for k, v in item.items() if k in Field.__dataclass_fields__}) for item in raw]


def _loc(page, apid: str):
    return page.locator(f'[data-ap-id="{apid}"]').first


def file_purpose(f: Field) -> str | None:
    text = f"{f.label} {f.name}".lower()
    if "cover" in text:
        return "cover"
    if re.search(r"resume|\bcv\b|curriculum|upload|attach", text) or f.name in ("resume", "file"):
        return "resume"
    return None


def _truthy(answer: str) -> bool:
    return bool(re.match(r"^(yes|y|true|i agree|agree|i accept|accept|i consent|consent|i confirm|confirm|1)\b",
                         (answer or "").strip(), re.I))


def _fill_combobox(page, loc, answer: str) -> bool:
    loc.click()
    try:
        loc.fill("")
    except Exception:  # noqa: BLE001 - some comboboxes are not editable
        pass
    loc.type(answer[:40], delay=25)
    time.sleep(0.8)
    options = page.locator('[role="option"]:visible')
    texts = [t.strip() for t in options.all_inner_texts()]
    pick = choose_option(answer, texts) if texts else None
    if pick is not None:
        options.nth(texts.index(pick)).click()
        return True
    if texts:
        loc.press("Escape")
        return False
    loc.press("Enter")
    return True


def fill_field(page, f: Field, answer: str) -> bool:
    loc = _loc(page, f.apid)
    if f.kind in TEXT_KINDS:
        if f.kind == "number":
            answer = re.sub(r"[^\d.]", "", answer) or "0"
        if f.kind == "textarea" and loc.evaluate("e => e.isContentEditable"):
            loc.click()
            loc.type(answer)
        else:
            loc.fill(answer)
        return True
    if f.kind == "select":
        opts = [o for o in f.options if o.get("value") not in (None, "")]
        pick = choose_option(answer, [o["text"] for o in opts])
        if pick is None:
            return False
        loc.select_option(value=next(o["value"] for o in opts if o["text"] == pick))
        return True
    if f.kind in ("radio", "buttons"):
        pick = choose_option(answer, [o["text"] for o in f.options])
        if pick is None:
            return False
        target = _loc(page, next(o["apid"] for o in f.options if o["text"] == pick))
        if f.kind == "buttons":
            target.click()
        else:
            _click_choice(page, target)
        return True
    if f.kind == "checkbox":
        want = _truthy(answer)
        target = _loc(page, f.options[0]["apid"])
        is_checked = bool(f.checked)
        if want != is_checked:
            _click_choice(page, target)
        return True
    if f.kind == "checkbox_group":
        picks = [choose_option(part.strip(), [o["text"] for o in f.options]) for part in answer.split(",")]
        for o in f.options:
            if o["text"] in picks and o["text"] not in f.checked:
                _click_choice(page, _loc(page, o["apid"]))
        return any(picks)
    if f.kind == "combobox":
        return _fill_combobox(page, loc, answer)
    return False


def _click_choice(page, loc) -> None:
    """Radios/checkboxes are often visually hidden behind their label."""
    try:
        loc.check(force=True, timeout=3000)
        return
    except Exception:  # noqa: BLE001 - ARIA widgets have no .check(); click instead
        pass
    try:
        loc.click(timeout=3000)
    except Exception:  # noqa: BLE001
        apid = loc.get_attribute("data-ap-id")
        page.locator(f'label:has([data-ap-id="{apid}"])').first.click(timeout=3000)


def fill_form(page, answers: AnswerBook, job: Job, files: dict[str, Path], root=None, log=print) -> FillReport:
    """Answer every visible field once. Prefilled fields are left alone."""
    report = FillReport()
    for f in scan(page, root):
        question = re.sub(r"\s*[*✱]\s*$", "", f.label).strip()
        if f.kind == "file":
            purpose = file_purpose(f)
            if purpose and purpose in files and files[purpose] and not f.value:
                _loc(page, f.apid).set_input_files(str(files[purpose]))
                report.filled.append(f"{question or purpose} <- {files[purpose].name}")
            elif f.required and not f.value:
                report.unknown_required.append(question or "file upload")
            continue
        if not f.is_empty and f.kind != "checkbox":
            continue        # LinkedIn/Lever prefill name, email, phone - keep them
        q = Question(label=question, kind=f.kind, options=[o["text"] for o in f.options], required=f.required)
        answer = answers.answer(q, job)
        if answer is None or answer == "":
            (report.unknown_required if f.required else report.skipped).append(question)
            continue
        try:
            ok = fill_field(page, f, answer)
        except Exception as exc:  # noqa: BLE001 - one stubborn widget must not end the form
            ok = False
            log(f"    could not fill '{question[:60]}': {type(exc).__name__}")
        if ok:
            report.filled.append(question)
        elif f.required:
            report.unknown_required.append(question)
    return report


def missing_required(page, root=None) -> list[str]:
    return [re.sub(r"\s*[*✱]\s*$", "", f.label) for f in scan(page, root)
            if f.required and f.kind != "file" and f.is_empty]


def challenge_visible(page) -> bool:
    for sel in CHALLENGE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return True
        except Exception:  # noqa: BLE001 - detached frames during navigation
            continue
    return False
