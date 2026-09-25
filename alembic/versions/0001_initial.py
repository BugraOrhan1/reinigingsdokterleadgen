"""Initial schema.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa

from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'companies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_name', sa.String(255), nullable=False),
        sa.Column('name_key', sa.String(255), nullable=False),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('website', sa.String(2048), nullable=False),
        sa.Column('industry', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=False),
        sa.Column('region', sa.String(100), nullable=False),
        sa.Column('address', sa.String(500), nullable=False),
        sa.Column('source', sa.String(100), nullable=False),
        sa.Column('source_url', sa.String(2048), nullable=False),
        sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('facts', sa.JSON(), nullable=False),
        sa.Column('analyzed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('name_key'),
        sa.UniqueConstraint('domain'),
    )
    op.create_index('ix_companies_company_name', 'companies', ['company_name'])

    op.create_table(
        'contacts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('email_source_url', sa.String(2048), nullable=False),
        sa.Column('contact_type', sa.String(50), nullable=False),
        sa.Column('confidence_score', sa.Integer(), nullable=False),
        sa.Column('verification_status', sa.String(30), nullable=False),
        sa.UniqueConstraint('email'),
    )
    op.create_index('ix_contacts_company_id', 'contacts', ['company_id'])

    op.create_table(
        'campaigns',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('industries', sa.JSON(), nullable=False),
        sa.Column('region', sa.String(100), nullable=False),
        sa.Column('city', sa.String(100), nullable=False),
        sa.Column('radius_km', sa.Integer(), nullable=False),
        sa.Column('daily_limit', sa.Integer(), nullable=False),
        sa.Column('min_score', sa.Integer(), nullable=False),
        sa.Column('service_override', sa.String(200), nullable=False),
        sa.Column('max_followups', sa.Integer(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('name'),
    )

    op.create_table(
        'campaign_leads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('contact_id', sa.Integer(), sa.ForeignKey('contacts.id'), nullable=True),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('service', sa.String(200), nullable=False),
        sa.Column('state', sa.String(30), nullable=False),
        sa.Column('followups_sent', sa.Integer(), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('campaign_id', 'company_id'),
    )
    op.create_index('ix_campaign_leads_campaign_id', 'campaign_leads', ['campaign_id'])
    op.create_index('ix_campaign_leads_company_id', 'campaign_leads', ['company_id'])

    op.create_table(
        'emails',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_lead_id', sa.Integer(), sa.ForeignKey('campaign_leads.id'), nullable=False),
        sa.Column('contact_id', sa.Integer(), sa.ForeignKey('contacts.id'), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('provider_id', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('campaign_lead_id', 'sequence'),
    )
    op.create_index('ix_emails_status_created', 'emails', ['status', 'created_at'])
    op.create_index('ix_emails_campaign_lead_id', 'emails', ['campaign_lead_id'])

    op.create_table(
        'email_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email_id', sa.Integer(), sa.ForeignKey('emails.id'), nullable=False),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('detail', sa.String(500), nullable=False),
    )
    op.create_index('ix_email_events_email_id', 'email_events', ['email_id'])

    op.create_table(
        'replies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('external_id', sa.String(255), nullable=False),
        sa.Column('contact_id', sa.Integer(), sa.ForeignKey('contacts.id'), nullable=True),
        sa.Column('email_id', sa.Integer(), sa.ForeignKey('emails.id'), nullable=True),
        sa.Column('classification', sa.String(30), nullable=False),
        sa.Column('snippet', sa.String(500), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('external_id'),
    )

    op.create_table(
        'suppression_list',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(320), nullable=True),
        sa.Column('domain', sa.String(255), nullable=True),
        sa.Column('reason', sa.String(100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('domain'),
    )

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('entity_type', sa.String(50), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('detail', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_audit_logs_action', 'audit_logs', ['action'])

    op.create_table(
        'settings',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
    )

    op.create_table(
        'service_mappings',
        sa.Column('industry', sa.String(100), primary_key=True),
        sa.Column('service', sa.String(200), nullable=False),
    )


def downgrade():
    for table in ('service_mappings', 'settings', 'audit_logs', 'suppression_list', 'replies', 'email_events', 'emails', 'campaign_leads', 'campaigns', 'contacts', 'companies'):
        op.drop_table(table)
