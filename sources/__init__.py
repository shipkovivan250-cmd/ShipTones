"""
Модуль источников музыки
Фабрика для создания загрузчиков разных источников
"""

from .ytmusic_source import YouTubeMusicDownloader
from .vk_source import VKMusicDownloader
from .yandex_source import YandexMusicDownloader


def get_downloader(source_type, parent_downloader):
    """
    Фабричный метод для получения загрузчика нужного источника
    
    Args:
        source_type: тип источника ("ytmusic", "vk", "yandex")
        parent_downloader: экземпляр основного Downloader для доступа к общим методам
    
    Returns:
        Экземпляр соответствующего загрузчика
    """
    downloaders = {
        "ytmusic": YouTubeMusicDownloader,
        "vk": VKMusicDownloader,
        "yandex": YandexMusicDownloader,
    }
    
    downloader_class = downloaders.get(source_type)
    if not downloader_class:
        raise ValueError(f"Неизвестный источник: {source_type}")
    
    return downloader_class(parent_downloader)


__all__ = ['get_downloader', 'YouTubeMusicDownloader', 
           'VKMusicDownloader', 'YandexMusicDownloader']
