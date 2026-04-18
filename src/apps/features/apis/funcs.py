from lib.sandbox_api import SandboxAPI

from .db import get_db_conn

api = SandboxAPI()


@api.function
async def fetch_user_lists() -> list[dict]:
    """获取所有用户列表，返回 id、username、email、phone 字段。"""
    async with get_db_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, username, email, phone FROM accounts_user WHERE deleted_at IS NULL AND is_active = 1 limit 10"
            )
            return await cur.fetchall()


@api.function
async def fetch_user_info(user_id: int) -> dict | None:
    """根据 ID 获取用户详情，返回 id、username、email、phone、is_superuser、date_joined 字段。"""
    async with get_db_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, username, email, phone, is_superuser, date_joined FROM accounts_user WHERE id = %s AND deleted_at IS NULL",
                (user_id,),
            )
            return await cur.fetchone()
