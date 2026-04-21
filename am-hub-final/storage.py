"""
storage.py — файловое хранилище.
Cloudflare R2 (primary, S3-compatible, бесплатно до 10GB)
→ fallback: local disk /tmp/amhub-uploads/
"""
import os, uuid, logging, mimetypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
R2_ACCOUNT_ID  = os.environ.get("CF_R2_ACCOUNT_ID",  "")
R2_ACCESS_KEY  = os.environ.get("CF_R2_ACCESS_KEY",  "")
R2_SECRET_KEY  = os.environ.get("CF_R2_SECRET_KEY",  "")
R2_BUCKET      = os.environ.get("CF_R2_BUCKET",      "amhub")
R2_PUBLIC_URL  = os.environ.get("CF_R2_PUBLIC_URL",  "")  # https://pub-xxx.r2.dev

LOCAL_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/tmp/amhub-uploads"))
LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_MIME  = {
    "application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "text/plain", "text/csv",
    "application/zip",
    # Audio (voice notes)
    "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav",
    "audio/mp4", "audio/m4a", "audio/x-m4a",
}


def _get_s3():
    """Получить boto3 S3 client для Cloudflare R2."""
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY]):
        return None
    try:
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            region_name="auto",
        )
    except Exception as e:
        logger.warning(f"R2 client error: {e}")
        return None


async def upload_file(
    file_bytes: bytes,
    original_filename: str,
    client_id: Optional[int] = None,
    mime_type: Optional[str] = None,
) -> dict:
    """
    Загружает файл в R2 или local.
    Возвращает {"key": "...", "url": "...", "size": N, "mime_type": "..."}
    """
    if len(file_bytes) > MAX_FILE_SIZE:
        raise ValueError(f"Файл слишком большой: {len(file_bytes)/1024/1024:.1f} MB (max 50 MB)")

    if not mime_type:
        mime_type, _ = mimetypes.guess_type(original_filename)
    if mime_type and mime_type not in ALLOWED_MIME:
        raise ValueError(f"Тип файла не разрешён: {mime_type}")

    ext = Path(original_filename).suffix.lower()
    file_key = f"clients/{client_id or 'shared'}/{uuid.uuid4().hex}{ext}"

    s3 = _get_s3()
    if s3:
        try:
            s3.put_object(
                Bucket=R2_BUCKET, Key=file_key, Body=file_bytes,
                ContentType=mime_type or "application/octet-stream",
                ContentDisposition=f'inline; filename="{original_filename}"',
            )
            url = f"{R2_PUBLIC_URL}/{file_key}" if R2_PUBLIC_URL else f"/api/files/{file_key}"
            logger.info(f"Uploaded to R2: {file_key} ({len(file_bytes)} bytes)")
            return {"key": file_key, "url": url, "size": len(file_bytes), "mime_type": mime_type, "storage": "r2"}
        except Exception as e:
            logger.warning(f"R2 upload failed, falling back to local: {e}")

    # Local fallback
    local_path = LOCAL_UPLOAD_DIR / file_key.replace("/", "_")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(file_bytes)
    return {"key": file_key, "url": f"/api/files/{file_key.replace('/', '_')}", "size": len(file_bytes), "mime_type": mime_type, "storage": "local"}


async def get_file(file_key: str) -> Optional[bytes]:
    """Скачать файл по ключу."""
    s3 = _get_s3()
    if s3:
        try:
            resp = s3.get_object(Bucket=R2_BUCKET, Key=file_key)
            return resp["Body"].read()
        except Exception:
            pass
    # Local
    local_path = LOCAL_UPLOAD_DIR / file_key.replace("/", "_")
    if local_path.exists():
        return local_path.read_bytes()
    return None


async def delete_file(file_key: str) -> bool:
    """Удалить файл."""
    s3 = _get_s3()
    if s3:
        try:
            s3.delete_object(Bucket=R2_BUCKET, Key=file_key)
            return True
        except Exception:
            pass
    local_path = LOCAL_UPLOAD_DIR / file_key.replace("/", "_")
    if local_path.exists():
        local_path.unlink()
        return True
    return False


def get_signed_url(file_key: str, expires: int = 3600) -> Optional[str]:
    """Presigned URL для прямого скачивания из R2."""
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{file_key}"  # public bucket
    s3 = _get_s3()
    if s3:
        try:
            return s3.generate_presigned_url(
                "get_object", Params={"Bucket": R2_BUCKET, "Key": file_key}, ExpiresIn=expires
            )
        except Exception:
            pass
    return f"/api/files/{file_key.replace('/', '_')}"
