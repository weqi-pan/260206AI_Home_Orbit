"""认证与会话加固：账号令牌版本号、刷新会话表、登录尝试表。

`token_version` 让「改密 / 管理员重置 / 禁用账号」能一次性作废该账号所有在途
访问令牌；`refresh_sessions` 记录每个 refresh JWT 的 jti，用于轮换与重放断族；
`login_attempts` 支撑限流，只存 `sha256(scope:identifier)`，不落用户名明文。
"""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0017"
down_revision = "20260828_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_id", sa.String(64), nullable=False),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(36), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
        sa.Column("replaced_by_token_id", sa.String(64)),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("user_agent", sa.String(200)),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_refresh_sessions_token_id", "refresh_sessions", ["token_id"], unique=True)
    op.create_index("ix_refresh_sessions_family_id", "refresh_sessions", ["family_id"])
    op.create_index("ix_refresh_sessions_account_id", "refresh_sessions", ["account_id"])
    op.create_index("ix_refresh_sessions_account_expires", "refresh_sessions", ["account_id", "expires_at"])

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_login_attempts_key_hash", "login_attempts", ["key_hash"])
    op.create_index("ix_login_attempts_key_created", "login_attempts", ["key_hash", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_login_attempts_key_created", table_name="login_attempts")
    op.drop_index("ix_login_attempts_key_hash", table_name="login_attempts")
    op.drop_table("login_attempts")

    op.drop_index("ix_refresh_sessions_account_expires", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_account_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_family_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_token_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")

    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("token_version")
