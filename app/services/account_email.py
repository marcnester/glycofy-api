from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from app.config import settings

logger = logging.getLogger("glycofy.account_email")


def account_email_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL)


def build_account_email_html(
    *,
    preheader: str,
    heading: str,
    message: str,
    action_label: str,
    action_url: str,
    expires: str,
    security_note: str,
) -> str:
    """Build conservative, inbox-friendly HTML with no remote image dependency."""
    safe = {
        "preheader": escape(preheader),
        "heading": escape(heading),
        "message": escape(message),
        "action_label": escape(action_label),
        "action_url": escape(action_url, quote=True),
        "expires": escape(expires),
        "security_note": escape(security_note),
    }
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>{safe['heading']}</title>
  </head>
  <body style="margin:0;background:#07111d;color:#e8eef7;font-family:Arial,Helvetica,sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
      {safe['preheader']}
    </div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#07111d;">
      <tr>
        <td align="center" style="padding:40px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;">
            <tr>
              <td style="padding:0 4px 24px;color:#54d39a;font-size:28px;font-weight:800;letter-spacing:-0.5px;">
                <span style="display:inline-block;width:34px;height:34px;line-height:34px;text-align:center;border-radius:50%;background:#54d39a;color:#07111d;font-size:20px;margin-right:9px;vertical-align:2px;">g</span>glycofy
              </td>
            </tr>
            <tr>
              <td style="background:#101b2b;border:1px solid #24344a;border-radius:20px;padding:40px 36px;box-shadow:0 14px 40px rgba(0,0,0,.24);">
                <div style="color:#54d39a;font-size:12px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;margin-bottom:14px;">Athlete nutrition, personalized</div>
                <h1 style="margin:0 0 16px;color:#f5f8fc;font-size:30px;line-height:1.2;letter-spacing:-0.6px;">{safe['heading']}</h1>
                <p style="margin:0 0 28px;color:#b8c5d8;font-size:17px;line-height:1.65;">{safe['message']}</p>
                <table role="presentation" cellspacing="0" cellpadding="0" border="0">
                  <tr>
                    <td bgcolor="#54d39a" style="border-radius:10px;">
                      <a href="{safe['action_url']}" style="display:inline-block;padding:15px 24px;color:#07111d;font-size:16px;font-weight:700;text-decoration:none;border-radius:10px;">{safe['action_label']}</a>
                    </td>
                  </tr>
                </table>
                <p style="margin:24px 0 0;color:#8fa2ba;font-size:14px;line-height:1.55;">{safe['expires']}</p>
                <div style="height:1px;background:#24344a;margin:28px 0 22px;"></div>
                <p style="margin:0;color:#8fa2ba;font-size:13px;line-height:1.55;">{safe['security_note']}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:22px 8px 0;color:#6f829a;font-size:12px;line-height:1.6;text-align:center;">
                Built for training days, recovery days, and everything between.<br>
                Need help? <a href="mailto:support@glycofy.ai" style="color:#54d39a;text-decoration:none;">support@glycofy.ai</a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def send_account_email(to: str, subject: str, body: str, html_body: str | None = None) -> bool:
    if not account_email_configured():
        return False
    message = EmailMessage()
    message["To"] = to
    message["From"] = settings.SMTP_FROM_EMAIL
    message["Subject"] = subject
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        if settings.SMTP_USE_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, context=ssl.create_default_context()
            ) as smtp:
                if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(message)
        return True
    except Exception:
        logger.exception("account_email_delivery_failed")
        return False
