import json
import logging
import os
import sys
from pathlib import Path

# ============================================================
# БАЗОВАЯ ПАПКА (работает в .py и в .exe)
# ============================================================
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# ============================================================
# ПАПКА ДЛЯ ДАННЫХ (база, лог, настройки)
# В exe — %APPDATA%\ShipTones, чтобы не мусорить рядом с exe
# (например, на рабочем столе). При запуске из исходников —
# как раньше, рядом со скриптами.
# ============================================================
if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("APPDATA", BASE_DIR)) / "ShipTones"
else:
    DATA_DIR = BASE_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(DATA_DIR / "shiptones.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ShipTones")

# ============================================================
# ЗАВИСИМОСТИ
# ============================================================
try:
    from mutagen.id3 import ID3, TIT2, TPE1, APIC, TALB, TRCK, TCON, TDRC, TXXX
    from mutagen.mp3 import MP3
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False
    logger.warning("mutagen не установлен: pip install mutagen")

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow не установлен: pip install Pillow")

# ============================================================
# ЕДИНАЯ ЦВЕТОВАЯ ПАЛИТРА
# ============================================================
C_BG     = "#0a0a0a"
C_PANEL  = "#121212"
C_CARD   = "#181818"
C_CARD2  = "#1f1f1f"
C_BORDER = "#2a2a2a"
C_TEXT   = "#ffffff"
C_TEXT2  = "#a7a7a7"
C_ACCENT = "#3b82f6"

# ============================================================
# ВЕРСИЯ ПРИЛОЖЕНИЯ И РЕПОЗИТОРИЙ (для авто-обновления)
# ФИКС: при выпуске новой версии — подними это число И запушь git-тег
# с тем же номером (v1.2.0 и т.п.), иначе автообновление не сработает
# и/или будет бесконечно предлагать "обновиться" на саму себя.
# ============================================================
APP_VERSION = "1.1.1"
GITHUB_REPO = "shipkovivan250-cmd/ShipTones"

# ============================================================
# ИСТОЧНИКИ МУЗЫКИ (фишка 11/12)
# color — фон активной кнопки, fg — цвет текста на ней
# in_dev — источник объявлен, но реальная загрузка ещё не реализована
# (см. downloader.py: каждый источник — отдельный изолированный метод,
# чтобы недоделанные VK/Яндекс не могли сломать рабочий YouTube)
# ============================================================
SOURCES = {
    "youtube": {"name": "YouTube", "icon": "▶", "color": "#FF0000", "fg": "#ffffff",
                "url_patterns": ["youtube.com", "youtu.be", "music.youtube.com"], "in_dev": False},
    "vk": {"name": "VK Музыка", "icon": "●", "color": "#4680C2", "fg": "#ffffff",
           "url_patterns": ["vk.com", "m.vk.com"], "in_dev": True},
    "yandex": {"name": "Яндекс Музыка", "icon": "◉", "color": "#FFCC00", "fg": "#000000",
               "url_patterns": ["music.yandex.ru", "music.yandex.com"], "in_dev": True},
}

def detect_source(url):
    u = url.lower()
    for key, src in SOURCES.items():
        for p in src["url_patterns"]:
            if p in u:
                return key
    return "youtube"

# ============================================================
# НАСТРОЙКИ
# ============================================================
DEFAULT_SETTINGS = {
    "quality": "192",
    "download_dir": "",
    "source": "youtube",
    "max_workers": 3,
    "smart_dedup": True,           # фишка 6
    "auto_categorize": False,      # фишка 8
    "extended_tags": True,         # фишка 9
    "auto_update_ytdlp": True,     # фишка 14
}
SETTINGS_FILE = DATA_DIR / "settings.json"

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save settings")