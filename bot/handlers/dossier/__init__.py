from aiogram import Router

from utils.render_access import RenderTesterFilter

from bot.handlers.dossier.handlers import router as _dossier_router

router = Router(name="dossier_gated")
router.message.filter(RenderTesterFilter())
router.callback_query.filter(RenderTesterFilter())

router.include_router(_dossier_router)

__all__ = ["router"]
