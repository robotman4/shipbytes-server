"""Content provenance for repository publications."""
from alembic import op
import sqlalchemy as sa
revision = '0002'
down_revision = '0001'

def upgrade():
    for name in ('source_filename', 'source_git_sha', 'source_content_hash'):
        op.add_column('issues', sa.Column(name, sa.String(), nullable=True))
    op.add_column('issues', sa.Column('publication_date', sa.DateTime(), nullable=True))

def downgrade():
    with op.batch_alter_table('issues') as batch:
        for name in ('source_filename', 'source_git_sha', 'source_content_hash', 'publication_date'):
            batch.drop_column(name)
