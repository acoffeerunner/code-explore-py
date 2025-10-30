"""add full-text search to chunks

Revision ID: a1b2c3d4e5f6
Revises: beaa0fe534fb
Create Date: 2026-03-08 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'beaa0fe534fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add tsvector column and GIN index for hybrid search."""
    op.execute(
        "ALTER TABLE chunks ADD COLUMN fts tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.create_index('idx_chunks_fts', 'chunks', ['fts'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    """Remove FTS column and index."""
    op.drop_index('idx_chunks_fts', table_name='chunks')
    op.execute("ALTER TABLE chunks DROP COLUMN fts")
