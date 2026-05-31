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
    from bot.handlers.admin.bsk_pool import build_import_queue_report
    return await build_import_queue_report()


async def _exec_bskmlstats() -> str:
    from bot.handlers.admin.bsk_ml import build_bsk_ml_stats_report
    return await build_bsk_ml_stats_report()


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
    Category("bskpool", "⚙️", "BSK-пул", (
        CommandSpec("bskpool", "Список пула",
                    "Показать BSK map pool (постранично).", args="[стр]"),
        CommandSpec("bskaddmap", "Добавить карту",
                    "Скачать .osu и добавить в BSK-пул.", args="<beatmap_id>"),
        CommandSpec("bskremovemap", "Убрать карту",
                    "Отключить карту в пуле.", args="<beatmap_id>"),
        CommandSpec("bskenable", "Включить карту",
                    "Снова включить отключённую карту.", args="<beatmap_id>"),
        CommandSpec("bskbroken", "Битые карты",
                    "Список битых/отключённых карт.", args="[стр]"),
        CommandSpec("bskreanalyze", "Переанализ",
                    "Заново проанализировать карты пула."),
        CommandSpec("bskrecalc", "Пересчёт скиллов",
                    "Пересчитать skill-stars / map_type из фич."),
        CommandSpec("bskrefresh", "Обновить пул",
                    "Обновить состояние пула карт."),
        CommandSpec("bskreset", "Сброс рейтингов",
                    "Сбросить BSK-рейтинг всех игроков.",
                    args="[casual|ranked|all] [pp|flat]", destructive=True),
        CommandSpec("regenpool", "Регенерация пула",
                    "Пересоздать недельный bounty-пул.", destructive=True),
        CommandSpec("poolhealth", "Здоровье пула",
                    "Сводка состояния BSK-пула.", executor=_exec_poolhealth),
    )),
    Category("bskml", "🤖", "BSK ML", (
        CommandSpec("bskmlstats", "Статистика ML",
                    "История обучения BSK ML.", executor=_exec_bskmlstats),
        CommandSpec("bskmlmonitor", "Монитор обучения",
                    "Наблюдать за текущим обучением."),
        CommandSpec("bsktrainml", "Запустить обучение",
                    "Запустить обучение модели вручную."),
    )),
    Category("bsktest", "🥊", "BSK тесты / дуэли", (
        CommandSpec("bsktest", "Тест-дуэль",
                    "Старт тест-дуэли (оба игрока — ты).",
                    args="[casual|ranked]", where="group"),
        CommandSpec("bsktestroom", "IRC-комната",
                    "Создать IRC-комнату для активной тест-дуэли.",
                    where="group"),
        CommandSpec("bsktestround", "Симул. раунд",
                    "Симулировать раунд фейковыми скорами.",
                    args="[p1_pp p1_acc p2_pp p2_acc]", where="group"),
        CommandSpec("bsktestend", "Завершить тест",
                    "Отменить активную тест-дуэль.", where="group"),
        CommandSpec("bskcleantest", "Очистить тесты",
                    "Удалить завершённые/отменённые тест-дуэли.",
                    destructive=True),
        CommandSpec("bskdiag", "Диагностика пула",
                    "Снимок состояния BSK-пула (read-only)."),
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
        CommandSpec("poolwipebsk", "Очистить BSK-пул",
                    "Очистить BSK map pool.", destructive=True),
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
        CommandSpec("notifyrelink", "Просьба перепривязки",
                    "DM юзеру с просьбой перепривязать osu!.", args="<id>"),
    )),
    Category("chats", "🔔", "Чаты / топики", (
        CommandSpec("setbountychat", "Чат баунти",
                    "Куда слать результаты и напоминания баунти.",
                    where="topic"),
        CommandSpec("setweeklychat", "Чат дайджеста",
                    "Куда слать недельный дайджест.", where="topic"),
        CommandSpec("setbsknotifychat", "Чат BSK-дивизионов",
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
