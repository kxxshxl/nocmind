"""Initial NOCMind schema"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('username', sa.String(64), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False), sa.Column('role', sa.String(32), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('incidents',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('title', sa.String(255), nullable=False), sa.Column('region', sa.String(64), nullable=False),
        sa.Column('severity', sa.String(16), nullable=False), sa.Column('service', sa.String(128), nullable=False), sa.Column('status', sa.String(32), nullable=False),
        sa.Column('symptoms_json', sa.Text(), nullable=False), sa.Column('ground_truth_root_cause', sa.String(255), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('incident_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('incident_id', sa.String(64), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False), sa.Column('event_type', sa.String(64), nullable=False), sa.Column('message', sa.Text(), nullable=False), sa.Column('details_json', sa.Text(), nullable=False))
    op.create_table('documents',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('title', sa.String(255), nullable=False), sa.Column('kind', sa.String(64), nullable=False),
        sa.Column('service', sa.String(128), nullable=False), sa.Column('severity', sa.String(16), nullable=False), sa.Column('version', sa.String(32), nullable=False))
    op.create_table('document_chunks',
        sa.Column('id', sa.String(128), primary_key=True), sa.Column('document_id', sa.String(64), nullable=False), sa.Column('text', sa.Text(), nullable=False),
        sa.Column('section', sa.String(255), nullable=False), sa.Column('metadata_json', sa.Text(), nullable=False), sa.Column('embedding_json', sa.Text(), nullable=False))
    op.create_table('historical_incidents',
        sa.Column('id', sa.String(64), primary_key=True), sa.Column('timestamp', sa.DateTime(), nullable=False), sa.Column('region', sa.String(64), nullable=False),
        sa.Column('service', sa.String(128), nullable=False), sa.Column('severity', sa.String(16), nullable=False), sa.Column('symptoms_json', sa.Text(), nullable=False),
        sa.Column('root_cause', sa.String(255), nullable=False), sa.Column('resolution', sa.Text(), nullable=False))
    op.create_table('tool_calls',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('incident_id', sa.String(64), nullable=False),
        sa.Column('tool_name', sa.String(128), nullable=False), sa.Column('risk_level', sa.String(32), nullable=False), sa.Column('arguments_json', sa.Text(), nullable=False),
        sa.Column('result_json', sa.Text(), nullable=False), sa.Column('approved', sa.Boolean(), nullable=False), sa.Column('success', sa.Boolean(), nullable=False), sa.Column('timestamp', sa.DateTime(), nullable=False))
    op.create_table('remediation_actions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('incident_id', sa.String(64), nullable=False),
        sa.Column('action', sa.String(128), nullable=False), sa.Column('target', sa.String(255), nullable=False), sa.Column('risk_level', sa.String(32), nullable=False),
        sa.Column('reversible', sa.Boolean(), nullable=False), sa.Column('status', sa.String(32), nullable=False), sa.Column('details_json', sa.Text(), nullable=False),
        sa.Column('approved_by', sa.String(64), nullable=True), sa.Column('approved_at', sa.DateTime(), nullable=True))
    op.create_table('llm_requests',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('incident_id', sa.String(64), nullable=True), sa.Column('provider', sa.String(64), nullable=False),
        sa.Column('model', sa.String(128), nullable=False), sa.Column('prompt_version', sa.String(64), nullable=False), sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False), sa.Column('latency_ms', sa.Float(), nullable=False), sa.Column('status', sa.String(32), nullable=False))
    op.create_table('evaluation_runs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('name', sa.String(128), nullable=False), sa.Column('metrics_json', sa.Text(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('audit_events',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True), sa.Column('actor_id', sa.String(64), nullable=False), sa.Column('incident_id', sa.String(64), nullable=True),
        sa.Column('action', sa.String(128), nullable=False), sa.Column('details_json', sa.Text(), nullable=False), sa.Column('timestamp', sa.DateTime(), nullable=False))
    op.create_index('ix_incident_events_incident_id', 'incident_events', ['incident_id'])
    op.create_index('ix_tool_calls_incident_id', 'tool_calls', ['incident_id'])
    op.create_index('ix_remediation_actions_incident_id', 'remediation_actions', ['incident_id'])
    op.create_index('ix_llm_requests_incident_id', 'llm_requests', ['incident_id'])
    op.create_index('ix_evaluation_runs_id', 'evaluation_runs', ['id'])


def downgrade():
    for name in ['audit_events','evaluation_runs','llm_requests','remediation_actions','tool_calls','historical_incidents','document_chunks','documents','incident_events','incidents','users']:
        op.drop_table(name)
