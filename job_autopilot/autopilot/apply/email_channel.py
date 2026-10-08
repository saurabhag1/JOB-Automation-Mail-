"""Send the tailored resume + cover note to the recruiter's inbox over SMTP.

Uses the same EMAIL_ADDRESS / EMAIL_PASSWORD (Gmail App Password) as the
repo's existing mailers. The message is multipart (plain text + HTML) with the
PDF attached as application/pdf, and a copy is always saved as email.eml in
the job's package folder - in a dry run that copy is all that happens.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from autopilot.apply import DRY_RUN, FAILED, SUBMITTED, ChannelDown, Outcome
from autopilot.config import Profile, env
from autopilot.models import Job


class Mailer:
    def __init__(self, profile: Profile, dry_run: bool, log=print):
        self.profile, self.dry_run, self.log = profile, dry_run, log
        self.user = env("EMAIL_ADDRESS")
        self.password = env("EMAIL_PASSWORD")
        self.host = env("SMTP_HOST", "smtp.gmail.com")
        self.port = int(env("SMTP_PORT", "587"))
        self.starttls = env("SMTP_STARTTLS", "1") != "0"
        self._smtp: smtplib.SMTP | None = None

    def problem(self) -> str:
        if self.dry_run:
            return ""
        if not self.user or not self.password:
            return "EMAIL_ADDRESS / EMAIL_PASSWORD are not set in job_autopilot/.env"
        return ""

    def build(self, job: Job, package) -> EmailMessage:
        sender = self.user or self.profile.email or "me@example.invalid"
        msg = EmailMessage()
        msg["From"] = formataddr((self.profile.name or "", sender))
        msg["To"] = job.hr_email
        msg["Subject"] = package.subject
        if self.profile.email and self.profile.email.lower() != sender.lower():
            msg["Reply-To"] = self.profile.email
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
        msg.set_content(package.email_text)
        msg.add_alternative(package.email_html, subtype="html")
        msg.add_attachment(package.resume_pdf.read_bytes(), maintype="application", subtype="pdf",
                           filename=package.resume_pdf.name)
        return msg

    def _connect(self) -> smtplib.SMTP:
        if self._smtp is None:
            smtp = smtplib.SMTP(self.host, self.port, timeout=30)
            smtp.ehlo()
            if self.starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            try:
                smtp.login(self.user, self.password)
            except smtplib.SMTPAuthenticationError as exc:
                smtp.close()
                raise ChannelDown(f"SMTP login failed for {self.user} - check the App Password ({exc.smtp_code})") from exc
            self._smtp = smtp
            self.log(f"  email: logged in to {self.host} as {self.user}")
        return self._smtp

    def send(self, job: Job, package) -> Outcome:
        msg = self.build(job, package)
        (package.folder / "email.eml").write_bytes(bytes(msg))
        if self.dry_run:
            return Outcome(DRY_RUN, f"would email {job.hr_email} (saved email.eml)", recipient=job.hr_email)
        for attempt in (1, 2):
            try:
                self._connect().send_message(msg, to_addrs=[job.hr_email])
                return Outcome(SUBMITTED, f"emailed {job.hr_email}", recipient=job.hr_email)
            except smtplib.SMTPRecipientsRefused as exc:
                return Outcome(FAILED, f"recipient refused: {exc.recipients}", recipient=job.hr_email)
            except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError) as exc:
                self._smtp = None              # reconnect once, then give up on this job
                if attempt == 2:
                    return Outcome(FAILED, f"SMTP connection lost: {exc}", recipient=job.hr_email)
            except smtplib.SMTPDataError as exc:
                if exc.smtp_code in (421, 450, 451, 452, 550) and b"limit" in (exc.smtp_error or b"").lower():
                    raise ChannelDown(f"provider sending limit reached: {exc.smtp_error!r}") from exc
                return Outcome(FAILED, f"SMTP error {exc.smtp_code}: {exc.smtp_error!r}", recipient=job.hr_email)
        return Outcome(FAILED, "unreachable", recipient=job.hr_email)

    def close(self) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except Exception:  # noqa: BLE001 - quitting a dead connection
                pass
            self._smtp = None
