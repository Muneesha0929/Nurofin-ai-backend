"""add_actual_completion_date

Revision ID: de529221ca51
Revises: 917228eda225
Create Date: 2026-08-24 11:56:41.912263

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'de529221ca51'
down_revision: Union[str, None] = '917228eda225'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('task', sa.Column('actual_completion_date', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('task', 'actual_completion_date')

