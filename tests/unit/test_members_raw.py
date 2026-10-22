import pytest
from aiogram.exceptions import ClientDecodeError

from services.render_farm import members

class Refusing:
    def __init__(self, data):
        self.data = data

    async def get_chat_member(self, chat_id, telegram_id):
        raise ClientDecodeError("Failed to deserialize object", ValueError("unknown field"), self.data)

@pytest.mark.asyncio
@pytest.mark.parametrize("data, inside", [
    ({"ok": True, "result": {"status": "administrator", "user": {"id": 1}}}, True),
    ('{"ok": true, "result": {"status": "member"}}', True),
    ({"ok": True, "result": {"status": "left"}}, False),
    ("not json", False),
])
async def test_a_member_the_library_cannot_read_is_judged_by_the_raw_status(data, inside):
    members.forget()
    assert await members.is_member(Refusing(data), -100, 7) is inside
