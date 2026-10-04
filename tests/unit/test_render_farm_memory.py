from types import SimpleNamespace

import pytest

from services.render_farm import members


class _Bot:
    def __init__(self, status="member", fail=False):
        self.status = status
        self.fail = fail
        self.asked = 0

    async def get_chat_member(self, chat_id, telegram_id):
        self.asked += 1
        if self.fail:
            raise RuntimeError("telegram is down")
        return SimpleNamespace(status=self.status)


@pytest.mark.asyncio
async def test_a_member_is_asked_about_once_in_a_while_not_on_every_request():
    bot = _Bot()
    assert [await members.is_member(bot, -100, 7) for _ in range(5)] == [True] * 5
    assert bot.asked == 1


@pytest.mark.asyncio
async def test_someone_who_left_is_remembered_too_so_a_loop_over_groups_stays_cheap():
    bot = _Bot(status="left")
    assert [await members.is_member(bot, -100, 7) for _ in range(3)] == [False] * 3
    assert bot.asked == 1


@pytest.mark.asyncio
async def test_a_telegram_failure_is_not_remembered_and_the_next_ask_goes_through():
    broken = _Bot(fail=True)
    assert await members.is_member(broken, -100, 7) is False
    assert await members.is_member(_Bot(), -100, 7) is True
    assert broken.asked == 1


@pytest.mark.asyncio
async def test_the_answer_is_per_chat_and_per_person():
    bot = _Bot()
    await members.is_member(bot, -100, 7)
    await members.is_member(bot, -100, 8)
    await members.is_member(bot, -200, 7)
    assert bot.asked == 3


@pytest.mark.asyncio
async def test_forgetting_makes_the_next_ask_real():
    bot = _Bot()
    await members.is_member(bot, -100, 7)
    members.forget()
    await members.is_member(bot, -100, 7)
    assert bot.asked == 2
