"""
Voice notes (голосовые заметки) — запись в браузере → R2 → Groq Whisper транскрипция.

Endpoints:
  POST   /api/voice-notes                      (multipart: audio + client_id + meeting_id?)
  GET    /api/clients/{id}/voice-notes         список
  GET    /api/meetings/{id}/voice-notes        список
  POST   /api/voice-notes/{id}/transcribe      ручной перезапуск транскрипции
  DELETE /api/voice-notes/{id}
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional
import logging
import os

from fastapi import APIRouter, Cookie, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from auth import decode_access_token
from models import Client, Meeting, User, VoiceNote

logger = logging.getLogger(__name__)
router = APIRouter()


def _user(auth_token: Optional[str], db: Session) -> User:
    if not auth_token:
        raise HTTPException(401)
    p = decode_access_token(auth_token)
    if not p:
        raise HTTPException(401)
    u = db.query(User).filter(User.id == int(p.get("sub"))).first()
    if not u:
        raise HTTPException(401)
    return u


def _user_groq_key(user: User) -> Optional[str]:
    settings = user.settings or {}
    return ((settings.get("groq") or {}).get("api_key")
            or os.environ.get("GROQ_API_KEY"))


@router.post("/api/voice-notes")
async def voice_note_upload(
    client_id: int = Form(...),
    meeting_id: Optional[int] = Form(None),
    audio: UploadFile = File(...),
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Upload аудио-файла (webm/ogg/mp3/...). Транскрипция запускается синхронно
    (для короткой записи до 2 мин это ok; для долгих — можно вынести в job)."""
    user = _user(auth_token, db)
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "client not found")
    if meeting_id:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m or m.client_id != client_id:
            raise HTTPException(400, "meeting not linked to client")

    # 1. Upload в R2/local
    from storage import upload_file
    data = await audio.read()
    if not data:
        raise HTTPException(400, "empty audio")
    try:
        up = await upload_file(
            file_bytes=data,
            original_filename=audio.filename or "voice.webm",
            client_id=client_id,
            mime_type=audio.content_type,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 2. Запись в БД
    note = VoiceNote(
        meeting_id=meeting_id,
        client_id=client_id,
        user_id=user.id,
        audio_url=up.get("url"),
        duration_seconds=0,  # не знаем без декода; можно доработать
    )
    db.add(note)
    db.flush()

    # 3. Транскрипция (fire-and-acknowledge; на таймауте просто нет текста)
    try:
        from integrations.whisper import transcribe_bytes
        key = _user_groq_key(user)
        text = await transcribe_bytes(data, filename=audio.filename or "voice.webm", api_key=key)
        if text:
            note.transcription = text
    except Exception as e:
        logger.warning(f"whisper transcribe failed for voice_note={note.id}: {e}")

    db.commit()
    db.refresh(note)
    return {
        "id": note.id, "audio_url": note.audio_url,
        "transcription": note.transcription,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }


@router.get("/api/clients/{client_id}/voice-notes")
async def voice_notes_by_client(
    client_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    rows = (db.query(VoiceNote)
              .filter(VoiceNote.client_id == client_id)
              .order_by(VoiceNote.created_at.desc()).all())
    return {"items": [{
        "id": n.id, "meeting_id": n.meeting_id,
        "audio_url": n.audio_url, "transcription": n.transcription,
        "duration_seconds": n.duration_seconds,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "user_id": n.user_id,
    } for n in rows]}


@router.get("/api/meetings/{meeting_id}/voice-notes")
async def voice_notes_by_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    rows = (db.query(VoiceNote)
              .filter(VoiceNote.meeting_id == meeting_id)
              .order_by(VoiceNote.created_at.desc()).all())
    return {"items": [{
        "id": n.id, "client_id": n.client_id,
        "audio_url": n.audio_url, "transcription": n.transcription,
        "duration_seconds": n.duration_seconds,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    } for n in rows]}


@router.post("/api/voice-notes/{note_id}/transcribe")
async def voice_note_retranscribe(
    note_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    """Повторный запуск Whisper (если был сбой или ключа не было)."""
    user = _user(auth_token, db)
    note = db.query(VoiceNote).filter(VoiceNote.id == note_id).first()
    if not note:
        raise HTTPException(404)
    if not note.audio_url:
        raise HTTPException(400, "no audio_url")

    # Качаем файл
    try:
        from storage import get_file
        key = note.audio_url.split("/api/files/")[-1].replace("_", "/") if note.audio_url.startswith("/api/files/") else None
        audio_data = None
        if key:
            audio_data = await get_file(key)
        if not audio_data:
            raise HTTPException(400, "cannot fetch audio from storage")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"fetch audio failed: {e}")

    from integrations.whisper import transcribe_bytes
    text = await transcribe_bytes(audio_data, api_key=_user_groq_key(user))
    if text:
        note.transcription = text
        db.commit()
        return {"ok": True, "transcription": text}
    raise HTTPException(502, "whisper returned empty / failed")


@router.delete("/api/voice-notes/{note_id}")
async def voice_note_delete(
    note_id: int,
    db: Session = Depends(get_db),
    auth_token: Optional[str] = Cookie(None),
):
    _user(auth_token, db)
    note = db.query(VoiceNote).filter(VoiceNote.id == note_id).first()
    if not note:
        raise HTTPException(404)
    db.delete(note)
    db.commit()
    return {"ok": True}
