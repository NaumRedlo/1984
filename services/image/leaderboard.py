from io import BytesIO
from typing import Optional

from PIL import Image

from services.image.base import BaseCardRenderer

class LeaderboardCardGenerator(BaseCardRenderer):

    @staticmethod
    def _image_from_bytes(data: Optional[bytes]) -> Optional[Image.Image]:
        if not data:
            return None
        try:
            return Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            return None

leaderboard_gen = LeaderboardCardGenerator()
