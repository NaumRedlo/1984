"""Buttons on the GPU watchdog's "server looks idle" DM prompt
(utils/cloud/gpu_power._prompt_admins_idle_server): confirm power-off, or
snooze the check for a while. Admin-only via the parent router's AdminFilter."""

from aiogram import Router, types, F

from utils.admin_check import AdminFilter
from utils.cloud import gpu_power
from utils.logger import get_logger

logger = get_logger("handlers.admin.gpu_watchdog")

router = Router(name="admin_gpu_watchdog")
router.callback_query.filter(AdminFilter())


@router.callback_query(F.data == "gpuwd:off")
async def cb_gpuwd_off(callback: types.CallbackQuery):
    if not await gpu_power._health_ok():
        try:
            await callback.message.edit_text("Сервер уже выключен.")
        except Exception:
            pass
        await callback.answer()
        return
    ok = await gpu_power.force_power_off("admin-confirmed watchdog")
    text = "✅ GPU-сервер выключен." if ok else "⚠️ Не получилось выключить — проверьте панель Intelion вручную."
    try:
        await callback.message.edit_text(text)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("gpuwd:snooze:"))
async def cb_gpuwd_snooze(callback: types.CallbackQuery):
    try:
        minutes = int(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        minutes = 30
    gpu_power.snooze_watchdog(minutes * 60)
    try:
        await callback.message.edit_text(f"🕐 Оставляю GPU-сервер включённым ещё {minutes} мин.")
    except Exception:
        pass
    await callback.answer()


__all__ = ["router"]
