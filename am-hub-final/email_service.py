"""
Email уведомления - интеграция с отправкой писем
Поддержка: SendGrid, SMTP, Postmark
"""

import os
import logging
from typing import List, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Configuration
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp")  # smtp, sendgrid, postmark
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@amhub.local")

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
POSTMARK_API_KEY = os.getenv("POSTMARK_API_KEY", "")


class EmailType(str, Enum):
    MORNING_PLAN = "morning_plan"
    OVERDUE_CHECKUP = "overdue_checkup"
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    MEETING_REMINDER = "meeting_reminder"
    WEEKLY_DIGEST = "weekly_digest"


class EmailService:
    """Базовый сервис отправки email"""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> bool:
        """Отправить email"""
        raise NotImplementedError


class SMTPEmailService(EmailService):
    """SMTP сервис"""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> bool:
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = FROM_EMAIL
            msg["To"] = to_email

            if cc:
                msg["Cc"] = ", ".join(cc)
            if bcc:
                msg["Bcc"] = ", ".join(bcc)

            # Attach text part
            text_part = MIMEText(body, "plain")
            msg.attach(text_part)

            # Attach HTML part if provided
            if html_body:
                html_part = MIMEText(html_body, "html")
                msg.attach(html_part)

            # Send
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)

            logger.info(f"✅ Email sent to {to_email}")
            return True

        except Exception as e:
            logger.error(f"❌ Email send error: {e}")
            return False


class SendGridEmailService(EmailService):
    """SendGrid сервис"""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> bool:
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail

            email = Mail(
                from_email=FROM_EMAIL,
                to_emails=to_email,
                subject=subject,
                plain_text_content=body,
                html_content=html_body,
            )

            if cc:
                email.cc = cc
            if bcc:
                email.bcc = bcc

            sg = SendGridAPIClient(SENDGRID_API_KEY)
            response = sg.send(email)

            logger.info(f"✅ Email sent via SendGrid to {to_email}")
            return True

        except Exception as e:
            logger.error(f"❌ SendGrid error: {e}")
            return False


class PostmarkEmailService(EmailService):
    """Postmark сервис"""

    async def send(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> bool:
        try:
            import httpx

            headers = {
                "X-Postmark-Server-Token": POSTMARK_API_KEY,
                "Content-Type": "application/json",
            }

            data = {
                "From": FROM_EMAIL,
                "To": to_email,
                "Subject": subject,
                "TextBody": body,
            }

            if html_body:
                data["HtmlBody"] = html_body

            if cc:
                data["Cc"] = ", ".join(cc)

            if bcc:
                data["Bcc"] = ", ".join(bcc)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.postmarkapp.com/email",
                    json=data,
                    headers=headers,
                    timeout=10,
                )

                if response.status_code == 200:
                    logger.info(f"✅ Email sent via Postmark to {to_email}")
                    return True
                else:
                    logger.warning(f"Postmark error: {response.status_code}")
                    return False

        except Exception as e:
            logger.error(f"❌ Postmark error: {e}")
            return False


def get_email_service() -> EmailService:
    """Получить сервис отправки email на основе конфигурации"""
    if EMAIL_PROVIDER == "sendgrid":
        return SendGridEmailService()
    elif EMAIL_PROVIDER == "postmark":
        return PostmarkEmailService()
    else:  # default: smtp
        return SMTPEmailService()


# ============================================================================
# Email Templates
# ============================================================================


