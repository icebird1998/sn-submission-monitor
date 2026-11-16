"""Independent delivery channels. TLS verification is never disabled."""
import base64
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid


def send_email(settings, subject, body, event_id=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("投稿状态监控", settings["smtp_user"]))
    recipients = settings["to"]
    if isinstance(recipients, str):
        recipients = [recipients]
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = ("<%s@sn-submission-monitor.local>" % event_id
                         if event_id else make_msgid(domain="sn-submission-monitor.local"))
    msg.set_content(body, charset="utf-8")
    context = ssl.create_default_context()
    port = settings.get("smtp_port", 465)
    if settings.get("smtp_security", "ssl") == "starttls":
        server = smtplib.SMTP(settings["smtp_host"], port, timeout=45)
    else:
        server = smtplib.SMTP_SSL(settings["smtp_host"], port, timeout=45, context=context)
    with server as session:
        if settings.get("smtp_security", "ssl") == "starttls":
            session.ehlo()
            session.starttls(context=context)
            session.ehlo()
        session.login(settings["smtp_user"], settings["smtp_auth_code"])
        refused = session.send_message(msg, from_addr=settings["smtp_user"], to_addrs=recipients)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    return True


def send_toast(title, body):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$t = $xml.GetElementsByTagName('text'); "
        "$t.Item(0).AppendChild($xml.CreateTextNode(%s)) | Out-Null; "
        "$t.Item(1).AppendChild($xml.CreateTextNode(%s)) | Out-Null; "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show([Windows.UI.Notifications.ToastNotification]::new($xml))"
    ) % (quote(title), quote(body[:500]))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        # PowerShell CLIXML can include large incidental data; don't persist it.
        raise RuntimeError("Windows 通知提交失败，请检查桌面会话及通知权限")
    return True
