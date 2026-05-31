"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("store_id", sa.String(64), nullable=False),
        sa.Column("camera_id", sa.String(64), nullable=False),
        sa.Column("visitor_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zone_id", sa.String(64), nullable=True),
        sa.Column("dwell_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_staff", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_events_store_time", "events", ["store_id", "timestamp"])
    op.create_index("ix_events_visitor", "events", ["visitor_id"])
    op.create_index("ix_events_type_store_time", "events", ["event_type", "store_id", "timestamp"])

    op.create_table(
        "pos_transactions",
        sa.Column("transaction_id", sa.String(64), primary_key=True),
        sa.Column("store_id", sa.String(64), nullable=False),
        sa.Column("visitor_id", sa.String(64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("basket_value", sa.Float, nullable=False),
        sa.Column("items_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_pos_store_time", "pos_transactions", ["store_id", "timestamp"])


def downgrade() -> None:
    op.drop_index("ix_pos_store_time", table_name="pos_transactions")
    op.drop_table("pos_transactions")
    op.drop_index("ix_events_type_store_time", table_name="events")
    op.drop_index("ix_events_visitor", table_name="events")
    op.drop_index("ix_events_store_time", table_name="events")
    op.drop_table("events")
