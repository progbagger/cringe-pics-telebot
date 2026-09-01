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


admin_broadcast_recipients = sa.Table(
    "admin_broadcast_recipients",
    _metadata,
    sa.Column(
        "broadcast_id",
        sa.BIGINT,
        sa.ForeignKey(admin_broadcasts.c.id),
        primary_key=True,
        nullable=False,
    ),
    sa.Column(
        "user_id",
        sa.BIGINT,
        sa.ForeignKey(users.c.id),
        primary_key=True,
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Index("admin_broadcast_recipients_user_id_idx", "user_id"),
)


subscription_types = sa.Table(
    "subscription_types",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column("name", sa.VARCHAR, nullable=False, unique=True),
    sa.Column("time", sa.TIME(False), nullable=True),
    sa.Column("weekdays", sa.SMALLINT, nullable=False, server_default=sa.text("127")),
    sa.Column("s3_directory_path", sa.VARCHAR, nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column(
        "search_aliases",
        sa.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    ),
    _time_column("created_at"),
    _time_column("updated_at"),
    sa.CheckConstraint(
        "weekdays BETWEEN 1 AND 127",
        name="subscription_types_weekdays_valid",
    ),
)

category_media = sa.Table(
    "category_media",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column(
        "subscription_type_id",
        sa.BIGINT,
        sa.ForeignKey(subscription_types.c.id),
        nullable=False,
    ),
    sa.Column("source_path", sa.Text, nullable=False),
    sa.Column("source_revision", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("mime_type", sa.Text, nullable=False),
    sa.Column("telegram_media_type", sa.Text, nullable=False),
    sa.Column("telegram_file_id", sa.Text, nullable=True),
    sa.Column("telegram_file_unique_id", sa.Text, nullable=True),
    sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column(
        "status",
        sa.Text,
        sa.Computed(
            "CASE WHEN NOT is_active THEN 'inactive' WHEN telegram_file_id IS NULL THEN 'pending' ELSE 'ready' END",
            persisted=None,
        ),
    ),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("source_path <> ''", name="category_media_source_path_nonempty"),
    sa.CheckConstraint("source_revision <> ''", name="category_media_source_revision_nonempty"),
    sa.CheckConstraint(
        "telegram_media_type IN ('photo', 'animation')",
        name="category_media_telegram_media_type_values",
    ),
    sa.CheckConstraint(
        "(telegram_file_id IS NULL AND telegram_file_unique_id IS NULL AND materialized_at IS NULL) OR "
        "(telegram_file_id IS NOT NULL AND telegram_file_unique_id IS NOT NULL AND materialized_at IS NOT NULL)",
        name="category_media_materialization_consistent",
    ),
    sa.UniqueConstraint(
        "subscription_type_id",
        "source_path",
        name="category_media_subscription_type_source_path_key",
    ),
    sa.Index(
        "category_media_active_category_idx",
        "subscription_type_id",
        "is_active",
        "id",
    ),
)

user_media_cycle_states = sa.Table(
    "user_media_cycle_states",
    _metadata,
    sa.Column("user_id", sa.BIGINT, sa.ForeignKey(users.c.id, ondelete="CASCADE"), primary_key=True, nullable=False),
    sa.Column(
        "subscription_type_id",
        sa.BIGINT,
        sa.ForeignKey(subscription_types.c.id, ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    sa.Column(
        "last_media_id",
        sa.BIGINT,
        sa.ForeignKey(category_media.c.id, ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

user_media_cycle_entries = sa.Table(
    "user_media_cycle_entries",
    _metadata,
    sa.Column("user_id", sa.BIGINT, primary_key=True, nullable=False),
    sa.Column("subscription_type_id", sa.BIGINT, primary_key=True, nullable=False),
    sa.Column("media_id", sa.BIGINT, sa.ForeignKey(category_media.c.id, ondelete="CASCADE"), primary_key=True),
    sa.Column("reservation_token", sa.Text, nullable=True),
    sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("shown_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "(reservation_token IS NOT NULL AND reserved_until IS NOT NULL AND shown_at IS NULL) OR "
        "(reservation_token IS NULL AND reserved_until IS NULL AND shown_at IS NOT NULL)",
        name="user_media_cycle_entries_state_consistent",
    ),
    sa.ForeignKeyConstraint(
        ["user_id", "subscription_type_id"],
        [user_media_cycle_states.c.user_id, user_media_cycle_states.c.subscription_type_id],
        ondelete="CASCADE",
    ),
    sa.Index(
        "user_media_cycle_entries_reservation_token_key",
        "reservation_token",
        unique=True,
        postgresql_where=sa.text("reservation_token IS NOT NULL"),
    ),
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
