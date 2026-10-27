"""Local private email sink; never expose mail files through an HTTP route."""
from email.message import EmailMessage
import logging
from uuid import uuid4

from .config import auth_settings


def send_action_mail(email: str, purpose: str, token: str) -> bool:
    settings = auth_settings()
    route = {"VERIFY_EMAIL": "verify-email", "RESET_PASSWORD": "reset-password", "WORKSPACE_INVITE": "accept-invite", 'ACTIVATE_ACCOUNT':'activate-account', 'OWNERSHIP_APPROVE':'ownership-transfer', 'OWNERSHIP_ACCEPT':'ownership-transfer'}[purpose]
    message = EmailMessage()
    message["To"] = email
    message["From"] = "ALIGNED local development <noreply@aligned.local>"
    message["Subject"] = {"VERIFY_EMAIL": "Verify your ALIGNED email", "RESET_PASSWORD": "Reset your ALIGNED password", "WORKSPACE_INVITE": "Join your ALIGNED firm", 'ACTIVATE_ACCOUNT':'Activate your ALIGNED account', 'OWNERSHIP_APPROVE':'Confirm your ALIGNED ownership transfer', 'OWNERSHIP_ACCEPT':'Accept ALIGNED workspace ownership'}[purpose]
    body = f"Local development email; not sent over the internet.\n\nOpen this complete link in your local browser:\n{settings.frontend_url}/#/{route}?token={token}\n\nIgnore this message if you did not request it.\n"
    message.set_content(body, cte="8bit")
    try:
        settings.mail_dir.mkdir(parents=True, exist_ok=True)
        identifier = uuid4()
        # A local mailbox is usually read in a text editor, not a MIME decoder.
        # Keep a canonical human-readable copy so email encoding cannot alter links.
        with (settings.mail_dir / f"{identifier}.txt").open("x", encoding="utf-8") as output:
            output.write(f"To: {email}\nSubject: {message['Subject']}\n\n{body}")
        with (settings.mail_dir / f"{identifier}.eml").open("x", encoding="utf-8") as output:
            output.write(message.as_string())
        return True
    except OSError:
        logging.getLogger(__name__).error("Local authentication mail could not be fully written; use resend after fixing mail configuration")
        return False
