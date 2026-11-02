"""
Модуль загрузки с YouTube Music
Специализированный экстрактор для music.youtube.com
"""

from .youtube_source import YouTubeDownloader


class YouTubeMusicDownloader(YouTubeDownloader):
    """Загрузчик треков и плейлистов с YouTube Music.

    Логика совпадает с YouTubeDownloader: отдельный класс нужен фабрике
    источников, чтобы не дублировать код и не чинить одни и те же баги дважды.
    """
