from email.message import EmailMessage

from app import alert_email


def test_alert_email_is_disabled_without_smtp(monkeypatch):
    monkeypatch.setattr(alert_email.settings, "SECURITY_ALERT_EMAIL_ENABLED", False)
    assert alert_email.email_delivery_configured() is False
    assert alert_email.queue_security_alert({"event_type": "test", "outcome": "failure"}) is False


def test_alert_message_contains_only_operational_context(monkeypatch):
    monkeypatch.setattr(alert_email.settings, "SECURITY_ALERT_EMAIL_TO", "marcnester@gmail.com")
    monkeypatch.setattr(alert_email.settings, "SMTP_FROM_EMAIL", "alerts@glycofy.ai")
    message = alert_email.build_alert_message(
        {
            "event_type": "oauth_strava_callback",
            "outcome": "invalid_state",
            "request_id": "request-123",
            "user_id": None,
        }
    )
    assert isinstance(message, EmailMessage)
    assert message["To"] == "marcnester@gmail.com"
    body = message.get_content()
    assert "oauth_strava_callback" in body
    assert "request-123" in body
    assert "password" not in body.lower()
    assert "token" not in body.lower()
