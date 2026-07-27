"""merge

Revision ID: c813af6d8311
Revises: 9fa37d90047f, c7d8e9f0a1b2
Create Date: 2026-07-27 18:49:01.962745

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c813af6d8311'
down_revision: Union[str, None] = ('9fa37d90047f', 'c7d8e9f0a1b2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
