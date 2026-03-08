"""add query_logs table for RAGOps quality signals

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-08 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add query_logs table for implicit quality signals."""
    op.create_table('query_logs',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('repo_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('effective_question', sa.Text(), nullable=True),

        # Retrieval signals
        sa.Column('dense_result_count', sa.Integer(), nullable=False),
        sa.Column('sparse_result_count', sa.Integer(), nullable=False),
        sa.Column('merged_result_count', sa.Integer(), nullable=False),
        sa.Column('post_threshold_count', sa.Integer(), nullable=False),
        sa.Column('parent_chunks_added', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('top_dense_score', sa.Float(), nullable=True),
        sa.Column('top_rrf_score', sa.Float(), nullable=True),
        sa.Column('no_results', sa.Boolean(), nullable=False, server_default='false'),

        # Generation signals
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('citation_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('answer_length', sa.Integer(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False),
        sa.Column('completion_tokens', sa.Integer(), nullable=False),

        # Pipeline flags
        sa.Column('hyde_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('reranker_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('history_rewrite_used', sa.Boolean(), nullable=False, server_default='false'),

        # Latency breakdown (milliseconds)
        sa.Column('latency_history_rewrite_ms', sa.Integer(), nullable=True),
        sa.Column('latency_hyde_ms', sa.Integer(), nullable=True),
        sa.Column('latency_embedding_ms', sa.Integer(), nullable=True),
        sa.Column('latency_dense_search_ms', sa.Integer(), nullable=True),
        sa.Column('latency_sparse_search_ms', sa.Integer(), nullable=True),
        sa.Column('latency_rerank_ms', sa.Integer(), nullable=True),
        sa.Column('latency_llm_ms', sa.Integer(), nullable=True),
        sa.Column('latency_total_ms', sa.Integer(), nullable=False),

        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['repo_id'], ['repos.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_query_logs_repo', 'query_logs', ['repo_id'], unique=False)
    op.create_index('idx_query_logs_created', 'query_logs', ['created_at'], unique=False)


def downgrade() -> None:
    """Remove query_logs table."""
    op.drop_index('idx_query_logs_created', table_name='query_logs')
    op.drop_index('idx_query_logs_repo', table_name='query_logs')
    op.drop_table('query_logs')
