"""Browser applications: Greenhouse / Lever / Ashby forms, LinkedIn Easy Apply
and Naukri, in a persistent Chrome profile you log in to once by hand.

When the bot can't continue honestly - a captcha, a login wall, a question it
has no true answer for - it does not guess. Interactive runs alert you and
wait while you finish in the browser window; unattended runs save a
screenshot and mark the job "needs_attention" with its link.

LinkedIn and Naukri automation is against those sites' terms and they limit
or restrict accounts that apply too fast. The channels are off by default,
capped per run, paced like a person, and never touch your password.
"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from autopilot import browser
from autopilot.apply import ALREADY, ATTENTION, MANUAL, SUBMITTED, SUBMITTED_UNCONFIRMED, ChannelDown, Outcome
from autopilot.apply.answers import AnswerBook, Question
from autopilot.apply.forms import challenge_visible, fill_form, missing_required
from autopilot.config import Settings
from autopilot.models import APPLY_LEVER, Job

CONFIRMED = re.compile(
    r"thank(?:s| you) for (?:applying|your application|your interest)|application (?:was |has been )?"
    r"(?:submitted|received|sent)|we.?ve received your application|your application (?:was|has been) sent|"
    r"successfully (?:applied|submitted)|you have successfully applied", re.I)
ALREADY_APPLIED = re.compile(r"already applied|you.?ve applied|application already submitted", re.I)
LOGIN_URL = re.compile(r"/login|/authwall|/checkpoint|/uas/|nlogin|/signin|sign-in", re.I)


def _pause(lo: float = 0.6, hi: float = 1.6) -> None:
    time.sleep(random.uniform(lo, hi))


class PortalSession:
    def __init__(self, settings: Settings, answers: AnswerBook, interactive: bool, auto_submit: bool, log=print):
        self.settings, self.answers, self.interactive, self.auto_submit, self.log = (
            settings, answers, interactive, auto_submit, log)
        self._page = None

    def page(self):
        if self._page is None or self._page.is_closed():
            ctx = browser.persistent_context(
                self.settings.path("browser.profile_dir", "data/browser-profile"),
                channel=self.settings.get("browser.channel", "chrome"),
                headless=bool(self.settings.get("browser.headless", False)),
                slow_mo=int(self.settings.get("browser.slow_mo_ms", 40)))
            self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._page.set_default_timeout(20000)
        return self._page

    def open(self, url: str):
        page = self.page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001 - busy pages never go idle; carry on
            pass
        _pause(1.0, 2.0)
        return page

    def snapshot(self, package, name: str) -> str:
        path = Path(package.folder) / f"{name}.png"
        try:
            self.page().screenshot(path=str(path), full_page=True)
        except Exception:  # noqa: BLE001
            return ""
        return str(path)

    def hand_over(self, job: Job, package, reason: str) -> Outcome:
        """Let you finish in the browser, then record what happened."""
        shot = self.snapshot(package, "needs_attention")
        if not self.interactive:
            return Outcome(ATTENTION, f"{reason} (screenshot: {Path(shot).name if shot else 'n/a'})")
        browser.notify("Job Autopilot needs you", f"{job.company}: {reason}")
        print(f"\n>>> {job.title} @ {job.company}: {reason}")
        print("    Finish it in the browser window (fill / solve / submit).")
        try:
            raw = input("    Press Enter once submitted, or type 's' to skip this job: ").strip().lower()
        except EOFError:
            raw = "s"
        if raw.startswith("s"):
            return Outcome(ATTENTION, f"skipped by you: {reason}")
        if confirmed(self.page()):
            return Outcome(SUBMITTED, "submitted after your help")
        try:
            yes = input("    I can't see a confirmation. Did you submit it? [y/N]: ").strip().lower().startswith("y")
        except EOFError:
            yes = False
        return Outcome(SUBMITTED_UNCONFIRMED if yes else ATTENTION,
                       "you confirmed submission" if yes else f"not submitted: {reason}")


def confirmed(page) -> bool:
    try:
        if re.search(r"confirmation|thank|/thanks|submitted|success", page.url, re.I):
            return True
        return bool(CONFIRMED.search(page.locator("body").inner_text(timeout=5000)))
    except Exception:  # noqa: BLE001 - navigation in progress
        return False


def _wait_confirmed(page, seconds: int = 25) -> str:
    """'confirmed' / 'challenge' / 'errors' / 'unknown' after clicking submit."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if confirmed(page):
            return "confirmed"
        if challenge_visible(page):
            return "challenge"
        time.sleep(1.0)
    if missing_required(page):
        return "errors"
    return "unknown"


