"""
Модуль загрузки с Яндекс Музыки
Заглушка для будущей реализации
"""

from config import logger


class YandexMusicDownloader:
    """Загрузчик треков с Яндекс Музыки (в разработке)"""
    
    def __init__(self, parent_downloader):
        self.parent = parent_downloader
        self.settings = parent_downloader.settings
        self.ffmpeg_path = parent_downloader.ffmpeg_path
        
    def _log(self, msg):
        self.parent._log(msg)
    
    def download(self, url, mode, target_folder, quality):
        """Загрузка с Яндекс Музыки - пока недоступна"""
        self._log("⚠ Яндекс Музыка пока в разработке — загрузка недоступна.")
        self._log("ℹ️ Причина: у yt-dlp нет рабочего экстрактора для Яндекс Музыки,")
        self._log("   а собственная реализация потребовала бы реверс-инжиниринга")
        self._log("   приватного API Яндекса с авторизацией.")
        self.parent._finish(ok=False, error="Источник «Яндекс Музыка» ещё не реализован")
