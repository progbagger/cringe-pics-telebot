from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Row

from .connection import get_connection
from .entities import (
    AdminBroadcast,
    AdminBroadcastDelivery,
    AdminBroadcastDeliveryStatus,
    AdminBroadcastStatus,
)
from .tables import admin_broadcast_deliveries, admin_broadcasts


async def create_admin_broadcast(
    *,
    created_by_user_id: int,
    source_chat_id: int,
    source_message_id: int,
    scheduled_local_at: datetime,
    timezone_offset_minutes: int | None,
) -> AdminBroadcast:
    now = datetime.now(UTC)
    async with get_connection() as conn:
        row = (
            await conn.execute(
                insert(admin_broadcasts)
                .values(
                    created_by_user_id=created_by_user_id,
                    source_chat_id=source_chat_id,
                    source_message_id=source_message_id,
                    scheduled_local_at=scheduled_local_at,
                    timezone_offset_minutes=timezone_offset_minutes,
                    status=AdminBroadcastStatus.scheduled,
                    created_at=now,
                    updated_at=now,
                )
                .returning(admin_broadcasts)
            )
        ).one()
    return _admin_broadcast_from_row(row)


async def get_admin_broadcast(broadcast_id: int) -> AdminBroadcast | None:
    async with get_connection() as conn:
        row = (await conn.execute(select(admin_broadcasts).where(admin_broadcasts.c.id == broadcast_id))).one_or_none()
    return _admin_broadcast_from_row(row) if row is not None else None


async def get_scheduled_admin_broadcasts() -> list[AdminBroadcast]:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(admin_broadcasts)
                .where(admin_broadcasts.c.status == AdminBroadcastStatus.scheduled)
                .order_by(admin_broadcasts.c.scheduled_local_at, admin_broadcasts.c.id)
            )
        ).all()
    return [_admin_broadcast_from_row(row) for row in rows]


async def get_dispatchable_admin_broadcasts() -> list[AdminBroadcast]:
    async with get_connection() as conn:
        rows = (
            await conn.execute(
                select(admin_broadcasts)
                .where(admin_broadcasts.c.status.in_((AdminBroadcastStatus.scheduled, AdminBroadcastStatus.sending)))
                .order_by(admin_broadcasts.c.scheduled_local_at, admin_broadcasts.c.id)
            )
        ).all()
    return [_admin_broadcast_from_row(row) for row in rows]


async def update_admin_broadcast_message(*, broadcast_id: int, source_chat_id: int, source_message_id: int) -> bool:
    return await _update_scheduled_broadcast(
        broadcast_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )


async def update_admin_broadcast_schedule(
    *,
    broadcast_id: int,
    scheduled_local_at: datetime,
    timezone_offset_minutes: int | None,
) -> bool:
    return await _update_scheduled_broadcast(
        broadcast_id,
        scheduled_local_at=scheduled_local_at,
        timezone_offset_minutes=timezone_offset_minutes,
    )


async def soft_delete_admin_broadcast(broadcast_id: int) -> bool:
    now = datetime.now(UTC)
    async with get_connection() as conn:
        result = await conn.execute(
            update(admin_broadcasts)
            .where(admin_broadcasts.c.id == broadcast_id)
            .where(admin_broadcasts.c.status == AdminBroadcastStatus.scheduled)
            .values(
                status=AdminBroadcastStatus.deleted,
                deleted_at=now,
                updated_at=now,
            )
            .returning(admin_broadcasts.c.id)
        )
    return result.scalar_one_or_none() is not None