# --------------------------------------------------------------------------- #
# Greenhouse / Lever / Ashby
# --------------------------------------------------------------------------- #
_SUBMIT = re.compile(r"^\s*submit(?: application| my application)?\s*$", re.I)


def apply_ats(sess: PortalSession, job: Job, package) -> Outcome:
    page = sess.open(job.apply_url or job.url)
    if ALREADY_APPLIED.search(page.locator("body").inner_text()):
        return Outcome(ALREADY, "the form says you already applied")
    files = {"resume": package.resume_pdf, "cover": package.letter_pdf}
    sess.answers.cover_text = package.cover_note
    sess.answers.source_label = "Company careers page"

    if job.apply_type == APPLY_LEVER:
        # Lever parses the uploaded resume and pre-fills name/email/phone/company.
        resume_input = page.locator('input[type="file"][name="resume"]').first
        if resume_input.count():
            resume_input.set_input_files(str(package.resume_pdf))
            _pause(3.0, 4.5)

    report = None
    for _ in range(2):   # second pass catches questions revealed by earlier answers
        report = fill_form(page, sess.answers, job, files, log=sess.log)
        _pause()
    missing = missing_required(page)
    sess.log(f"    filled {len(report.filled)} fields" + (f"; unanswered: {missing[:4]}" if missing else ""))
    if missing:
        return sess.hand_over(job, package, f"required questions without a known answer: {'; '.join(missing[:3])}")
    if not sess.auto_submit:
        return sess.hand_over(job, package, "review mode - check the form and press Submit yourself")

    submit = page.get_by_role("button", name=_SUBMIT).last
    if not submit.count():
        submit = page.locator('button[type="submit"], input[type="submit"], #btn-submit').last
    submit.click()
    state = _wait_confirmed(page)
    sess.snapshot(package, "after_submit")
    if state == "confirmed":
        return Outcome(SUBMITTED, f"submitted on {job.apply_type}")
    if state == "challenge":
        return sess.hand_over(job, package, "captcha challenge after submit")
    if state == "errors":
        return sess.hand_over(job, package, "the form reported errors after submit")
    return Outcome(SUBMITTED_UNCONFIRMED, "submit clicked; no confirmation text detected (see after_submit.png)")


# --------------------------------------------------------------------------- #
# LinkedIn Easy Apply
# --------------------------------------------------------------------------- #
_LI_LIMIT = re.compile(r"(?:reached|exceeded) (?:today.?s|the daily) (?:easy apply |application )?limit|"
                       r"daily (?:submissions|application limit)", re.I)


def _li_modal(page):
    for sel in ('div[role="dialog"].jobs-easy-apply-modal', '[data-test-modal].jobs-easy-apply-modal',
                '[data-testid="dialog-content"]', 'div[role="dialog"]'):
        loc = page.locator(sel).last
        if loc.count() and loc.is_visible():
            return loc
    return None


def _li_discard(page) -> None:
    try:
        modal = _li_modal(page)
        if modal is not None:
            modal.get_by_role("button", name=re.compile(r"dismiss", re.I)).first.click(timeout=4000)
            page.get_by_role("button", name=re.compile(r"discard", re.I)).first.click(timeout=4000)
    except Exception:  # noqa: BLE001 - best effort clean-up
        page.keyboard.press("Escape")


