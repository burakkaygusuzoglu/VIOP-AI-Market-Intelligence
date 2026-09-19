"""Remember where a replay command was going (Phase 11 closeout).

A retried advance must finish the command it names rather than start it again.
That needs one fact the session did not store: the revealed count the command
was working towards. With it, a retry can tell a finished command from one that
stopped part way, and can complete exactly the remainder.

**Why a second migration rather than an edit to 0004.** 0004 had already been
applied - to the development database and inside the running container - and a
database stamped at a revision never re-runs it. Amending an applied migration
therefore leaves those databases without the column while Alembic reports them
up to date, which is precisely what happened when this change was first written
into 0004. Applied is history, even before release.

Nullable on purpose: sessions created before this migration have no command in
flight to describe, and a NULL reads as "no target recorded", which the service
treats as a completed command.

Revision ID: 0005_replay_command_target
Revises: 0004_replay
Create Date: Phase 11 closeout
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_replay_command_target"
down_revision: str | None = "0004_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "replay_sessions",
        sa.Column("command_target_revealed", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("replay_sessions", "command_target_revealed")
