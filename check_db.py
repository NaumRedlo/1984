from config.settings import DATABASE_URL
print(f"DATABASE_URL: {DATABASE_URL}")

import asyncio
from sqlalchemy import text
from db.database import engine

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table';"))
        tables = result.fetchall()
        print(f"Tables in DB: {tables}")
        
        result = await conn.execute(text("SELECT * FROM users;"))
        users = result.fetchall()
        print(f"Users in DB: {users}")

asyncio.run(check())
