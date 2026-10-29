from alembic import context
from sqlalchemy import create_engine

from config.settings import settings


def migrate(connection):
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run():
    url = settings.database_url.get_secret_value().replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url)
    with engine.connect() as connection:
        migrate(connection)
    engine.dispose()


run()
