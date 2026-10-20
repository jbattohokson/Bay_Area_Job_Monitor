"""Compiles the digest into HTML/plain-text and sends it via Gmail SMTP.

Plain functions, not a CrewAI tool — main.py calls these directly after
crew.kickoff() returns, on the final structured DigestResult.
"""
from __future__ import annotations

import html
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _escape(text) -> str:
    """Every scraped/generated string is untrusted third-party text getting
    interpolated into an HTML body — with no templating engine auto-escaping
    for us, every interpolation point must go through this."""
    return html.escape(str(text) if text is not None else "")


def _job_link_html(job) -> str:
    link = _escape(job.link)
    marker = "" if job.link_verified else ' <span style="color:#b00;">⚠️ verify before applying</span>'
    return f'<a href="{link}">{link}</a>{marker}'


def _pay_range_html(job) -> str:
    pay = _escape(job.pay_range or "Not available")
    if job.pay_range_is_estimate:
        return f'<span style="color:#666;font-style:italic;">{pay} (estimated)</span>'
    return pay


def _job_row_html(job) -> str:
    return f"""
    <tr>
      <td style="padding:6px 10px;border-bottom:1px solid #eee;">
        <strong>{_escape(job.title)}</strong><br>
        <span style="color:#555;">{_escape(job.location)} &middot; {_escape(job.work_mode)}</span>
      </td>
      <td style="padding:6px 10px;border-bottom:1px solid #eee;">{_escape(job.years_experience)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #eee;">{_pay_range_html(job)}</td>
      <td style="padding:6px 10px;border-bottom:1px solid #eee;">{_job_link_html(job)}</td>
    </tr>"""


def _company_group_html(group) -> str:
    rows = "\n".join(_job_row_html(job) for job in group.jobs)
    return f"""
    <h3 style="margin-bottom:4px;">{_escape(group.company)} ({len(group.jobs)})</h3>
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px;">
      <tr style="text-align:left;color:#888;font-size:12px;">
        <th style="padding:4px 10px;">Role</th>
        <th style="padding:4px 10px;">Experience</th>
        <th style="padding:4px 10px;">Pay range</th>
        <th style="padding:4px 10px;">Link</th>
      </tr>
      {rows}
    </table>"""


def _section_html(title: str, groups) -> str:
    if not groups:
        return f'<h2>{_escape(title)}</h2><p style="color:#888;">None this run.</p>'
    body = "\n".join(_company_group_html(g) for g in groups)
    return f"<h2>{_escape(title)}</h2>\n{body}"


def render_html_digest(digest) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:720px;margin:0 auto;">
  <h1 style="font-size:20px;">Bay Area Job Digest</h1>
  <p>{_escape(digest.summary)}</p>
  <p style="color:#555;">{digest.total_new} new since last run &middot; {digest.total_open} total open roles matched.</p>
  {_section_html("New since last run", digest.new_jobs_by_company)}
  {_section_html("All current openings", digest.all_jobs_by_company)}
  <p style="color:#aaa;font-size:12px;margin-top:24px;">
    Generated locally by bay-area-job-monitor. Pay ranges marked "(estimated)" are
    LLM-generated guesses, not employer-stated figures. Links marked
    "verify before applying" failed an automated reachability check — this is
    often a false positive from bot detection, not necessarily a dead link.
  </p>
</body>
</html>"""


def _job_row_text(job) -> str:
    marker = " [VERIFY LINK]" if not job.link_verified else ""
    pay = f"{job.pay_range or 'Not available'}" + (" (estimated)" if job.pay_range_is_estimate else "")
    return (
        f"  - {job.title} | {job.location} | {job.work_mode} | {job.years_experience} | "
        f"{pay}\n    {job.link}{marker}"
    )


def _section_text(title: str, groups) -> str:
    if not groups:
        return f"{title}\n{'-' * len(title)}\nNone this run.\n"
    lines = [f"{title}\n{'-' * len(title)}"]
    for group in groups:
        lines.append(f"\n{group.company} ({len(group.jobs)})")
        lines.extend(_job_row_text(job) for job in group.jobs)
    return "\n".join(lines) + "\n"


def render_text_digest(digest) -> str:
    parts = [
        "BAY AREA JOB DIGEST",
        digest.summary,
        f"{digest.total_new} new since last run / {digest.total_open} total open roles matched.\n",
        _section_text("New since last run", digest.new_jobs_by_company),
        _section_text("All current openings", digest.all_jobs_by_company),
    ]
    return "\n".join(parts)


def send_digest_email(digest, sender: str, app_password: str, recipient: str) -> None:
    """Sends via Gmail SMTP (STARTTLS on port 587) using an App Password.
    Deliberately does NOT catch its own exceptions — a failed send is exactly
    the failure main.py's error handling exists to surface, unlike the
    scraper's per-company handlers, which must never raise."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Bay Area Job Digest — {digest.total_new} new, {digest.total_open} total open roles"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(render_text_digest(digest), "plain"))
    msg.attach(MIMEText(render_html_digest(digest), "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())
