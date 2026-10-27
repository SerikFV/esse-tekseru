"""SMTP adapter. Neither codes, message contents nor SMTP exceptions are logged."""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from fastapi import HTTPException


class SMTPMailer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        s = self.settings
        return bool(s.smtp_host and s.smtp_username and s.smtp_password.get_secret_value() and s.smtp_from_email)

    def send_code(self, email, code, purpose):
        if not self.configured:
            raise HTTPException(503, "Пошта жіберу бапталмаған. Сервердегі SMTP параметрлерін тексеріңіз.")
        labels = {"register": "Тіркелуді растау", "reset": "Құпиясөзді қалпына келтіру", "email": "Жаңа поштаны растау"}
        message = EmailMessage()
        message["Subject"] = f"tekSER — {labels[purpose]}"
        message["From"] = formataddr((self.settings.smtp_from_name, self.settings.smtp_from_email))
        message["To"] = email
        message.set_content(f"{labels[purpose]} коды: {code}\n\nКод 10 минут жарамды. Кодты ешкімге бермеңіз.\nБұл әрекетті сіз бастамаған болсаңыз, хатты елемеңіз.\n\ntekSER")
        try:
            context = ssl.create_default_context()
            cls = smtplib.SMTP_SSL if self.settings.smtp_ssl else smtplib.SMTP
            options = {"context": context} if self.settings.smtp_ssl else {}
            with cls(self.settings.smtp_host, self.settings.smtp_port, timeout=15, **options) as client:
                if not self.settings.smtp_ssl:
                    client.starttls(context=context)
                client.login(self.settings.smtp_username, self.settings.smtp_password.get_secret_value())
                client.send_message(message)
        except (smtplib.SMTPException, OSError, ValueError):
            raise HTTPException(503, "Растау хатын жіберу мүмкін болмады. Пошта баптауын тексеріп, кейінірек қайталаңыз.") from None
