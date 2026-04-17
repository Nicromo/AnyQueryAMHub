"""
Авторизация: Telegram Login Widget + JWT + Email/Password
"""
import os
import hashlib
import hmac
import time
from typing import Optional
from datetime import datetime, timedelta

from fastapi import Request, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from itsdangerous import URLSafeTimedSerializer, BadSignature
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

# Database
from database import SessionLocal
from models import User, AuditLog

# Constants
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def verify_tg_auth(data: dict, bot_token: str) -> bool:
    """Проверяем подпись от Telegram Login Widget."""
    check_hash = data.pop("hash", None)
    if not check_hash:
        return False

    # Строка для проверки — все поля кроме hash, отсортированные
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # Проверяем подпись и свежесть (не старше 1 часа)
    if not hmac.compare_digest(computed, check_hash):
        return False
    if time.time() - int(data.get("auth_date", 0)) > 3600:
        return False
    return True


# ============================================================================
# Password Functions
# ============================================================================


def hash_password(password: str) -> str:
    """Хэшировать пароль.

    bcrypt ограничивает вход в 72 байта — это безопасно усекаем,
    чтобы не падать на длинных паролях (совместимость с bcrypt 4.x).
    """
    if isinstance(password, str):
        password_bytes = password.encode("utf-8")[:72]
        password = password_bytes.decode("utf-8", errors="ignore")
    # Попытка через passlib; если всё-таки падает bug-check — fallback на прямой bcrypt
    try:
        return pwd_context.hash(password)
    except Exception:
        import bcrypt as _bc
        return _bc.hashpw(password.encode("utf-8")[:72], _bc.gensalt(rounds=12)).decode()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверить пароль (совместимо с passlib и прямым bcrypt)."""
    if isinstance(plain_password, str):
        pw_bytes = plain_password.encode("utf-8")[:72]
        plain_password = pw_bytes.decode("utf-8", errors="ignore")
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        try:
            import bcrypt as _bc
            return _bc.checkpw(
                plain_password.encode("utf-8")[:72],
                hashed_password.encode("utf-8") if isinstance(hashed_password, str) else hashed_password,
            )
        except Exception:
            return False


# ============================================================================
# JWT Token Functions
# ============================================================================


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Создать JWT токен"""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Декодировать JWT токен"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            return None
        return payload
    except JWTError:
        return None


# ============================================================================
# Database Functions
# ============================================================================


def get_db() -> Session:
    """Получить сессию БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Найти пользователя по email"""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    """Найти пользователя по ID"""
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_telegram_id(db: Session, telegram_id: str) -> Optional[User]:
    """Найти пользователя по Telegram ID"""
    return db.query(User).filter(User.telegram_id == telegram_id).first()


# ============================================================================
# Authentication Functions
# ============================================================================


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> Optional[User]:
    """Аутентифицировать пользователя (email + password)"""
    user = get_user_by_email(db, email)
    
    if not user:
        return None
    
    if not user.hashed_password:
        return None
    
    if not verify_password(password, user.hashed_password):
        return None
    
    return user


def create_user(
    db: Session,
    email: str,
    first_name: str,
    last_name: str,
    password: Optional[str] = None,
    telegram_id: Optional[str] = None,
    role: str = "manager",
) -> User:
    """Создать ново пользователя"""
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        role=role,
        telegram_id=telegram_id,
    )
    
    if password:
        user.hashed_password = hash_password(password)
    
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ============================================================================
# Dependencies
# ============================================================================


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Получить текущего аутентифицированного пользователя"""
    token = credentials.credentials
    payload = decode_access_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    user = get_user_by_id(db, user_id)
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )
    
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """Получить текущего админа (или выбросить 403)"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


# ============================================================================
# Authorization Checks
# ============================================================================


def check_client_access(user: User, client_id: int, db: Session) -> bool:
    """
    Проверить может ли пользователь видеть этого клиента
    - Админ видит всех
    - Менеджер видит только своих
    - Viewer видит только своих
    """
    if user.role == "admin":
        return True
    
    # Проверить есть ли клиент в assigned_clients
    from models import Client
    
    assigned_client = db.query(Client).filter(
        Client.id == client_id
    ).join(User.assigned_clients).filter(
        User.id == user.id
    ).first()
    
    if assigned_client:
        return True
    
    # Fallback: check by manager_email
    client = db.query(Client).filter(Client.id == client_id).first()
    if client and client.manager_email == user.email:
        return True
    
    return False


def ensure_client_access(user: User, client_id: int, db: Session):
    """Выбросить 403 если нет доступа"""
    if not check_client_access(user, client_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No access to this client",
        )


# ============================================================================
# User Management
# ============================================================================


def assign_client_to_user(
    db: Session,
    user_id: int,
    client_id: int,
):
    """Назначить клиента менеджеру"""
    from models import UserClientAssignment
    
    # Проверить есть ли уже такое назначение
    existing = db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user_id,
        UserClientAssignment.client_id == client_id,
    ).first()
    
    if existing:
        return
    
    assignment = UserClientAssignment(
        user_id=user_id,
        client_id=client_id,
    )
    db.add(assignment)
    db.commit()


def remove_client_from_user(
    db: Session,
    user_id: int,
    client_id: int,
):
    """Убрать клиента от менеджера"""
    from models import UserClientAssignment
    
    db.query(UserClientAssignment).filter(
        UserClientAssignment.user_id == user_id,
        UserClientAssignment.client_id == client_id,
    ).delete()
    
    db.commit()


# ============================================================================
# Audit Logging
# ============================================================================


def log_audit(
    db: Session,
    user_id: Optional[int],
    action: str,
    resource_type: str,
    resource_id: int,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    ip_address: Optional[str] = None,
):
    """Логировать действие в audit log"""
    audit = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        old_values=old_values,
        new_values=new_values,
        ip_address=ip_address,
    )
    db.add(audit)
    db.commit()


class SessionManager:
    def __init__(self, secret_key: str):
        self.serializer = URLSafeTimedSerializer(secret_key)

    def create_session(self, tg_id: int, tg_name: str) -> str:
        return self.serializer.dumps({"id": tg_id, "name": tg_name})

    def get_user(self, request: Request) -> Optional[dict]:
        token = request.cookies.get("session")
        if not token:
            return None
        try:
            data = self.serializer.loads(token, max_age=86400 * 7)  # 7 дней
            return data
        except BadSignature:
            return None

    def require_user(self, request: Request) -> dict:
        user = self.get_user(request)
        if not user:
            raise HTTPException(status_code=302, headers={"Location": "/login"})
        return user
