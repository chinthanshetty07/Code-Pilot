"""baseline

Revision ID: feb559afa513
Revises:
Create Date: 2026-09-09 13:56:51.297061

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "feb559afa513"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
