import asyncio
from db.database import engine
from sqlalchemy import text

async def fix():
    async with engine.begin() as c:
        result = await c.execute(text(
            "UPDATE bsk_ratings SET peak_mu = mu_aim + mu_speed + mu_acc + mu_cons "
            "WHERE mu_aim + mu_speed + mu_acc + mu_cons > peak_mu"
        ))
        print(f"Updated {result.rowcount} rows")

asyncio.run(fix())
