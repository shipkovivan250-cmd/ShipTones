"""Mobile (Android) бэкенд ShipTones.

Тонкая обёртка над существующими :mod:`downloader`, :mod:`database` и
:mod:`config` для работы внутри Chaquopy на Android.

Не импортирует ``webview`` / ``tkinter`` и не использует десктоп-специфичные
API (диалоги выбора папки, ``os.startfile``, автообновление .exe, drag&drop).
Все пути загрузки и БД находятся внутри приватной директории приложения.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from config import APP_VERSION, SOURCES, detect_source, load_settings, save_settings, logger
from database import Database
from downloader import Downloader

try:  # find_ffmpeg может быть недоступен без установленного utils
    from utils import find_ffmpeg
except Exception:  # pragma: no cover
    find_ffmpeg = None  # type: ignore[assignment]

try:  # check_internet из utils (сетевая проверка для статусной пилюли)
    from utils import check_internet
except Exception:  # pragma: no cover
    check_internet = None  # type: ignore[assignment]

# Аудио-форматы, которые понимает кодек-карта youtube_source.YouTubeDownloader.
_AUDIO_FORMATS = ("mp3", "aac", "flac", "opus")


class Api:
    """Обёртка API для мобильного интерфейса (WebView → JS-мост → Python).

    Args:
        music_dir: папка для закачки аудио (на Android –
            ``getExternalFilesDir(DIRECTORY_MUSIC)``). Если не передана –
            используется домашняя папка пользователя (хост/тесты).
        ffmpeg_path: путь к ffmpeg-бинарнику/папке. На Android передаётся со
            стороны Kotlin (mobile-ffmpeg); на хосте — через ``find_ffmpeg()``.
        event_callback: колбэк ``(kind, payload)`` для событий Python → JS.
    """

    def __init__(self, music_dir: Optional[str] = None, ffmpeg_path: Optional[str] = None,
                 event_callback: Optional[Callable[[str, object], None]] = None):
        self._settings = load_settings()
        self.event_callback = event_callback
        self._downloader: Optional[Downloader] = None
        self._download_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._current_folder: Optional[str] = None

        # Директория загрузок — на Android приватная директория приложения.
        if music_dir:
            self.music_dir = Path(music_dir)
        else:
            self.music_dir = Path.home() / "ShipTonesMobile" / "Music"
        self.music_dir.mkdir(parents=True, exist_ok=True)

        # БД в приватной директории приложения (рядом с музыкой).
        self.db = Database(db_path=str(self.music_dir.parent / "shiptones.db"))

        # FFmpeg: на Android путь приходит из Kotlin (mobile-ffmpeg); на хосте —
        # автоматически через find_ffmpeg().
        if ffmpeg_path:
            self.ffmpeg_path = ffmpeg_path
        elif find_ffmpeg:
            self.ffmpeg_path = find_ffmpeg()
        else:
            self.ffmpeg_path = None
        logger.info(
            f"ShipTones Mobile Api: music_dir={self.music_dir} ffmpeg={self.ffmpeg_path}")

    # ---------------- источники / мета ----------------
    def get_sources(self):
        return {k: {"name": v["name"], "icon": v["icon"], "color": v["color"], "fg": v["fg"],
                    "in_dev": bool(v.get("in_dev"))}
                for k, v in SOURCES.items()}

    def detect_source(self, url):
        return detect_source(url)

    def get_app_version(self):
        return APP_VERSION

    def check_internet(self):
        if check_internet:
            try:
                return bool(check_internet())
            except Exception:
                return False
        return False

    # ---------------- настройки ----------------
    def settings(self, patch: Optional[dict] = None) -> dict:
        """Геттер/сеттер настроек. Без аргумента — вернуть текущие настройки."""
        if patch is None:
            return dict(self._settings)
        if not isinstance(patch, dict):
            return {"error": "settings must be an object"}
        settings = dict(self._settings)
        for key, value in patch.items():
            if key == "max_workers":
                try:
                    value = max(1, min(5, int(value)))
                except (TypeError, ValueError):
                    continue
            if key == "quality" and value not in (
                    "0", "96", "128", "160", "192", "256", "320"):
                continue
            if key == "format" and value not in ("mp3", "aac", "flac", "opus"):
                continue
            if key == "download_dir" and not isinstance(value, str):
                continue
            settings[key] = value
        self._settings = settings
        save_settings(self._settings)
        return {"ok": True}

    # ---------------- история / плейлисты ----------------
    def history(self, search=""):
        return [list(row) for row in self.db.get_history(search)]

    def playlists(self):
        return [list(row) for row in self.db.get_playlists()]

    def delete_playlist(self, playlist_id):
        self.db.delete_playlist(playlist_id)
        return {"ok": True}

    # ---------------- загрузка ----------------
    def _is_busy(self):
        return bool(self._download_thread and self._download_thread.is_alive())

    def _emit(self, kind, payload):
        if self.event_callback:
            try:
                self.event_callback(kind, payload)
            except Exception as e:  # pragma: no cover
                logger.error(f"event_callback failed: {e}")

    def start_download(self, url: str, fmt: str = "aac") -> dict:
        """Начать загрузку одного трека/плейлиста.

        Возвращает ``{"ok": True}`` или ``{"error": ...}``.
        События прогресса транслируются через ``event_callback`` в JS.
        """
        if self._is_busy():
            return {"error": "Загрузка уже идёт"}
        if not url or not str(url).strip():
            return {"error": "Введите ссылку"}

        url = str(url).strip()
        fmt = (fmt or self._settings.get("format", "aac")).lower()
        if fmt not in _AUDIO_FORMATS:
            fmt = "aac"
        quality = str(self._settings.get("quality", "256"))

        self._settings["format"] = fmt
        self._settings["quality"] = quality
        save_settings(self._settings)

        target_dir = self.music_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            workers = int(self._settings.get("max_workers", 3))
        except (TypeError, ValueError):
            workers = 3
        workers = max(1, min(5, workers))

        gui_queue = queue.Queue()
        try:
            downloader = Downloader(gui_queue, self._settings, max_workers=workers)
            downloader.ffmpeg_path = self.ffmpeg_path
        except Exception as e:
            logger.exception("Failed to create Downloader")
            self._emit("done", {"ok": False, "canceled": False, "error": str(e)})
            return {"error": str(e)}

        self._downloader = downloader

        def _run():
            try:
                downloader.download(url, "single", str(target_dir), quality, "ytmusic")
            except Exception as e:  # pragma: no cover
                logger.exception("Download thread crashed")
                self._emit("done", {"ok": False, "canceled": False, "error": str(e)})

        def _pump():
            while True:
                try:
                    kind, payload = gui_queue.get()
                except Exception:
                    break
                if kind == "done" and isinstance(payload, dict):
                    self._current_folder = payload.get("folder")
                self._emit(kind, payload)
                if kind == "done":
                    break

        self._download_thread = threading.Thread(target=_run, daemon=True)
        pump_thread = threading.Thread(target=_pump, daemon=True)
        self._download_thread.start()
        pump_thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        if self._downloader:
            self._downloader.stop()
        return {"ok": True}

    def open_current_folder(self) -> dict:
        # На Android папка фиксирована (приватная директория приложения).
        return {"ok": True, "path": str(self.music_dir)}

    def download_dir(self) -> str:
        return str(self.music_dir)


def _make_js_callback(bridge):
    """Создаёт колбэк, пересылающий события в JS через Kotlin-мост (PyBridge.emit)."""

    def _callback(kind, payload):
        bridge.emit(kind, payload)

    return _callback
