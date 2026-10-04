"""Persist incident AI investigation results"""
from alembic import op
import sqlalchemy as sa

revision = "0002_incident_ai_results"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("incidents", sa.Column("rca_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("incidents", sa.Column("remediation_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("incidents", sa.Column("final_report", sa.Text(), nullable=False, server_default=""))

def downgrade():
    op.drop_column("incidents", "final_report")
    op.drop_column("incidents", "remediation_json")
    op.drop_column("incidents", "rca_json")
