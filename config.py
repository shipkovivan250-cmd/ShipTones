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
# как раньше, рядом со скриптами. Фолбэк: если BASE_DIR недоступна
# для записи (например, на Android в Chaquopy модули лежат в read-only
# APK), данные идут в $HOME/.shiptones.
# ============================================================
def _pick_data_dir() -> "Path":
    from pathlib import Path as _P
    candidates = []
    if getattr(sys, "frozen", False):
        if sys.platform == "win32":
            candidates.append(_P(os.environ.get("APPDATA", str(BASE_DIR))) / "ShipTones")
        elif sys.platform == "darwin":
            candidates.append(_P.home() / "Library" / "Application Support" / "ShipTones")
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME")
            candidates.append((_P(xdg) if xdg else _P.home() / ".config") / "shiptones")
    # Из исходников (desktop) — рядом со скриптами, как раньше; на Android BASE_DIR
    # внутри APK не записываема, поэтому кандидаты проверяются с фолбэком на $HOME.
    candidates.append(BASE_DIR)
    home = os.environ.get("HOME")
    if home:
        candidates.append(_P(home) / ".shiptones")
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".perm_check"
            probe.touch()
            probe.unlink()
            return d
        except Exception:
            continue
    return _P.cwd()


DATA_DIR = _pick_data_dir()
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
# ANDROID: NATIVE LIB DIR + БАНДЛОВЫЙ JS-РАНТАЙМ ДЛЯ YT-DLP
# yt-dlp >= 2025.10 требует внешний JS-рантайм (deno/node/quickjs/bun)
# для извлечения YouTube: без него доступны только jsless-клиенты,
# решение подписей (sig/n) падает и скачивание на Android не работает.
# На Android нет deno/node, поэтому в APK запасён лёгкий qjs
# (QuickJS-ng ~2 МБ) под именем libqjs.so:
#   1) исполняемые файлы на Android могут лежать ТОЛЬКО в
#      nativeLibraryDir — поэтому бинарь пакуется как native-библиотека
#      (jniLibs + useLegacyPackaging, распаковка с правом exec);
#   2) yt-dlp проверяет рантайм запуском `qjs --help` и вызывает
#      `qjs --script file.js` — QuickJS-ng 0.12.0 поддерживает оба.
# На десктопе хелпер возвращает {} — поведение не меняется.
# ============================================================
def android_native_library_dir():
    """Каталог распакованных native-библиотек приложения (только Chaquopy)."""
    try:
        from com.chaquo.python.android import AndroidPlatform  # только Chaquopy
        info = AndroidPlatform.getApplication().getApplicationInfo()
        lib_dir = info.nativeLibraryDir
        return str(lib_dir) if lib_dir else None
    except Exception:
        return None


def js_runtime_ydl_opts() -> dict:
    """Доп. опции YoutubeDL для внешнего JS-рантайма (yt-dlp >= 2025.10).

    Проверяем только наличие файла: бит исполнения на extracted-библиотеках
    варьируется от устройства к устройству, а фактический запуск всё равно
    проверяет мобильная диагностика (mobile.backend._diagnose_js_runtime).
    """
    lib_dir = android_native_library_dir()
    if not lib_dir:
        return {}
    qjs_path = os.path.join(lib_dir, "libqjs.so")
    if os.path.isfile(qjs_path):
        return {"js_runtimes": {"quickjs": {"path": qjs_path}}}
    return {}


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
APP_VERSION = "1.2.10"
GITHUB_REPO = "shipkovivan250-cmd/ShipTones"

# ============================================================
# ИСТОЧНИКИ МУЗЫКИ (фишка 11/12)
# color — фон активной кнопки, fg — цвет текста на ней
# in_dev — источник объявлен, но реальная загрузка ещё не реализована
# (см. downloader.py: каждый источник — отдельный изолированный метод,
# чтобы недоделанные VK/Яндекс не могли сломать рабочий YouTube Music)
# ============================================================
SOURCES = {
    "ytmusic": {"name": "YouTube Music", "icon": "🎵", "color": "#FF0000", "fg": "#ffffff",
                "url_patterns": ["music.youtube.com", "youtube.com", "youtu.be"], "in_dev": False},
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
    return "ytmusic"  # По умолчанию YouTube Music

# ============================================================
# НАСТРОЙКИ
# ============================================================
DEFAULT_SETTINGS = {
    "quality": "256",
    "format": "aac",  # mp3, aac, flac, opus
    "download_dir": "",
    "source": "ytmusic",
    # ФИКС СКОРОСТЫ: дефолт поднят с 3 до 4 — больше параллельных скачиваний
    # плейлиста = быстрее общий цикл «скачать весь плейлист».
    "max_workers": 4,
    "smart_dedup": True,           # фишка 6
    "auto_categorize": False,      # фишка 8
    "extended_tags": True,         # фишка 9
    "auto_update_ytdlp": True,     # фишка 14
    "normalize_volume": False,     # нормализация -14 LUFS как YouTube Music (по умолчанию выкл для чистого качества)
    "bass_boost": False,           # бас-буст 60 Гц +2 dB
    "compression": False,          # компрессия динамики
    "remove_silence": False,       # удаление тишины
    "export_to_downloads": True,   # Android: копировать треки в системную папку Загрузки/ShipTones (MediaStore)
}
SETTINGS_FILE = DATA_DIR / "settings.json"

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            merged = {**DEFAULT_SETTINGS, **loaded}
            if merged.get("source") in ("youtube", "yt"):
                merged["source"] = "ytmusic"
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save settings")