def get_morning_plan_email(
    manager_name: str,
    overdue_checkups: List[dict],
    today_tasks: List[dict],
) -> tuple[str, str]:
    """Шаблон утреннего плана"""
    text_body = f"""
Доброе утро, {manager_name}!

Ваш план на день:

📋 ПЕРЕГОТОВЛЕННЫЕ ЧЕКАПЫ ({len(overdue_checkups)}):
"""
    
    for checkup in overdue_checkups[:5]:
        text_body += f"\n  • {checkup['client_name']} - {checkup['type']} ({checkup['days_overdue']} дней)"
    
    text_body += f"\n\n📅 ЗАДАЧИ НА СЕГОДНЯ ({len(today_tasks)}):\n"
    
    for task in today_tasks[:10]:
        text_body += f"\n  • {task['title']} (приоритет: {task['priority']})"
    
    text_body += "\n\nУдачи!"

    html_body = f"""
    <html>
    <body>
        <h2>Доброе утро, {manager_name}!</h2>
        <p>Ваш план на день:</p>
        
        <h3>📋 Переготовленные чекапы ({len(overdue_checkups)})</h3>
        <ul>
    """
    
    for checkup in overdue_checkups[:5]:
        html_body += f"<li>{checkup['client_name']} - {checkup['type']} ({checkup['days_overdue']} дней)</li>"
    
    html_body += f"""
        </ul>
        
        <h3>📅 Задачи на сегодня ({len(today_tasks)})</h3>
        <ul>
    """
    
    for task in today_tasks[:10]:
        html_body += f"<li>{task['title']} (приоритет: {task['priority']})</li>"
    
    html_body += """
        </ul>
        <p>Удачи!</p>
    </body>
    </html>
    """
    
    return text_body, html_body


def get_overdue_checkup_email(client_name: str, checkup_type: str, days_overdue: int) -> tuple[str, str]:
    """Шаблон алерта на переутомленный чекап"""
    text_body = f"""
⚠️ ВНИМАНИЕ: Переутомленный чекап

Клиент: {client_name}
Тип: {checkup_type}
Просрочено на: {days_overdue} дней

Пожалуйста, как можно скорее запланируйте встречу.
    """
    
    html_body = f"""
    <html>
    <body>
        <h2>⚠️ ВНИМАНИЕ: Переутомленный чекап</h2>
        <p><strong>Клиент:</strong> {client_name}</p>
        <p><strong>Тип:</strong> {checkup_type}</p>
        <p><strong>Просрочено на:</strong> {days_overdue} дней</p>
        <p>Пожалуйста, как можно скорее запланируйте встречу.</p>
    </body>
    </html>
    """
    
    return text_body, html_body


def get_task_created_email(task_title: str, client_name: str, assigned_to: str) -> tuple[str, str]:
    """Шаблон: задача создана"""
    text_body = f"""
✅ Новая задача создана

Задача: {task_title}
Клиент: {client_name}
Назначено: {assigned_to}
    """
    
    html_body = f"""
    <html>
    <body>
        <h2>✅ Новая задача создана</h2>
        <p><strong>Задача:</strong> {task_title}</p>
        <p><strong>Клиент:</strong> {client_name}</p>
        <p><strong>Назначено:</strong> {assigned_to}</p>
    </body>
    </html>
    """
    
    return text_body, html_body


async def send_morning_plan(
    manager_email: str,
    manager_name: str,
    overdue_checkups: List[dict],
    today_tasks: List[dict],
) -> bool:
    """Отправить утренний план"""
    service = get_email_service()
    
    text_body, html_body = get_morning_plan_email(manager_name, overdue_checkups, today_tasks)
    
    return await service.send(
        to_email=manager_email,
        subject="📋 Ваш утренний план",
        body=text_body,
        html_body=html_body,
    )


async def send_overdue_checkup_alert(
    manager_email: str,
    client_name: str,
    checkup_type: str,
    days_overdue: int,
) -> bool:
    """Отправить алерт на переутомленный чекап"""
    service = get_email_service()
    
    text_body, html_body = get_overdue_checkup_email(client_name, checkup_type, days_overdue)
    
    return await service.send(
        to_email=manager_email,
        subject=f"⚠️ Переутомленный чекап: {client_name}",
        body=text_body,
        html_body=html_body,
    )


async def send_task_created(
    assigned_to_email: str,
    task_title: str,
    client_name: str,
) -> bool:
    """Отправить уведомление о создании задачи"""
    service = get_email_service()
    
    text_body, html_body = get_task_created_email(task_title, client_name, assigned_to_email)
    
    return await service.send(
        to_email=assigned_to_email,
        subject=f"✅ Новая задача: {task_title}",
        body=text_body,
        html_body=html_body,
    )
