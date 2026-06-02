"""Single source of truth for the admin panel (bot.handlers.admin.panel).

Every admin command is described once here; the panel UI is rendered from
this registry. Buckets are derived from the fields:

  * executor set       → read-only, no-arg, text-output commands get a
                         "▶️ Выполнить здесь" button (runs in the DM).
  * args is not None    → command needs arguments → copy-the-text hint.
  * where in {group,topic} → must be run elsewhere → "где выполнять" hint.
  * destructive         → ⚠️ note; never executed from the panel (the command
                         has its own confirm flow when typed manually).

Executors are tiny lazy wrappers so importing this module never drags in the
handler modules at import time (avoids import-order fragility).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Optional

Where = Literal["dm", "group", "topic", "any"]


@dataclass(frozen=True)
class CommandSpec:
    trigger: str
    label: str
    desc: str
    args: Optional[str] = None
    where: Where = "any"
    destructive: bool = False
    executor: Optional[Callable[[], Awaitable[str]]] = None


@dataclass(frozen=True)
class Category:
    key: str          # short, used in callback_data (ap:c:<key>)
    icon: str
    title: str
    commands: tuple[CommandSpec, ...]


# ── lazy executors for the safe read-only commands ──────────────────────────

async def _exec_poolhealth() -> str:
    from bot.handlers.admin.pool_wipe import build_poolhealth_report
    return await build_poolhealth_report()


async def _exec_importqueue() -> str:
    from bot.handlers.admin.duel_pool import build_import_queue_report
    return await build_import_queue_report()


async def _exec_hpspoolstats() -> str:
    from bot.handlers.admin.hps_pool import build_hps_pool_stats_report
    return await build_hps_pool_stats_report()


# ── the registry ────────────────────────────────────────────────────────────

CATEGORIES: tuple[Category, ...] = (
    Category("bounty", "🎯", "Баунти", (
        CommandSpec("bountycreate", "Создать баунти",
                    "Мастер создания баунти (пошагово)."),
        CommandSpec("bountyedit", "Редактировать",
                    "Изменить поля баунти.", args="<id>"),
        CommandSpec("bountyclose", "Закрыть",
                    "Закрыть активный баунти.", args="<id>"),
        CommandSpec("bountydelete", "Удалить",
                    "Удалить баунти.", args="<id>", destructive=True),
        CommandSpec("sendweekly", "Недельный дайджест",
                    "Отправить дайджест в настроенный чат."),
        CommandSpec("review", "Ревью сабмишенов",
                    "Открыть очередь ревью сабмишенов."),
        CommandSpec("reviewselect", "Открыть сабмишен",
                    "Открыть конкретный сабмишен на ревью.", args="<id>"),
    )),
    Category("duelpool", "⚙️", "DUEL-пул", (
        CommandSpec("duelpool", "Список пула",
                    "Показать DUEL map pool (постранично).", args="[стр]"),
        CommandSpec("dueladdmap", "Добавить карту",
                    "Добавить карту в DUEL-пул по beatmap_id.", args="<beatmap_id>"),
        CommandSpec("duelremovemap", "Убрать карту",
                    "Отключить карту в пуле.", args="<beatmap_id>"),
        CommandSpec("duelenable", "Включить карту",
                    "Снова включить отключённую карту.", args="<beatmap_id>"),
        CommandSpec("duelbroken", "Битые карты",
                    "Список битых/отключённых карт.", args="[стр]"),
        CommandSpec("duelrefresh", "Обновить пул",
                    "Обновить состояние пула карт."),
        CommandSpec("duelreset", "Сброс рейтингов",
                    "Сбросить DUEL-рейтинг всех игроков.",
                    args="[casual|ranked|all] [pp|flat]", destructive=True),
        CommandSpec("regenpool", "Регенерация пула",
                    "Пересоздать недельный bounty-пул.", destructive=True),
        CommandSpec("poolhealth", "Здоровье пула",
                    "Сводка состояния DUEL-пула.", executor=_exec_poolhealth),
    )),
    Category("duelmgmt", "🥊", "DUEL — управление", (
        CommandSpec("dueldiag", "Диагностика пула",
                    "Снимок состояния DUEL-пула (read-only)."),
        CommandSpec("closeduel", "Закрыть дуэль",
                    "Принудительно закрыть конкретную дуэль.",
                    args="<id>", destructive=True),
        CommandSpec("closeallduels", "Закрыть все дуэли",
                    "Закрыть все активные дуэли.",
                    args="[active|stuck|all]", destructive=True),
    )),
    Category("hpspool", "🎵", "HPS-пул", (
        CommandSpec("hpspoollist", "Список пула",
                    "Показать HPS map pool (постранично).", args="[стр]"),
        CommandSpec("hpspoolstats", "Статистика",
                    "Распределение HPS-пула.", executor=_exec_hpspoolstats),
        CommandSpec("hpsaddmap", "Добавить карту",
                    "Профилировать и добавить в hps_map_pool.",
                    args="<beatmap_id>"),
        CommandSpec("hpsdelmap", "Убрать карту",
                    "Soft-delete записи (enabled=0).", args="<beatmap_id>"),
        CommandSpec("hpsrefreshmap", "Обновить карту",
                    "Перетянуть метаданные + перепрофилировать.",
                    args="<beatmap_id>"),
    )),
    Category("wipe", "🗑", "Очистка пулов", (
        CommandSpec("poolwipe", "Очистить bounty-пул",
                    "Закрыть активный пул + его авто-баунти.",
                    destructive=True),
        CommandSpec("poolwipeduel", "Очистить DUEL-пул",
                    "Очистить DUEL map pool.", destructive=True),
        CommandSpec("poolwipehps", "Очистить HPS-пул",
                    "Очистить HPS map pool.", destructive=True),
    )),
    Category("users", "👤", "Юзеры / сезон", (
        CommandSpec("userslist", "Список юзеров",
                    "Все зарегистрированные, по last_seen."),
        CommandSpec("whois", "Кто это",
                    "Инфо о юзере по User.id или telegram_id.", args="<id>"),
        CommandSpec("purgeuser", "Удалить юзера",
                    "Каскадно удалить юзера и его данные.",
                    args="<id>", destructive=True),
        CommandSpec("recalcranks", "Пересчёт рангов",
                    "Пересчитать ранги всех игроков."),
        CommandSpec("seasonstart", "Старт сезона",
                    "Завершить текущий сезон и начать новый.",
                    destructive=True),
        CommandSpec("seasons", "Список сезонов",
                    "Все сезоны и их статус."),
        CommandSpec("seasonvoid", "Аннулировать сезон",
                    "Удалить сезон и его снапшоты из БД.",
                    args="<номер>", destructive=True),
        CommandSpec("hpwipe", "Вайп HP",
                    "Обнулить HPS-очки всех игроков.",
                    destructive=True),
        CommandSpec("notifyrelink", "Просьба перепривязки",
                    "DM юзеру с просьбой перепривязать osu!.", args="<id>"),
    )),
    Category("chats", "🔔", "Чаты / топики", (
        CommandSpec("setbountychat", "Чат баунти",
                    "Куда слать результаты и напоминания баунти.",
                    where="topic"),
        CommandSpec("setweeklychat", "Чат дайджеста",
                    "Куда слать недельный дайджест.", where="topic"),
        CommandSpec("setduelnotifychat", "Чат DUEL-дивизионов",
                    "Куда слать уведомления о смене дивизиона.",
                    where="topic"),
        CommandSpec("whereami", "Где я",
                    "Показать chat_id и message_thread_id.", where="group"),
    )),
    Category("import", "📥", "Импорт", (
        CommandSpec("import", "Импорт карт",
                    "По ссылке (Drive/MediaFire/GoFile/.zip/.7z) или вложением.",
                    args="<ссылка|файл>"),
        CommandSpec("importqueue", "Очередь импорта",
                    "Показать очередь импорта.", executor=_exec_importqueue),
    )),
)


def find_category(key: str) -> Optional[Category]:
    return next((c for c in CATEGORIES if c.key == key), None)


def find_command(trigger: str) -> Optional[CommandSpec]:
    for cat in CATEGORIES:
        for cmd in cat.commands:
            if cmd.trigger == trigger:
                return cmd
    return None


def all_commands() -> tuple[CommandSpec, ...]:
    return tuple(cmd for cat in CATEGORIES for cmd in cat.commands)
