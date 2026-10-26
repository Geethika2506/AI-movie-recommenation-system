"""Pending username, email, and password changes confirmed by email code."""

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import hash_password, hash_token, verify_password
from backend.models import EmailChallenge, User
from backend.schemas import VerificationRequest

CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5


def prepare_change(db: Session, user: User, payload: VerificationRequest) -> tuple[str, str]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    if payload.purpose == "username":
        username = (payload.username or "").strip()
        if username.lower() == user.username.lower():
            raise HTTPException(status_code=422, detail="username is unchanged")
        taken = db.scalar(
            select(User.id).where(
                func.lower(User.username) == username.lower(),
                User.id != user.id,
            )
        )
        if taken:
            raise HTTPException(status_code=409, detail="username is already taken")
        return username, user.email
    if payload.purpose == "email":
        email = str(payload.email or "").lower()
        if email == user.email.lower():
            raise HTTPException(status_code=422, detail="email is unchanged")
        taken = db.scalar(
            select(User.id).where(func.lower(User.email) == email, User.id != user.id)
        )
        if taken:
            raise HTTPException(status_code=409, detail="email is already registered")
        return email, email
    password = payload.password or ""
    if verify_password(password, user.password_hash):
        raise HTTPException(status_code=422, detail="choose a different password")
    return hash_password(password), user.email


def store_challenge(
    db: Session,
    user: User,
    purpose: str,
    payload: str,
    sent_to: str,
    code: str,
) -> EmailChallenge:
    now = datetime.now(UTC)
    pending = db.scalars(
        select(EmailChallenge).where(
            EmailChallenge.user_id == user.id,
            EmailChallenge.consumed_at.is_(None),
        )
    )
    for old in pending:
        old.consumed_at = now
    challenge = EmailChallenge(
        user_id=user.id,
        purpose=purpose,
        payload=payload,
        code_hash=hash_token(code),
        sent_to=sent_to,
        expires_at=now + CODE_TTL,
    )
    db.add(challenge)
    return challenge


def new_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def take_challenge(db: Session, user: User, purpose: str, code: str) -> EmailChallenge:
    now = datetime.now(UTC)
    challenge = db.scalar(
        select(EmailChallenge)
        .where(
            EmailChallenge.user_id == user.id,
            EmailChallenge.purpose == purpose,
            EmailChallenge.consumed_at.is_(None),
        )
        .order_by(EmailChallenge.created_at.desc())
    )
    if challenge is None or _as_utc(challenge.expires_at) <= now:
        raise HTTPException(
            status_code=400, detail="verification code is incorrect or expired"
        )
    if challenge.attempts >= MAX_ATTEMPTS:
        challenge.consumed_at = now
        db.commit()
        raise HTTPException(status_code=400, detail="too many attempts; send a new code")
    if not secrets.compare_digest(challenge.code_hash, hash_token(code)):
        challenge.attempts += 1
        if challenge.attempts >= MAX_ATTEMPTS:
            challenge.consumed_at = now
        db.commit()
        raise HTTPException(
            status_code=400, detail="verification code is incorrect or expired"
        )
    return challenge


def apply_change(user: User, challenge: EmailChallenge) -> None:
    if challenge.purpose == "username":
        user.username = challenge.payload
    elif challenge.purpose == "email":
        user.email = challenge.payload
    elif challenge.purpose == "password":
        user.password_hash = challenge.payload
    else:
        raise HTTPException(
            status_code=400, detail="verification code is incorrect or expired"
        )
    challenge.consumed_at = datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
