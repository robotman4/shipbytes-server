"""Persistent issue covers and attribution."""
from alembic import op
import sqlalchemy as sa
revision = '0003'
down_revision = '0002'
FIELDS = ('image_file', 'image_thumbnail', 'image_alt', 'image_credit', 'image_source_url', 'image_usage')

def upgrade():
    for name in FIELDS:
        op.add_column('issues', sa.Column(name, sa.String(), nullable=True))

def downgrade():
    with op.batch_alter_table('issues') as batch:
        for name in FIELDS:
            batch.drop_column(name)
