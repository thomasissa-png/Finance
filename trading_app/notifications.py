"""
Notifications WhatsApp via Twilio.
"""
import os

from .config import client_twilio, logger

def envoyer_whatsapp(message):
    """Envoie un message WhatsApp"""
    if not client_twilio:
        print("⚠️ Twilio non configuré")
        return False

    twilio_from = os.environ.get("TWILIO_WHATSAPP_FROM")
    twilio_to = os.environ.get("TWILIO_WHATSAPP_TO")

    if not twilio_from or not twilio_to:
        print("⚠️ TWILIO_WHATSAPP_FROM ou TWILIO_WHATSAPP_TO non configuré")
        return False

    try:
        client_twilio.messages.create(
            from_=twilio_from,
            body=message[:1500],
            to=twilio_to
        )
        return True
    except Exception as e:
        logger.error(f"Erreur WhatsApp: {e}")
        return False

