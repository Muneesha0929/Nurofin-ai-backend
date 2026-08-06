"""Merge multiple heads

Revision ID: 4112356551bf
Revises: d2e3f4a5b6c7, f938dc292fb0
Create Date: 2026-08-05 10:59:26.654628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4112356551bf'
down_revision: Union[str, None] = ('d2e3f4a5b6c7', 'f938dc292fb0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