async def reserve_admin_broadcast_deliveries(
    *, broadcast_id: int, user_ids: Iterable[int]
) -> list[AdminBroadcastDelivery]:
    unique_user_ids = set(user_ids)
    if not unique_user_ids:
        return []

    now = datetime.now(UTC)
    async with get_connection() as conn:
        broadcast_row = (
            await conn.execute(
                select(admin_broadcasts.c.status).where(admin_broadcasts.c.id == broadcast_id).with_for_update()
            )
        ).one_or_none()
        if broadcast_row is None or AdminBroadcastStatus(broadcast_row.status) not in {
            AdminBroadcastStatus.scheduled,
            AdminBroadcastStatus.sending,
        }:
            return []

        if AdminBroadcastStatus(broadcast_row.status) is AdminBroadcastStatus.scheduled:
            await conn.execute(
                update(admin_broadcasts)
                .where(admin_broadcasts.c.id == broadcast_id)
                .values(status=AdminBroadcastStatus.sending, started_at=now, updated_at=now)
            )

        rows = (
            await conn.execute(
                insert(admin_broadcast_deliveries)
                .values(
                    [
                        {
                            "broadcast_id": broadcast_id,
                            "user_id": user_id,
                            "status": AdminBroadcastDeliveryStatus.pending,
                            "attempted_at": now,
                        }
                        for user_id in unique_user_ids
                    ]
                )
                .on_conflict_do_nothing(constraint="admin_broadcast_deliveries_broadcast_user_key")
                .returning(admin_broadcast_deliveries)
            )
        ).all()
    return [_admin_broadcast_delivery_from_row(row) for row in rows]


async def finish_admin_broadcast_delivery(
    delivery_id: int,
    *,
    status: AdminBroadcastDeliveryStatus,
    error: str | None = None,
) -> None:
    if status is AdminBroadcastDeliveryStatus.pending:
        raise ValueError("A finished delivery cannot have pending status")

    async with get_connection() as conn:
        await conn.execute(
            update(admin_broadcast_deliveries)
            .where(admin_broadcast_deliveries.c.id == delivery_id)
            .where(admin_broadcast_deliveries.c.status == AdminBroadcastDeliveryStatus.pending)
            .values(status=status, error=error, finished_at=datetime.now(UTC))
        )


async def complete_admin_broadcast(broadcast_id: int) -> bool:
    now = datetime.now(UTC)
    async with get_connection() as conn:
        result = await conn.execute(
            update(admin_broadcasts)
            .where(admin_broadcasts.c.id == broadcast_id)
            .where(admin_broadcasts.c.status.in_((AdminBroadcastStatus.scheduled, AdminBroadcastStatus.sending)))
            .values(
                status=AdminBroadcastStatus.completed,
                completed_at=now,
                updated_at=now,
            )
            .returning(admin_broadcasts.c.id)
        )
    return result.scalar_one_or_none() is not None


async def _update_scheduled_broadcast(broadcast_id: int, **values: object) -> bool:
    async with get_connection() as conn:
        result = await conn.execute(
            update(admin_broadcasts)
            .where(admin_broadcasts.c.id == broadcast_id)
            .where(admin_broadcasts.c.status == AdminBroadcastStatus.scheduled)
            .values(**values, updated_at=datetime.now(UTC))
            .returning(admin_broadcasts.c.id)
        )
    return result.scalar_one_or_none() is not None


def _admin_broadcast_from_row(row: Row[Any]) -> AdminBroadcast:
    return AdminBroadcast(
        id=row.id,
        created_by_user_id=row.created_by_user_id,
        source_chat_id=row.source_chat_id,
        source_message_id=row.source_message_id,
        scheduled_local_at=row.scheduled_local_at,
        timezone_offset_minutes=row.timezone_offset_minutes,
        status=AdminBroadcastStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        deleted_at=row.deleted_at,
    )


def _admin_broadcast_delivery_from_row(row: Row[Any]) -> AdminBroadcastDelivery:
    return AdminBroadcastDelivery(
        id=row.id,
        broadcast_id=row.broadcast_id,
        user_id=row.user_id,
        status=AdminBroadcastDeliveryStatus(row.status),
        attempted_at=row.attempted_at,
        finished_at=row.finished_at,
        error=row.error,
    )