def apply_linkedin(sess: PortalSession, job: Job, package) -> Outcome:
    page = sess.open(job.url)
    if LOGIN_URL.search(page.url):
        return sess.hand_over(job, package, "LinkedIn wants you to sign in (run: python -m autopilot login linkedin)")
    body = page.locator("body").inner_text()
    if _LI_LIMIT.search(body):
        raise ChannelDown("LinkedIn Easy Apply daily limit reached")
    if page.locator(".jobs-s-apply__application-link").count() or re.search(r"\bApplied \d+ \w+ ago\b", body):
        return Outcome(ALREADY, "LinkedIn shows this job as applied")

    button = page.locator("button#jobs-apply-button-id, button.jobs-apply-button").filter(
        has_text=re.compile(r"easy apply", re.I)).first
    if not button.count():
        button = page.get_by_role("button", name=re.compile(r"easy apply", re.I)).first
    if not button.count():
        button = page.locator('a[href*="openSDUIApplyFlow=true"]').first
    if not button.count():
        return Outcome(MANUAL, "no Easy Apply button (external application)")
    button.click()
    _pause(1.5, 2.5)

    files = {"resume": package.resume_pdf, "cover": package.letter_pdf}
    sess.answers.cover_text = package.cover_note
    sess.answers.source_label = "LinkedIn"
    follow = bool(sess.settings.get("apply.linkedin.follow_company", False))
    for step in range(15):
        modal = _li_modal(page)
        if modal is None:
            if confirmed(page):
                return Outcome(SUBMITTED, "Easy Apply submitted")
            return sess.hand_over(job, package, "Easy Apply window closed unexpectedly")
        if _LI_LIMIT.search(modal.inner_text()):
            _li_discard(page)
            raise ChannelDown("LinkedIn Easy Apply daily limit reached")
        report = fill_form(page, sess.answers, job, files, root=modal, log=sess.log)
        follow_box = page.locator("#follow-company-checkbox")
        if follow_box.count():
            try:
                if follow_box.is_checked() != follow:
                    page.locator('label[for="follow-company-checkbox"]').click()
            except Exception:  # noqa: BLE001
                pass
        submit = modal.get_by_role("button", name=re.compile(r"submit application", re.I))
        review = modal.get_by_role("button", name=re.compile(r"^\s*review", re.I))
        nxt = modal.get_by_role("button", name=re.compile(r"continue to next step|^\s*next\s*$", re.I))
        if submit.count() and submit.first.is_visible():
            if not sess.auto_submit:
                return sess.hand_over(job, package, "review mode - check and press 'Submit application'")
            submit.first.click()
            _pause(2.0, 3.5)
            sess.snapshot(package, "after_submit")
            if re.search(r"application (?:was )?sent|application submitted|done", page.locator("body").inner_text(), re.I):
                try:
                    page.get_by_role("button", name=re.compile(r"^\s*(done|dismiss)", re.I)).first.click(timeout=3000)
                except Exception:  # noqa: BLE001
                    page.keyboard.press("Escape")
                return Outcome(SUBMITTED, "Easy Apply submitted")
            return Outcome(SUBMITTED_UNCONFIRMED, "Easy Apply submit clicked; no confirmation detected")
        target = review if (review.count() and review.first.is_visible()) else nxt
        if not target.count():
            return sess.hand_over(job, package, f"unexpected Easy Apply step {step + 1}")
        target.first.click()
        _pause(1.2, 2.2)
        errors = modal.locator(".artdeco-inline-feedback--error, [data-test-form-element-error-messages]")
        if errors.count() and errors.first.is_visible():
            # One more pass with what we know; if it is still stuck, a person has to look.
            fill_form(page, sess.answers, job, files, root=modal, log=sess.log)
            target.first.click()
            _pause(1.2, 2.0)
            if errors.count() and errors.first.is_visible():
                outcome = sess.hand_over(job, package, "Easy Apply questions need your answer: " +
                                         "; ".join(report.unknown_required[:3] or ["see the form"]))
                if outcome.status not in (SUBMITTED, SUBMITTED_UNCONFIRMED):
                    _li_discard(page)
                return outcome
    _li_discard(page)
    return Outcome(ATTENTION, "Easy Apply had too many steps")


# --------------------------------------------------------------------------- #
# Naukri
# --------------------------------------------------------------------------- #
_NK_DONE = re.compile(r"successfully applied|application submitted|you have applied|applied to", re.I)
_NK_QUOTA = re.compile(r"daily quota has been (?:expired|exhausted)|reached the daily limit", re.I)


