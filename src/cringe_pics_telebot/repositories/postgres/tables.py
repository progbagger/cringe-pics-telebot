import sqlalchemy as sa

from ._metadata import _metadata


def _time_column(name: str) -> sa.Column:
    return sa.Column(name, sa.TIME(True), nullable=False, default=sa.text("now()"))


users = sa.Table(
    "users",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=False),
    sa.Column(
        "timezone_offset_minutes",
        sa.SMALLINT,
        nullable=False,
        server_default=sa.text("420"),
    ),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    _time_column("created_at"),
    sa.CheckConstraint(
        "timezone_offset_minutes BETWEEN -720 AND 840",
        name="users_timezone_offset_minutes_range",
    ),
)


administrators = sa.Table(
    "administrators",
    _metadata,
    sa.Column("user_id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)


admin_broadcasts = sa.Table(
    "admin_broadcasts",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column("created_by_user_id", sa.BIGINT, nullable=False),
    sa.Column("source_chat_id", sa.BIGINT, nullable=False),
    sa.Column("source_message_id", sa.BIGINT, nullable=False),
    sa.Column("scheduled_local_at", sa.DateTime(timezone=False), nullable=False),
    sa.Column("timezone_offset_minutes", sa.SMALLINT, nullable=True),
    sa.Column("status", sa.VARCHAR(16), nullable=False, server_default="scheduled"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "timezone_offset_minutes IS NULL OR timezone_offset_minutes BETWEEN -720 AND 840",
        name="admin_broadcasts_timezone_offset_minutes_range",
    ),
    sa.CheckConstraint(
        "status IN ('scheduled', 'sending', 'completed', 'deleted')",
        name="admin_broadcasts_status_values",
    ),
    sa.Index("admin_broadcasts_active_schedule_idx", "status", "scheduled_local_at"),
)


admin_broadcast_deliveries = sa.Table(
    "admin_broadcast_deliveries",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column(
        "broadcast_id",
        sa.BIGINT,
        sa.ForeignKey(admin_broadcasts.c.id),
        nullable=False,
    ),
    sa.Column("user_id", sa.BIGINT, sa.ForeignKey(users.c.id), nullable=False),
    sa.Column("status", sa.VARCHAR(16), nullable=False, server_default="pending"),
    sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("error", sa.VARCHAR(1000), nullable=True),
    sa.CheckConstraint(
        "status IN ('pending', 'sent', 'failed')",
        name="admin_broadcast_deliveries_status_values",
    ),
    sa.UniqueConstraint(
        "broadcast_id",
        "user_id",
        name="admin_broadcast_deliveries_broadcast_user_key",
    ),
    sa.Index("admin_broadcast_deliveries_broadcast_status_idx", "broadcast_id", "status"),
)


subscription_types = sa.Table(
    "subscription_types",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column("name", sa.VARCHAR, nullable=False, unique=True),
    sa.Column("time", sa.TIME(False), nullable=False),
    sa.Column("s3_directory_path", sa.VARCHAR, nullable=False),
    _time_column("created_at"),
    _time_column("updated_at"),
)

subscriptions = sa.Table(
    "subscriptions",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column(
        "subscription_type_id",
        sa.BIGINT,
        sa.ForeignKey(subscription_types.c.id),
        nullable=False,
    ),
    _subscriptions_user_id := sa.Column(
        "user_id",
        sa.BIGINT,
        sa.ForeignKey(users.c.id),
        nullable=False,
    ),
    _time_column("created_at"),
    sa.Index("subscriptions_user_id_idx", _subscriptions_user_id),
)
