"""Add community tables for vocab sharing

Revision ID: add_community_tables
Revises: add_has_english
Create Date: 2026-06-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_community_tables'
down_revision: Union[str, None] = 'add_has_english'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    def table_exists(name):
        result = conn.execute(sa.text(
            f"SELECT table_name FROM information_schema.tables WHERE table_name='{name}'"
        ))
        return result.fetchone() is not None

    def index_exists(name):
        result = conn.execute(sa.text(f"SELECT indexname FROM pg_indexes WHERE indexname='{name}'"))
        return result.fetchone() is not None

    def constraint_exists(name):
        result = conn.execute(sa.text(f"SELECT conname FROM pg_constraint WHERE conname='{name}'"))
        return result.fetchone() is not None

    # Create community_groups table
    if not table_exists('community_groups'):
        op.create_table(
            'community_groups',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('description', sa.String(1000), nullable=True),
            sa.Column('language', sa.String(10), nullable=False, server_default='ko'),
            sa.Column('is_public', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('invite_code', sa.String(10), nullable=False),
            sa.Column('creator_id', sa.Integer(), nullable=False),
            sa.Column('member_permission', sa.String(20), nullable=False, server_default='all'),
            sa.Column('member_count', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='CASCADE'),
        )
    if not index_exists('ix_community_groups_invite_code'):
        op.create_index('ix_community_groups_invite_code', 'community_groups', ['invite_code'], unique=True)
    if not index_exists('ix_community_groups_creator_id'):
        op.create_index('ix_community_groups_creator_id', 'community_groups', ['creator_id'], unique=False)

    # Create community_memberships table
    if not table_exists('community_memberships'):
        op.create_table(
            'community_memberships',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('group_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(20), nullable=False, server_default='member'),
            sa.Column('last_synced_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['group_id'], ['community_groups.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('user_id', 'group_id', name='uq_community_membership'),
        )
    if not index_exists('ix_community_memberships_user_id'):
        op.create_index('ix_community_memberships_user_id', 'community_memberships', ['user_id'], unique=False)
    if not index_exists('ix_community_memberships_group_id'):
        op.create_index('ix_community_memberships_group_id', 'community_memberships', ['group_id'], unique=False)

    # Create community_vocab_lists table
    if not table_exists('community_vocab_lists'):
        op.create_table(
            'community_vocab_lists',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('group_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(255), nullable=False),
            sa.Column('added_by', sa.Integer(), nullable=True),
            sa.Column('word_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['group_id'], ['community_groups.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='SET NULL'),
        )
    if not index_exists('ix_community_vocab_lists_group_id'):
        op.create_index('ix_community_vocab_lists_group_id', 'community_vocab_lists', ['group_id'], unique=False)

    # Create community_vocab_words table
    if not table_exists('community_vocab_words'):
        op.create_table(
            'community_vocab_words',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('list_id', sa.Integer(), nullable=False),
            sa.Column('word', sa.String(255), nullable=False),
            sa.Column('translation', sa.String(500), nullable=False),
            sa.Column('example', sa.String(1000), nullable=True),
            sa.Column('example_translation', sa.String(1000), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('added_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['list_id'], ['community_vocab_lists.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['added_by'], ['users.id'], ondelete='SET NULL'),
            sa.UniqueConstraint('list_id', 'word', name='uq_community_vocab_word'),
        )
    if not index_exists('ix_community_vocab_words_list_id'):
        op.create_index('ix_community_vocab_words_list_id', 'community_vocab_words', ['list_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_community_vocab_words_list_id', table_name='community_vocab_words')
    op.drop_table('community_vocab_words')

    op.drop_index('ix_community_vocab_lists_group_id', table_name='community_vocab_lists')
    op.drop_table('community_vocab_lists')

    op.drop_index('ix_community_memberships_group_id', table_name='community_memberships')
    op.drop_index('ix_community_memberships_user_id', table_name='community_memberships')
    op.drop_table('community_memberships')

    op.drop_index('ix_community_groups_creator_id', table_name='community_groups')
    op.drop_index('ix_community_groups_invite_code', table_name='community_groups')
    op.drop_table('community_groups')
