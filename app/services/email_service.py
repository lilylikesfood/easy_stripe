import os
import logging
import requests

logger =logging.getLogger(__name__)

POSTMARK_SERVER_TOKEN = os.environ.get("POSTMARK_SERVER_TOKEN")
EMAIL_FROM_ADDRESS = os.environ.get("EMAIL_FROM_ADDRESS")
EMAIL_SENDING_ENABLED = os.environ.get("EMAIL_SENDING_ENABLED", "false").lower() == "true"
# environment variables are always text (strings), no matter what. 
# Even if you write EMAIL_SENDING_ENABLED=true in your .env file, Python does NOT receive an actual True value — it receives the word "true"
# For the if statement to work correctly, EMAIL_SENDING_ENABLED needs to actually be True or False (real Python booleans) — not the text "true" or "false"
# So the comparison isn't "checking something we already know" — it's a conversion step. 
# It's the one and only line where we transform "the word false" (text) into "the actual value False" (a real boolean)
# "false" == "true" → this is where the real conversion happens. 
# Python compares the two strings and produces a genuine boolean. 
# Since "false" does not equal "true", this evaluates to the real value False — not the word "false", but the actual False object.

def send_email(to, subject, html_body, tag=None):
    if not EMAIL_SENDING_ENABLED:
        logger.info(f"would have sent {subject} to {to}")

        return {
            "success": False, 
            "message_id": None, 
            "error": "disabled"
        }

    payload ={
        "From": EMAIL_FROM_ADDRESS,
        "To": to,
        "Subject": subject,
        "HtmlBody": html_body,
        "MessageStream": "outbound",
    }

    if tag:
        payload["Tag"] = tag

    response = requests.post(
        "https://api.postmarkapp.com/email", 
        headers={
            # "I'm sending you data in JSON format"
            "Content-Type": "application/json",
            # "I'd like your response back in JSON format too"
            "Accept": "application/json",
            # "Here's my secret token so you know it's really me"
            "X-Postmark-Server-Token": POSTMARK_SERVER_TOKEN,
        }, 
        json=payload,
    )

    if response.status_code == 200:
        return {
            "success": True,
            "message_id": response.json()["MessageID"],
            "error": None,
        }

    else: 
        return {
            "success": False,
            "message_id": None,
            "error": response.json()["Message"],
        }