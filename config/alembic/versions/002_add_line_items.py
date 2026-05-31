"""add line_items

Revision ID: 002
Revises: 001
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Use server_default to handle existing rows if any, 
    # though in this case the DB is fresh.
    op.add_column("pos_transactions", sa.Column("line_items", sa.JSON(), nullable=False, server_default='[]'))

def downgrade() -> None:
    op.drop_column("pos_transactions", "line_items")
