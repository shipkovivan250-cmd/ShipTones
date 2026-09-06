"""
Модуль загрузки с VK Музыки
Заглушка для будущей реализации
"""

from config import logger


class VKMusicDownloader:
    """Загрузчик треков с VK Музыки (в разработке)"""
    
    def __init__(self, parent_downloader):
        self.parent = parent_downloader
        self.settings = parent_downloader.settings
        self.ffmpeg_path = parent_downloader.ffmpeg_path
        
    def _log(self, msg):
        self.parent._log(msg)
    
    def download(self, url, mode, target_folder, quality):
        """Загрузка с VK Музыки - пока недоступна"""
        self._log("⚠ VK Музыка пока в разработке — загрузка недоступна.")
        self._log("ℹ️ Причина: у yt-dlp нет рабочего экстрактора для VK Музыки")
        self._log("   (только для обычного видео VK), а собственная реализация")
        self._log("   потребовала бы реверс-инжиниринга приватного API VK с авторизацией.")
        self.parent._finish(ok=False, error="Источник «VK Музыка» ещё не реализован")
