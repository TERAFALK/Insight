from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin
from app.core.audit import log_action
from app.core.security import hash_password
from app.db.database import get_db
from app.db.models import User

router = APIRouter()


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str = ""
    role: str = "customer"  # "admin" | "customer"
    customer_id: str | None = None


class UserUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    full_name: str | None = None
    role: str | None = None
    customer_id: str | None = None
    is_active: bool | None = None


@router.get("")
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    rows = await db.scalars(select(User).order_by(User.created_at))
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role,
            "customer_id": u.customer_id,
            "is_active": u.is_active,
        }
        for u in rows.all()
    ]


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(400, "Lösenordet måste vara minst 8 tecken")


@router.post("", status_code=201)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if body.role not in ("admin", "customer"):
        raise HTTPException(400, "Ogiltig roll — 'admin' eller 'customer'")
    if body.role == "customer" and not body.customer_id:
        raise HTTPException(400, "customer_id krävs för kundanvändare")
    _validate_password(body.password)
    existing = await db.scalar(select(User).where(User.email == body.email))
    if existing:
        raise HTTPException(400, "E-postadressen används redan")
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        customer_id=body.customer_id,
    )
    db.add(user)
    await db.flush()
    await log_action(db, admin, "user.create", "user", user.id,
                     f"Skapade användare {user.email} ({user.role})")
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "email": user.email}


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Användare hittades inte")
    if body.role is not None and body.role not in ("admin", "customer"):
        raise HTTPException(400, "Ogiltig roll — 'admin' eller 'customer'")
    if body.email is not None and body.email != user.email:
        clash = await db.scalar(select(User).where(User.email == body.email, User.id != user_id))
        if clash:
            raise HTTPException(400, "E-postadressen används redan")
    changed_password = False
    for field, value in body.model_dump(exclude_none=True).items():
        if field == "password":
            _validate_password(value)
            user.hashed_password = hash_password(value)
            changed_password = True
        else:
            setattr(user, field, value)
    note = " (lösenord ändrat)" if changed_password else ""
    await log_action(db, admin, "user.update", "user", user.id,
                     f"Uppdaterade användare {user.email}{note}")
    await db.commit()
    return {"id": user.id, "email": user.email}


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(400, "Du kan inte ta bort dig själv")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "Användare hittades inte")
    await log_action(db, admin, "user.delete", "user", user.id,
                     f"Raderade användare {user.email}")
    await db.delete(user)
    await db.commit()
