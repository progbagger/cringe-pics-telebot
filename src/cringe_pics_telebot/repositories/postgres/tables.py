import sqlalchemy as sa

_metadata = sa.MetaData()


users = sa.Table(
    "users",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=False),
    sa.Column("created_at", sa.TIMESTAMP(True), nullable=False),
)


subscription_types = sa.Table(
    "subscription_types",
    _metadata,
    sa.Column("id", sa.BIGINT, primary_key=True, nullable=False, autoincrement=True),
    sa.Column("name", sa.VARCHAR, nullable=False, unique=True),
    sa.Column("time", sa.TIME(True), nullable=False),
    sa.Column("s3_directory_path", sa.VARCHAR, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(True), nullable=False),
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
    sa.Column("created_at", sa.TIMESTAMP(True), nullable=False),
    sa.Index("subscriptions_user_id_idx", _subscriptions_user_id),
)
