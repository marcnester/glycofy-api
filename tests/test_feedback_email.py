from email.message import EmailMessage

from app import feedback_email


def test_feedback_email_contains_only_safe_operational_context(monkeypatch):
    monkeypatch.setattr(feedback_email.settings, "ADMIN_EMAILS", "owner@example.com, backup@example.com")
    monkeypatch.setattr(feedback_email.settings, "SMTP_FROM_EMAIL", "noreply@glycofy.ai")
    monkeypatch.setattr(feedback_email.settings, "PUBLIC_BASE_URL", "https://app.glycofy.ai")
    message = feedback_email.build_feedback_message(
        {
            "category": "issue",
            "rating": 2,
            "page_path": "/ui/plan.html",
            "request_id": "request-123",
            "message": "Private written feedback",
            "user_email": "user@example.com",
        }
    )

    assert isinstance(message, EmailMessage)
    assert message["To"] == "owner@example.com, backup@example.com"
    body = message.get_content()
    assert "Category: issue" in body
    assert "Rating: 2" in body
    assert "/ui/plan.html" in body
    assert "request-123" in body
    assert "https://app.glycofy.ai/ui/operations.html" in body
    assert "Private written feedback" not in body
    assert "user@example.com" not in body


def test_feedback_email_is_disabled_without_configuration(monkeypatch):
    monkeypatch.setattr(feedback_email.settings, "BETA_FEEDBACK_EMAIL_ENABLED", False)
    assert feedback_email.email_delivery_configured() is False
    assert feedback_email.queue_feedback_notification({"category": "idea"}) is False


def test_feedback_email_notifications_are_rate_limited(monkeypatch):
    queued = []
    monkeypatch.setattr(feedback_email, "email_delivery_configured", lambda: True)
    monkeypatch.setattr(feedback_email, "_ensure_worker", lambda: None)
    monkeypatch.setattr(feedback_email, "_LAST_SENT", 0.0)
    monkeypatch.setattr(feedback_email.settings, "BETA_FEEDBACK_EMAIL_COOLDOWN_SECONDS", 300)
    monkeypatch.setattr(feedback_email.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(feedback_email._QUEUE, "put_nowait", queued.append)

    event = {"category": "idea"}
    assert feedback_email.queue_feedback_notification(event) is True
    assert feedback_email.queue_feedback_notification(event) is False
    assert queued == [event]
