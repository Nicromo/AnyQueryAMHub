"""
PDF генерация — QBR отчёты и карточки клиентов
"""
from typing import Optional
from datetime import datetime
import logging, os

from fastapi import APIRouter, Cookie, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import desc

from database import get_db
from models import Client, QBR, Task, Meeting, AccountPlan
from auth import decode_access_token
from models import User

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _require_user(auth_token, db):
    if not auth_token:
        raise HTTPException(status_code=401)
    payload = decode_access_token(auth_token)
    if not payload:
        raise HTTPException(status_code=401)
    user = db.query(User).filter(User.id == int(payload.get("sub", 0))).first()
    if not user:
        raise HTTPException(status_code=401)
    return user


def _build_qbr_html(client: Client, qbr: QBR, tasks: list, meetings: list) -> str:
    """Генерирует красивый HTML для QBR PDF."""
    now = datetime.utcnow()
    health_pct = round((client.health_score or 0) * 100)
    health_color = "#22c55e" if health_pct >= 70 else "#f59e0b" if health_pct >= 40 else "#ef4444"
    mrr = client.mrr or 0

    achievements = qbr.achievements or []
    issues = qbr.issues or []
    goals = qbr.next_quarter_goals or []
    future = qbr.future_work or []

    def list_items(items, color="#1e293b"):
        if not items:
            return "<li style='color:#94a3b8'>Нет данных</li>"
        return "".join(f"<li style='margin-bottom:6px;'>{i if isinstance(i, str) else i.get('text', str(i))}</li>" for i in items)

    tasks_done = sum(1 for t in tasks if t.status == "done")
    tasks_open = sum(1 for t in tasks if t.status in ("plan", "in_progress"))
    tasks_blocked = sum(1 for t in tasks if t.status == "blocked")

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Arial', sans-serif; color: #1e293b; background: #fff; font-size: 13px; line-height: 1.5; }}
  .page {{ max-width: 900px; margin: 0 auto; padding: 40px; }}

  .header {{ background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%); color: #fff; padding: 32px 40px; border-radius: 12px; margin-bottom: 28px; }}
  .header h1 {{ font-size: 24px; font-weight: 800; margin-bottom: 4px; }}
  .header .sub {{ font-size: 13px; opacity: .7; margin-bottom: 20px; }}
  .kpi-row {{ display: flex; gap: 20px; flex-wrap: wrap; }}
  .kpi-box {{ background: rgba(255,255,255,.1); border-radius: 8px; padding: 12px 18px; min-width: 120px; }}
  .kpi-box .val {{ font-size: 22px; font-weight: 700; }}
  .kpi-box .lbl {{ font-size: 10px; opacity: .7; text-transform: uppercase; letter-spacing: .5px; margin-top: 2px; }}

  .section {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 20px 24px; margin-bottom: 18px; }}
  .section h2 {{ font-size: 14px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }}
  .section ul {{ padding-left: 18px; color: #334155; }}

  .metrics-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }}
  .metric-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; text-align: center; }}
  .metric-card .val {{ font-size: 20px; font-weight: 700; color: #1e293b; }}
  .metric-card .lbl {{ font-size: 10px; color: #94a3b8; text-transform: uppercase; margin-top: 3px; }}

  .tasks-row {{ display: flex; gap: 10px; }}
  .task-chip {{ flex: 1; text-align: center; padding: 10px; border-radius: 7px; }}
  .tc-done {{ background: #dcfce7; color: #166534; }}
  .tc-open {{ background: #dbeafe; color: #1e40af; }}
  .tc-blocked {{ background: #fee2e2; color: #991b1b; }}

  .footer {{ text-align: center; color: #94a3b8; font-size: 11px; margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; }}
  @media print {{ .page {{ padding: 20px; }} }}
</style>
</head>
<body>
<div class="page">

  <!-- Шапка -->
  <div class="header">
    <div class="sub">Quarterly Business Review · {qbr.quarter} · Подготовлено {now.strftime("%d.%m.%Y")}</div>
    <h1>{client.name}</h1>
    <div class="kpi-row">
      <div class="kpi-box">
        <div class="val" style="color:{health_color};">{health_pct}%</div>
        <div class="lbl">Health Score</div>
      </div>
      <div class="kpi-box">
        <div class="val">{mrr:,.0f} ₽</div>
        <div class="lbl">MRR</div>
      </div>
      <div class="kpi-box">
        <div class="val">{client.nps_last if client.nps_last else "—"}</div>
        <div class="lbl">NPS</div>
      </div>
      <div class="kpi-box">
        <div class="val">{client.segment or "—"}</div>
        <div class="lbl">Сегмент</div>
      </div>
    </div>
  </div>

  <!-- Метрики задач -->
  <div class="tasks-row" style="margin-bottom:18px;">
    <div class="task-chip tc-done"><div style="font-size:18px;font-weight:700;">{tasks_done}</div><div style="font-size:10px;text-transform:uppercase;">Выполнено</div></div>
    <div class="task-chip tc-open"><div style="font-size:18px;font-weight:700;">{tasks_open}</div><div style="font-size:10px;text-transform:uppercase;">Открыто</div></div>
    <div class="task-chip tc-blocked"><div style="font-size:18px;font-weight:700;">{tasks_blocked}</div><div style="font-size:10px;text-transform:uppercase;">Заблок.</div></div>
    <div class="task-chip" style="background:#f1f5f9;color:#475569;"><div style="font-size:18px;font-weight:700;">{len(meetings)}</div><div style="font-size:10px;text-transform:uppercase;">Встреч</div></div>
  </div>

  <!-- Достижения -->
  <div class="section">
    <h2>🏆 Достижения квартала</h2>
    <ul>{list_items(achievements)}</ul>
  </div>

  <!-- Проблемы -->
  <div class="section">
    <h2>⚠️ Проблемы и вызовы</h2>
    <ul>{list_items(issues)}</ul>
  </div>

  <!-- Цели на следующий квартал -->
  <div class="section">
    <h2>🎯 Цели на следующий квартал</h2>
    <ul>{list_items(goals)}</ul>
  </div>

  <!-- Планы по задачам -->
  {'<div class="section"><h2>🗺️ Планируемые работы</h2><ul>' + list_items(future) + '</ul></div>' if future else ''}

  <!-- Резюме -->
  {'<div class="section"><h2>📝 Резюме</h2><p style="color:#334155;">' + (qbr.executive_summary or qbr.summary or '') + '</p></div>' if (qbr.executive_summary or qbr.summary) else ''}

  <div class="footer">
    AM Hub · {client.name} · {qbr.quarter} · {now.strftime("%d.%m.%Y")}
    {f' · Менеджер: {client.manager_email}' if client.manager_email else ''}
  </div>
</div>
</body>
</html>"""


@router.get("/api/clients/{client_id}/qbr/{quarter}/pdf")
async def download_qbr_pdf(
    client_id: int,
    quarter: str,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Скачать QBR как PDF."""
    _require_user(auth_token, db)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    qbr = db.query(QBR).filter(
        QBR.client_id == client_id,
        QBR.quarter == quarter,
    ).first()
    if not qbr:
        raise HTTPException(status_code=404, detail=f"QBR {quarter} не найден")

    tasks = db.query(Task).filter(Task.client_id == client_id).all()
    meetings = db.query(Meeting).filter(Meeting.client_id == client_id).all()

    html_content = _build_qbr_html(client, qbr, tasks, meetings)

    # Попытка сгенерировать PDF через WeasyPrint
    try:
        from weasyprint import HTML as WH
        import io
        pdf_bytes = WH(string=html_content).write_pdf()
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="QBR_{client.name}_{quarter}.pdf"',
            },
        )
    except ImportError:
        # WeasyPrint не установлен — возвращаем HTML для печати из браузера
        return HTMLResponse(
            content=html_content,
            headers={
                "Content-Disposition": f'inline; filename="QBR_{client.name}_{quarter}.html"',
            },
        )
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        return HTMLResponse(content=html_content)


@router.get("/api/clients/{client_id}/qbr/latest/pdf")
async def download_latest_qbr_pdf(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Скачать последний QBR как PDF."""
    _require_user(auth_token, db)

    qbr = db.query(QBR).filter(
        QBR.client_id == client_id
    ).order_by(desc(QBR.year), desc(QBR.quarter)).first()

    if not qbr:
        raise HTTPException(status_code=404, detail="QBR не найден")

    return await download_qbr_pdf(client_id, qbr.quarter, db, auth_token)
