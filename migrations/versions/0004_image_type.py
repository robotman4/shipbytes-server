"""Machine-readable image provenance."""
from alembic import op
import sqlalchemy as sa
revision = '0004'
down_revision = '0003'

def upgrade():
    op.add_column('issues', sa.Column('image_type', sa.String(), nullable=True))

def downgrade():
    with op.batch_alter_table('issues') as batch:
        batch.drop_column('image_type')
