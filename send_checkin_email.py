"""Send a one-off check-in email using configured SMTP credentials."""

from __future__ import annotations

import argparse
import smtplib
from email.message import EmailMessage

from common.settings import settings


DEFAULT_RECIPIENT = "betterlokaki@gmail.com"
DEFAULT_SUBJECT = "Quick check-in"
DEFAULT_MESSAGE = "Hi, it's me just checking if you're good."


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a one-off check-in email.")
    parser.add_argument(
        "--to",
        default=DEFAULT_RECIPIENT,
        help=f"Recipient email address (default: {DEFAULT_RECIPIENT})",
    )
    parser.add_argument(
        "--subject",
        default=DEFAULT_SUBJECT,
        help=f"Email subject (default: {DEFAULT_SUBJECT!r})",
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Email body message.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    cfg = settings.eod_report
    sender = cfg.sender_email or cfg.smtp_username
    smtp_host = cfg.smtp_host
    smtp_port = int(cfg.smtp_port)
    smtp_username = cfg.smtp_username
    smtp_password = cfg.smtp_password

    missing = []
    if not sender:
        missing.append("EOD_REPORT_SENDER_EMAIL (or EOD_REPORT_SMTP_USERNAME)")
    if not smtp_host:
        missing.append("EOD_REPORT_SMTP_HOST")
    if not smtp_port:
        missing.append("EOD_REPORT_SMTP_PORT")
    if not smtp_username:
        missing.append("EOD_REPORT_SMTP_USERNAME")
    if not smtp_password:
        missing.append("EOD_REPORT_SMTP_PASSWORD")

    if missing:
        raise ValueError(
            "Missing required SMTP configuration: "
            + ", ".join(missing)
        )

    message = EmailMessage()
    message["Subject"] = args.subject
    message["From"] = sender
    message["To"] = args.to
    message.set_content(args.message)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        if cfg.use_tls:
            smtp.starttls()
        smtp.login(smtp_username, smtp_password)
        smtp.send_message(message)

    print(f"Email sent to {args.to}.")


if __name__ == "__main__":
    main()
