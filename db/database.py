# db/database.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config.settings import DATABASE_URL

# Creating an asynchronous engine
engine = create_async_engine(DATABASE_URL, echo=False) # echo=True for SQL logs

# Creating a session factory
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session

# Export the engine so that main.py can use it
__all__ = ["engine", "AsyncSessionLocal", "get_db_session"]


