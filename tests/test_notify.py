import unittest
from unittest.mock import MagicMock, patch

from submission_monitor.notify import send_email


class NotificationTests(unittest.TestCase):
    def settings(self):
        return {"smtp_host": "smtp.example.test", "smtp_port": 465,
                "smtp_user": "sender@example.test", "smtp_auth_code": "synthetic-secret",
                "to": ["recipient@example.test"], "smtp_security": "ssl"}

    def test_ssl_delivery_builds_utf8_headers_and_stable_message_id(self):
        with patch("submission_monitor.notify.smtplib.SMTP_SSL") as constructor:
            session = constructor.return_value.__enter__.return_value
            session.send_message.return_value = {}
            self.assertTrue(send_email(self.settings(), "合成状态更新", "合成正文", "abc123"))
            session.login.assert_called_once_with("sender@example.test", "synthetic-secret")
            message = session.send_message.call_args.args[0]
            self.assertEqual(str(message["Subject"]), "合成状态更新")
            self.assertEqual(message["Message-ID"], "<abc123@sn-submission-monitor.local>")
            self.assertIn("合成正文", message.get_content())
            self.assertIsNotNone(constructor.call_args.kwargs["context"])
            session.starttls.assert_not_called()

    def test_starttls_happens_before_password_authentication(self):
        settings = self.settings()
        settings.update(smtp_security="starttls", smtp_port=587)
        with patch("submission_monitor.notify.smtplib.SMTP") as constructor:
            session = constructor.return_value.__enter__.return_value
            session.send_message.return_value = {}
            send_email(settings, "Synthetic subject", "Synthetic body")
            names = [call[0] for call in session.method_calls]
            self.assertLess(names.index("starttls"), names.index("login"))
            self.assertLess(names.index("login"), names.index("send_message"))

    def test_partial_recipient_refusal_does_not_acknowledge_event(self):
        with patch("submission_monitor.notify.smtplib.SMTP_SSL") as constructor:
            session = constructor.return_value.__enter__.return_value
            session.send_message.return_value = {"recipient@example.test": (550, b"rejected")}
            from smtplib import SMTPRecipientsRefused
            with self.assertRaises(SMTPRecipientsRefused):
                send_email(self.settings(), "Synthetic subject", "Synthetic body")


if __name__ == "__main__":
    unittest.main()
