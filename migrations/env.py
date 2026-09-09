from alembic import context
from shipbytes.config import settings
from shipbytes.db import Base, database
from shipbytes import models
engine, _ = database(settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
