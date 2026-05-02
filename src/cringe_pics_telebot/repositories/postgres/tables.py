import sqlalchemy as sa

from ._metadata import _metadata


def _time_column(name: str) -> sa.Column:
    return sa.Column(name, sa.TIME(True), nullable=False, default=sa.text("now()"))


users = sa.Table(
    "users",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=False),
    _time_column("created_at"),
)


subscription_types = sa.Table(
    "subscription_types",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column("name", sa.VARCHAR, nullable=False, unique=True),
    sa.Column("time", sa.TIME(True), nullable=False),
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
