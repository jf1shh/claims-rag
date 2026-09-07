"""Versioned source references; existing rows remain explicitly unversioned."""
from alembic import op

revision = '0002'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('ALTER TABLE documents ADD COLUMN storage_key text')
    op.execute('ALTER TABLE documents ADD COLUMN document_version text')


def downgrade():
    op.execute('ALTER TABLE documents DROP COLUMN document_version')
    op.execute('ALTER TABLE documents DROP COLUMN storage_key')
