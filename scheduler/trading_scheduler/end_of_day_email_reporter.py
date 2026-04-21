"""End-of-day portfolio P/L email reporting."""

from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import date, datetime
from email.message import EmailMessage

from common.models.pnl_summary import PnlSummary
from common.settings import EndOfDayReportConfig
from publishers.abstracts import IBroker

logger: logging.Logger = logging.getLogger(__name__)


class EndOfDayEmailReporter:
    """Sends a daily portfolio P/L email after market close."""

    def __init__(self, broker: IBroker, config: EndOfDayReportConfig) -> None:
        self._broker = broker
        self._config = config
        self._last_sent_trading_day: date | None = None

    async def send_report_for_trading_day(self, trading_day: datetime) -> None:
        """Send one report per trading day."""
        if not self._config.enabled:
            return

        day = trading_day.date()
        if self._last_sent_trading_day == day:
            logger.debug("EOD report already sent for %s", day)
            return

        if not self._has_required_smtp_config():
            logger.warning(
                "EOD report is enabled but SMTP settings are incomplete. "
                "Set EOD_REPORT_SENDER_EMAIL, EOD_REPORT_SMTP_USERNAME, and EOD_REPORT_SMTP_PASSWORD."
            )
            return

        since_date = self._resolve_since_date(day)
        try:
            summary = await self._broker.get_pnl_summary(since_date=since_date)
        except Exception as exc:
            logger.error("Failed to fetch P/L summary for EOD report: %s", exc, exc_info=True)
            return

        subject, body = self._build_email(summary)

        try:
            await asyncio.to_thread(self._send_email_sync, subject, body)
            self._last_sent_trading_day = day
            logger.info(
                "✅ End-of-day portfolio report email sent to %s for %s",
                self._config.recipient_email,
                day.isoformat(),
            )
        except Exception as exc:
            logger.error("Failed to send EOD report email: %s", exc, exc_info=True)

    def _send_email_sync(self, subject: str, body: str) -> None:
        """Send a plaintext email via SMTP."""
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._config.sender_email
        message["To"] = self._config.recipient_email
        message.set_content(body)

        with smtplib.SMTP(self._config.smtp_host, self._config.smtp_port, timeout=30) as smtp:
            if self._config.use_tls:
                smtp.starttls()
            smtp.login(self._config.smtp_username, self._config.smtp_password)
            smtp.send_message(message)

    def _resolve_since_date(self, trading_day: date) -> date:
        """Use the most recent configured month/day that is <= trading_day."""
        candidate = date(trading_day.year, self._config.since_month, self._config.since_day)
        if candidate > trading_day:
            return date(trading_day.year - 1, self._config.since_month, self._config.since_day)
        return candidate

    def _build_email(self, summary: PnlSummary) -> tuple[str, str]:
        """Build subject/body for the report."""
        subject = f"FinanceMaker EOD P/L - {summary.as_of_date.isoformat()}"

        lines = [
            "End-of-day portfolio report (Interactive Brokers)",
            f"As of: {summary.as_of_date.isoformat()}",
            "",
            f"Today's P/L: {self._fmt_money(summary.daily_pnl, summary.currency)}",
            (
                f"P/L since {summary.since_date.isoformat()}: "
                f"{self._fmt_money(summary.pnl_since_date, summary.currency)}"
            ),
        ]

        if summary.baseline_date is not None and summary.baseline_nav is not None:
            lines.append(
                "Baseline NAV "
                f"({summary.baseline_date.isoformat()}): "
                f"{self._fmt_abs_money(summary.baseline_nav, summary.currency)}"
            )
        if summary.current_nav is not None:
            lines.append(
                f"Current NAV: {self._fmt_abs_money(summary.current_nav, summary.currency)}"
            )
        if summary.pnl_since_date is None:
            lines.append(
                "Note: Could not compute cumulative P/L for the requested baseline date from IBKR performance data."
            )

        return subject, "\n".join(lines)

    def _has_required_smtp_config(self) -> bool:
        return all(
            [
                bool(self._config.sender_email),
                bool(self._config.smtp_username),
                bool(self._config.smtp_password),
                bool(self._config.recipient_email),
                bool(self._config.smtp_host),
                self._config.smtp_port > 0,
            ]
        )

    @staticmethod
    def _fmt_money(value: float | None, currency: str) -> str:
        if value is None:
            return "N/A"
        sign = "+" if value >= 0 else "-"
        return f"{sign}{currency} {abs(value):,.2f}"

    @staticmethod
    def _fmt_abs_money(value: float, currency: str) -> str:
        return f"{currency} {value:,.2f}"