def _naukri_chatbot(sess: PortalSession, page, job: Job, package) -> Outcome | None:
    """Answer the recruiter-question drawer. None = drawer finished/closed."""
    for _ in range(25):
        drawer = page.locator(".chatbot_Drawer, [class*='chatbot_Drawer'], [id*='ChatbotContainer']").first
        if not drawer.count() or not drawer.is_visible():
            return None
        bot = drawer.locator(".botMsg, li.botItem .msg, .chatbot_ListItem.botItem .msg")
        if not bot.count():
            time.sleep(1.0)
            continue
        question = bot.last.inner_text().strip()
        radios = drawer.locator(".ssrc__radio")
        chips = drawer.locator(".chatbot_Chip, .chipsContainer .chatbot_Chip")
        if radios.count():
            labels = drawer.locator("label.ssrc__label").all_inner_texts()
            q = Question(question, "radio", [t.strip() for t in labels], True)
        elif chips.count():
            q = Question(question, "radio", [t.strip() for t in chips.all_inner_texts()], True)
        else:
            q = Question(question, "text", [], True)
        answer = sess.answers.answer(q, job)
        if answer is None:
            return sess.hand_over(job, package, f"Naukri recruiter question: {question[:90]}")
        if radios.count():
            from autopilot.apply.answers import choose_option
            pick = choose_option(answer, q.options)
            if pick is None:
                return sess.hand_over(job, package, f"no option fits '{answer}' for: {question[:80]}")
            drawer.locator("label.ssrc__label").nth(q.options.index(pick)).click()
        elif chips.count():
            from autopilot.apply.answers import choose_option
            pick = choose_option(answer, q.options)
            if pick is None:
                return sess.hand_over(job, package, f"no option fits '{answer}' for: {question[:80]}")
            chips.nth(q.options.index(pick)).click()
        else:
            box = drawer.locator("div.textArea[contenteditable='true'], [contenteditable='true']").last
            box.click()
            box.type(answer, delay=30)
        _pause(0.6, 1.2)
        send = drawer.locator(".sendMsg, .sendMsgbtn_container .sendMsg").last
        if send.count():
            send.click()
        _pause(1.5, 2.5)
        if _NK_DONE.search(page.locator("body").inner_text()):
            return Outcome(SUBMITTED, "applied on Naukri")
    return sess.hand_over(job, package, "Naukri questionnaire did not finish")


def apply_naukri(sess: PortalSession, job: Job, package) -> Outcome:
    page = sess.open(job.url)
    if LOGIN_URL.search(page.url):
        return sess.hand_over(job, package, "Naukri wants you to sign in (run: python -m autopilot login naukri)")
    body = page.locator("body").inner_text()
    if _NK_QUOTA.search(body):
        raise ChannelDown("Naukri daily apply quota reached")
    if page.locator("#company-site-button").count():
        return Outcome(MANUAL, "Naukri sends this job to the company's site")
    button = page.locator("#apply-button").first
    if not button.count():
        return Outcome(MANUAL, "no Naukri apply button found")
    if "applied" in button.inner_text().lower():
        return Outcome(ALREADY, "Naukri shows this job as applied")
    if not sess.auto_submit:
        return sess.hand_over(job, package, "review mode - press Apply yourself")
    sess.answers.source_label = "Naukri"
    button.click()
    _pause(2.0, 3.0)
    if re.search(r"saveApply", page.url) or _NK_DONE.search(page.locator("body").inner_text()):
        return Outcome(SUBMITTED, "applied on Naukri")
    if _NK_QUOTA.search(page.locator("body").inner_text()):
        raise ChannelDown("Naukri daily apply quota reached")
    outcome = _naukri_chatbot(sess, page, job, package)
    if outcome is not None:
        return outcome
    _pause(1.5, 2.5)
    if re.search(r"saveApply", page.url) or _NK_DONE.search(page.locator("body").inner_text()):
        return Outcome(SUBMITTED, "applied on Naukri")
    sess.snapshot(package, "after_apply")
    return Outcome(SUBMITTED_UNCONFIRMED, "apply clicked; no confirmation detected (see after_apply.png)")


# --------------------------------------------------------------------------- #
# One-time login
# --------------------------------------------------------------------------- #
LOGIN_PAGES = {"linkedin": "https://www.linkedin.com/login", "naukri": "https://www.naukri.com/nlogin/login"}


def login(settings: Settings, site: str) -> bool:
    """Open the site in the bot's browser profile so you can sign in by hand."""
    ctx = browser.persistent_context(settings.path("browser.profile_dir", "data/browser-profile"),
                                     channel=settings.get("browser.channel", "chrome"), headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_PAGES[site], wait_until="domcontentloaded")
    print(f"Sign in to {site} in the browser window (password, OTP, captcha - all by you).")
    input("Press Enter here once you are signed in... ")
    ok = not LOGIN_URL.search(page.url)
    browser.close_persistent()
    return ok
