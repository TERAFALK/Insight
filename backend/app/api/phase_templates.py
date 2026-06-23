"""API-endpoints för konfigurerbara orderfaser."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import current_user, require_admin
from app.db.database import get_db
from app.db.models import OrderPhaseTemplate, User

router = APIRouter()


def _phase_dict(phase: OrderPhaseTemplate) -> dict:
    return {
        "id": phase.id,
        "order_type": phase.order_type,
        "name": phase.name,
        "position": phase.position,
        "is_default": phase.is_default,
    }


@router.get("")
async def list_phase_templates(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(OrderPhaseTemplate).order_by(
            OrderPhaseTemplate.order_type, OrderPhaseTemplate.position
        )
    )
    return [_phase_dict(p) for p in result.all()]


class PhaseTemplateBody(BaseModel):
    order_type: str  # "order" | "project"
    name: str
    position: int = 0
    is_default: bool = False


@router.post("", status_code=201)
async def create_phase_template(
    body: PhaseTemplateBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if body.order_type not in ("order", "project"):
        raise HTTPException(status_code=400, detail="order_type måste vara 'order' eller 'project'")
    phase = OrderPhaseTemplate(
        id=str(uuid.uuid4()),
        order_type=body.order_type,
        name=body.name,
        position=body.position,
        is_default=body.is_default,
    )
    db.add(phase)
    await db.commit()
    return _phase_dict(phase)


@router.put("/{phase_id}")
async def update_phase_template(
    phase_id: str,
    body: PhaseTemplateBody,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    phase = await db.get(OrderPhaseTemplate, phase_id)
    if not phase:
        raise HTTPException(status_code=404, detail="Fas hittades inte")
    phase.name = body.name
    phase.position = body.position
    phase.is_default = body.is_default
    await db.commit()
    return _phase_dict(phase)


@router.delete("/{phase_id}", status_code=204)
async def delete_phase_template(
    phase_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    phase = await db.get(OrderPhaseTemplate, phase_id)
    if not phase:
        raise HTTPException(status_code=404, detail="Fas hittades inte")
    await db.delete(phase)
    await db.commit()
