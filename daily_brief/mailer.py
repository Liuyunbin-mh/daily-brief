"""Send one plain-text message over SMTP SSL, then close the connection."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

_PLACEHOLDERS = {"", "PASTE_QQ_SMTP_AUTH_CODE", "your-smtp-auth-code"}


class MailError(Exception):
    """The brief was saved, but it was not sent."""


def read_secret(path: Path) -> str:
    if not path.is_file():
        raise MailError(
            f"未找到授权码文件：{path}\n"
            "请在 QQ 邮箱设置中开启 SMTP，把授权码写入该文件，并执行 chmod 600。本次不发信。"
        )
    secret = path.read_text(encoding="utf-8").strip()
    if secret in _PLACEHOLDERS or secret.startswith("#"):
        raise MailError(
            f"授权码文件尚未填写：{path}\n"
            "请把 QQ 邮箱 SMTP 授权码写入该文件。本次不发信。"
        )
    return secret


def send_email(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    from_addr: str,
    to: list[str],
    subject: str,
    body: str,
    timeout: float = 20.0,
) -> None:
    if not username or not from_addr or not to:
        raise MailError("发信配置不完整：需要 username、from_addr 和 to。本次不发信。")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(to)
    message.set_content(body)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=timeout) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    except MailError:
        raise
    except Exception as exc:
        raise MailError(f"发信失败：{exc}") from exc
