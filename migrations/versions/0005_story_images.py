"""Story media and durable provider references, preserving existing issue media."""
from alembic import op
import sqlalchemy as sa
revision = '0005'
down_revision = '0004'
FIELDS = ('image_file', 'image_thumbnail', 'image_type', 'image_alt', 'image_credit', 'image_source_url', 'image_usage')

def upgrade():
    op.add_column('issues', sa.Column('image_reference', sa.Text(), nullable=True))
    op.add_column('stories', sa.Column('image_reference', sa.Text(), nullable=True))
    for name in FIELDS:
        op.add_column('stories', sa.Column(name, sa.String(), nullable=True))

def downgrade():
    with op.batch_alter_table('stories') as batch:
        for name in (*FIELDS, 'image_reference'):
            batch.drop_column(name)
    with op.batch_alter_table('issues') as batch:
        batch.drop_column('image_reference')
