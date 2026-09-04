from email.message import EmailMessage

from app.services import account_email


class _SMTP:
    sent: EmailMessage | None = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def login(self, *args):
        return None

    def send_message(self, message: EmailMessage):
        self.__class__.sent = message


def test_account_email_includes_html_and_plain_text(monkeypatch):
    monkeypatch.setattr(account_email.settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(account_email.settings, "SMTP_PORT", 465)
    monkeypatch.setattr(account_email.settings, "SMTP_FROM_EMAIL", "noreply@glycofy.ai")
    monkeypatch.setattr(account_email.settings, "SMTP_USE_TLS", False)
    monkeypatch.setattr(account_email.smtplib, "SMTP_SSL", _SMTP)

    html = account_email.build_account_email_html(
        preheader="Secure account action",
        heading="Reset your password",
        message="Use the button below.",
        action_label="Reset password",
        action_url="https://app.glycofy.ai/reset?token=abc&mode=reset",
        expires="Expires in one hour.",
        security_note="Ignore this if you did not request it.",
    )
    assert account_email.send_account_email("athlete@example.com", "Reset", "Plain fallback", html)

    message = _SMTP.sent
    assert message is not None
    assert message.get_content_type() == "multipart/alternative"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "Plain fallback"
    rendered = message.get_body(preferencelist=("html",)).get_content()
    assert "Reset password" in rendered
    assert message["From"] == "noreply@glycofy.ai"
    assert "token=abc&amp;mode=reset" in rendered
    assert "support@glycofy.ai" in rendered
