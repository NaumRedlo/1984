from typing import Any, Optional

MESSAGES = {
    "INVALID_STATE": ("Сейчас это действие недоступно.", False),
    "VERSION_CONFLICT": ("Дуэль уже изменилась, обновите её состояние.", True),
    "NOT_PARTICIPANT": ("Вы не участвуете в этой дуэли.", False),
    "NOT_FOUND": ("Дуэль не найдена.", False),
    "ALREADY_BUSY": ("Вы или соперник уже в очереди или в другой дуэли.", True),
    "ALREADY_INVITED": ("Вызов этому игроку уже отправлен.", False),
    "SELF_CHALLENGE": ("Нельзя вызвать на дуэль самого себя.", False),
    "NOT_LINKED": ("Для дуэлей нужен привязанный аккаунт osu!.", False),
    "OPERATION_REUSED": ("Этот идентификатор операции уже занят другим действием.", False),
    "NOT_YOUR_TURN": ("Сейчас ход соперника.", False),
    "SLOT_UNAVAILABLE": ("Эта карта уже недоступна.", False),
    "POOL_UNAVAILABLE": ("Подходящего пула пока нет.", True),
    "MAP_REVISION_CHANGED": ("Карта изменилась после раскрытия пула.", False),
    "ROOM_CAPACITY_WAIT": ("Все комнаты заняты, дуэль ждёт свободного места.", True),
    "ROOM_CONTROL_REQUIRED": ("Боту нужны права судьи в этой комнате.", True),
    "EVIDENCE_PENDING": ("Результат ещё проверяется.", True),
    "INCOMPATIBLE_CLIENT": ("Эта версия Dossier не поддерживает дуэли.", False),
    "SERVICE_RECOVERING": ("Сервис дуэлей восстанавливается, попробуйте чуть позже.", True),
}

class DuelError(Exception):
    def __init__(self, code: str, snapshot: Optional[dict[str, Any]] = None):
        message, retryable = MESSAGES.get(code, ("Ошибка дуэли.", False))
        super().__init__(code)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.snapshot = snapshot

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "retryable": self.retryable, "duel": self.snapshot}
