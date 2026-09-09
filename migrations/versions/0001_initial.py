"""Initial publication schema."""
from alembic import op
import sqlalchemy as sa
revision = '0001'
down_revision = None

def upgrade():
    timestamps = lambda: [sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False)]
    op.create_table('subscribers', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('email', sa.String(320), nullable=False, unique=True), sa.Column('status', sa.String(), nullable=False), sa.Column('resend_contact_id', sa.String()), sa.Column('confirmation_token_hash', sa.String(), unique=True), sa.Column('confirmation_expires_at', sa.DateTime()), sa.Column('confirmed_at', sa.DateTime()), *timestamps())
    op.create_table('issues', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('slug', sa.String(), nullable=False, unique=True), sa.Column('title', sa.String(), nullable=False), sa.Column('subject', sa.String(), nullable=False), sa.Column('intro', sa.Text(), nullable=False), sa.Column('status', sa.String(), nullable=False), sa.Column('published_at', sa.DateTime()), sa.Column('resend_broadcast_id', sa.String()), sa.Column('delivery_state', sa.String(), nullable=False), sa.Column('newsletter_html', sa.Text()), sa.Column('newsletter_text', sa.Text()), *timestamps())
    op.create_index('ix_issues_status', 'issues', ['status'])
    op.create_table('stories', sa.Column('id', sa.Integer(), primary_key=True), sa.Column('issue_id', sa.Integer(), sa.ForeignKey('issues.id'), nullable=False), sa.Column('slug', sa.String(), nullable=False, unique=True), *[sa.Column(name, sa.Text(), nullable=False) for name in ('title','byte','summary','why_it_matters','source_name','source_url','source_type')], sa.Column('sort_order', sa.Integer(), nullable=False), sa.Column('published_at', sa.DateTime()), *timestamps())
    op.create_index('ix_stories_issue_id', 'stories', ['issue_id'])
    op.create_table('webhook_receipts', sa.Column('id', sa.String(), primary_key=True), sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('rate_limits', sa.Column('key', sa.String(), primary_key=True), sa.Column('count', sa.Integer(), nullable=False), sa.Column('expires', sa.Integer(), nullable=False))

def downgrade():
    for table in ('rate_limits', 'webhook_receipts', 'stories', 'issues', 'subscribers'):
        op.drop_table(table)
