"""add chat_messages table

Revision ID: beaa0fe534fb
Revises:
Create Date: 2025-12-31 21:27:48.443981+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'beaa0fe534fb'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table('chat_messages',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('repo_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False, comment='References auth.users(id) in Supabase'),
        sa.Column('role', sa.String(length=20), nullable=False, comment='user or assistant'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Source citations for assistant messages'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name='valid_message_role'),
        sa.ForeignKeyConstraint(['repo_id'], ['repos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_messages_repo_created', 'chat_messages', ['repo_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_chat_messages_repo_id'), 'chat_messages', ['repo_id'], unique=False)
    op.create_index(op.f('ix_chat_messages_user_id'), 'chat_messages', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index(op.f('ix_chat_messages_user_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_repo_id'), table_name='chat_messages')
    op.drop_index('idx_messages_repo_created', table_name='chat_messages')
    op.drop_table('chat_messages')
