"""add channel_webhook_url to chat_sessions and fix group session is_group flag

Revision ID: 0c7477c8beb4
Revises:
Create Date: 2026-04-10

Fixes:
- DingTalk group session webhook URL is now persisted across backend restarts.
- Historical chat_sessions with external_conv_id LIKE 'dingtalk_group_%' had
  is_group=FALSE due to a creation-time bug; this migration corrects them.
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0c7477c8beb4"
down_revision = "d9cbd43b62e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add channel_webhook_url column to chat_sessions
    op.add_column(
        "chat_sessions",
        sa.Column("channel_webhook_url", sa.String(500), nullable=True),
    )

    # 2. Fix historical data: DingTalk group sessions incorrectly stored as is_group=FALSE
    op.execute(
        """
        UPDATE chat_sessions
        SET is_group = TRUE
        WHERE external_conv_id LIKE 'dingtalk_group_%'
          AND is_group = FALSE
        """
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "channel_webhook_url")
    # NOTE: downgrade does NOT revert the is_group fix — that data change is correct.
