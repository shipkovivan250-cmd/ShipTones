import json
import os
import queue
import sys
import threading
from pathlib import Path

if sys.platform == "win32":
    # ФИКС: без этого Windows рендерит окно в заниженном DPI и растягивает
    # картинкой — скруглённые углы (переключатели, карточки) становятся зубчатыми.
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import webview

from config import BASE_DIR, SOURCES, load_settings, save_settings, logger
from database import DB
from downloader import Downloader
from utils import check_internet, update_ytdlp

# ФИКС: в собранном .exe (--onefile) файлы из --add-data распаковываются
# во временную папку sys._MEIPASS, а не рядом с exe — читаем web/ оттуда.
WEB_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "web"


class Api:
    def __init__(self):
        self._window = None
        self.settings = load_settings()
        self._downloader = None
        self._download_thread = None
        self._current_target_folder = None

    # ---------------- lookups ----------------
    def get_sources(self):
        return {k: {"name": v["name"], "icon": v["icon"], "color": v["color"], "fg": v["fg"]}
                for k, v in SOURCES.items()}

    def get_settings(self):
        return self.settings

    def save_settings(self, settings):
        self.settings.update(settings)
        save_settings(self.settings)

    def pick_folder(self):
        start_dir = self.settings.get("download_dir") or ""
        if not start_dir or not os.path.isdir(start_dir):
            start_dir = os.path.expanduser("~")

        holder = {}

        def _do():
            holder["result"] = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start_dir)

        if sys.platform == "win32" and self._window is not None and self._window.native is not None:
            # ФИКС: create_file_dialog в pywebview не переключается на UI-поток сама
            # (в отличие от evaluate_js) — наши API-методы всегда идут в фоновом потоке,
            # поэтому диалог зависает/падает с COM-ошибкой. Переключаемся вручную.
            from System import Action
            self._window.native.Invoke(Action(_do))
        else:
            _do()

        result = holder.get("result")
        return result[0] if result else None

    def check_internet(self):
        return check_internet()

    def update_ytdlp(self):
        ok, msg = update_ytdlp()
        return {"ok": ok, "msg": msg}

    def get_history(self, search=""):
        return [list(row) for row in DB.get_history(search)]

    def get_playlists(self):
        return [list(row) for row in DB.get_playlists()]

    def delete_playlist(self, playlist_id):
        DB.delete_playlist(playlist_id)

    def open_current_folder(self):
        if self._current_target_folder and os.path.exists(self._current_target_folder):
            os.startfile(self._current_target_folder)

    # ---------------- downloads ----------------
    def _is_busy(self):
        return bool(self._download_thread and self._download_thread.is_alive())

    def start_download(self, url, mode, source, target_dir, quality):
        if self._is_busy():
            return {"error": "Загрузка уже идёт"}
        if not url:
            return {"error": "Введите ссылку"}
        if not target_dir or not os.path.isdir(target_dir):
            return {"error": "Папка назначения недоступна"}

        self._launch(url, mode, source, target_dir, quality)
        return {"ok": True}

    def sync_playlist(self, playlist_id):
        if self._is_busy():
            return {"error": "Загрузка уже идёт"}
        row = next((p for p in DB.get_playlists() if p[0] == playlist_id), None)
        if not row:
            return {"error": "Плейлист не найден"}
        _, name, url, source, target_path, _ = row
        if not target_path:
            return {"error": "У плейлиста нет папки назначения"}
        root = str(Path(target_path).parent)
        quality = self.settings.get("quality", "192")
        self._launch(url, "playlist", source, root, quality)
        return {"ok": True}

    def _launch(self, url, mode, source, target_root, quality):
        self._current_target_folder = target_root
        gui_queue = queue.Queue()
        workers = self.settings.get("max_workers", 3)
        self._downloader = Downloader(gui_queue, self.settings, max_workers=workers)
        self._download_thread = threading.Thread(
            target=self._downloader.download, args=(url, mode, target_root, quality, source), daemon=True)
        self._download_thread.start()
        threading.Thread(target=self._pump_queue, args=(gui_queue,), daemon=True).start()

    def cancel_download(self):
        if self._downloader:
            self._downloader.stop()

    def _pump_queue(self, gui_queue):
        while True:
            kind, payload = gui_queue.get()
            self._emit(kind, payload)
            if kind == "done":
                break

    def _emit(self, kind, payload):
        if not self._window:
            return
        try:
            self._window.evaluate_js(f"window.onEvent({json.dumps(kind)}, {json.dumps(payload)})")
        except Exception as e:
            logger.error(f"evaluate_js failed: {e}")


def _system_prefers_dark():
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False


def main():
    # ФИКС: окно рисует свой фон ДО того, как наш JS успевает применить тему —
    # подбираем цвет фона под системную тему заранее, чтобы не было белой вспышки.
    bg = "#0a0a0b" if _system_prefers_dark() else "#f9fafb"

    api = Api()
    window = webview.create_window(
        "ShipTones", url=str(WEB_DIR / "index.html"),
        js_api=api, width=1180, height=780, min_size=(960, 640),
        background_color=bg,
    )
    api._window = window
    webview.start()


if __name__ == "__main__":
    main()
