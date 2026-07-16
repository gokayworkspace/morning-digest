#!/usr/bin/env python3
"""
emailer.py — send today's digest by email.  Run:  python3 emailer.py

Reads out/digest-YYYY-MM-DD.md (today's), converts it to simple HTML,
and sends it via SMTP. All credentials come from environment variables,
so the same script works locally and inside GitHub Actions (where the
variables are populated from repo secrets):

    SMTP_HOST      e.g. smtp.gmail.com
    SMTP_PORT      e.g. 587
    SMTP_USER      your full Gmail address
    SMTP_PASSWORD  a Gmail *App Password* (NOT your real password —
                   Google account → Security → 2-Step Verification →
                   App passwords)
    DIGEST_TO      recipient (usually the same address)

Design rule carried over from the rest of the project: this module does
ONE thing. It doesn't fetch or build anything — if today's file doesn't
exist, it fails loudly, because silently sending nothing is worse.
"""

from __future__ import annotations

import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import markdown

HERE = Path(__file__).parent


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"[email] missing environment variable: {name}")
    return value


def send_digest() -> None:
    today = datetime.now()
    digest_path = HERE / "out" / f"digest-{today:%Y-%m-%d}.md"
    if not digest_path.exists():
        sys.exit(f"[email] no digest found at {digest_path} — run digest.py first")

    md_text = digest_path.read_text()

    host = _require_env("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = _require_env("SMTP_USER")
    password = _require_env("SMTP_PASSWORD")
    to_addr = _require_env("DIGEST_TO")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"☕ Morning Digest — {today:%a %d %b}"
    msg["From"] = user
    msg["To"] = to_addr

    # Plain-text part (fallback for old clients), then HTML part.
    # Email clients render the LAST alternative they support, so HTML
    # goes second.
    msg.attach(MIMEText(md_text, "plain", "utf-8"))
    # Headlines sometimes contain raw <angle brackets> or &, which
    # python-markdown would pass through as live HTML. Escape them
    # first; our renderer only uses [](), #, -, _ and --- syntax,
    # none of which is affected.
    escaped = (md_text.replace("&", "&amp;")
                      .replace("<", "&lt;")
                      .replace(">", "&gt;"))
    html_body = markdown.markdown(escaped)
    html = f"""\
<html><body style="font-family: -apple-system, Segoe UI, sans-serif;
                   max-width: 680px; margin: 0 auto; line-height: 1.5;">
{html_body}
</body></html>"""
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()          # upgrade the connection to TLS
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())

    print(f"[email] sent to {to_addr}")


if __name__ == "__main__":
    send_digest()
