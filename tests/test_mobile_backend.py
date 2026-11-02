import os
import threading
import time
from pathlib import Path

import pytest

import config
import database
from sources import youtube_source
from mobile.backend import Api

# Каталог для загрузок и БД изолируем во временной директории pytest,
# чтобы не писать в корень репозитория и не трогать desktop-базу.


@pytest.fixture
def env(tmp_path, monkeypatch):
    music_dir = tmp_path / "music"
    # api.db и youtube_source.DB (test_db) должны указывать на ОДИН файл.
    db_path = tmp_path / "shiptones.db"
    settings_file = tmp_path / "settings.json"

    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    # youtube_source использует module-level DB singleton — подменяем на тестовую БД.
    test_db = database.Database(db_path=str(db_path))
    monkeypatch.setattr(youtube_source, "DB", test_db)

    # Не писать отчёты в корень репозитория во время тестов.
    import downloader
    monkeypatch.setattr(downloader.Downloader, "_save_report",
                        lambda self, *a, **k: None)
    # Пропускаем встраивание обложек/тегов (сетевой запрос к YouTube) в тестах.
    monkeypatch.setattr(downloader.Downloader, "_process_tags_and_covers",
                        lambda self, playlist_dir, entries: None)

    api = Api(music_dir=str(music_dir), ffmpeg_path="/fake/ffmpeg")
    return {"api": api, "tmp_path": tmp_path, "music_dir": music_dir,
            "db_path": db_path, "test_db": test_db}


# ---------- yt-dlp заглушка ----------

class FakeYoutubeDL:
    """Имитирует yt_dlp.YoutubeDL: возвращает плейлист из одного трека и
    «скачивает» файл, чтобы downloader нашёл его через find_audio_file."""

    def __init__(self, opts=None, **kwargs):
        self.opts = opts or {}
        self.download_called = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {
            "_type": "playlist",
            "entries": [{
                "id": "dQw4w9WgXcQ",
                "title": "Never Gonna Give You Up",
                "artist": "Rick Astley",
                "uploader": "Rick Astley",
                "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            }],
        }

    def download(self, urls):
        self.download_called = True
        # progress_hooks — yt-dlp вызывает их на каждом тике прогресса.
        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "downloading"})
        # «Создаём» аудиофайл, который позже найдёт find_audio_file.
        outtmpl = self.opts.get("outtmpl", "")
        if "%(ext)s" in outtmpl:
            path = outtmpl.replace("%(ext)s", "m4a")
        else:
            path = outtmpl or "fake.m4a"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 2000)  # >1024 байт, чтобы find_audio_file нашёл файл


@pytest.fixture(autouse=True)
def patch_ytdlp(monkeypatch):
    import yt_dlp
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(youtube_source.yt_dlp, "YoutubeDL", FakeYoutubeDL)


# ---------- тесты ----------

def test_start_download_success(env):
    api = env["api"]
    events = []
    api.event_callback = lambda kind, payload: events.append((kind, payload))

    res = api.start_download("https://youtube.com/watch?v=dQw4w9WgXcQ", "aac")
    assert res == {"ok": True}

    # Ждём завершения загрузки (фоновый поток).
    api._download_thread.join(timeout=10)
    assert not api._download_thread.is_alive()

    done_events = [e for e in events if e[0] == "done"]
    assert done_events, "ожидалось событие done"
    assert done_events[-1][1]["ok"] is True
    assert done_events[-1][1]["canceled"] is False

    # Файл должен появиться в приватной директории приложения.
    music_files = list(env["music_dir"].rglob("*"))
    assert any(f.is_file() for f in music_files), "аудиофайл не создан"

    # История должна содержать загруженный трек.
    history = api.history()
    assert any(row[0] == "Never Gonna Give You Up" for row in history), history


def test_start_download_empty_input(env):
    api = env["api"]
    assert api.start_download("", "aac") == {"error": "Введите ссылку"}
    assert api.start_download("   ", "aac") == {"error": "Введите ссылку"}
    assert api.start_download(None, "aac") == {"error": "Введите ссылку"}


def test_start_download_busy(env, monkeypatch):
    api = env["api"]
    # Первый запуск — идёт в фоне.
    api.start_download("https://youtube.com/watch?v=dQw4w9WgXcQ", "aac")
    # Второй запуск должен быть отклонён.
    res = api.start_download("https://youtube.com/watch?v=other", "aac")
    assert res == {"error": "Загрузка уже идёт"}
    api._download_thread.join(timeout=10)


def test_start_download_bad_url(env, monkeypatch):
    import yt_dlp

    class BrokenYDL:
        def __init__(self, opts=None, **kwargs):
            self.opts = opts or {}
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def extract_info(self, url, download=False):
            return None
        def download(self, urls):
            pass

    monkeypatch.setattr(yt_dlp, "YoutubeDL", BrokenYDL)
    monkeypatch.setattr(youtube_source.yt_dlp, "YoutubeDL", BrokenYDL)

    api = env["api"]
    events = []
    api.event_callback = lambda kind, payload: events.append((kind, payload))
    res = api.start_download("https://youtube.com/watch?v=broken", "aac")
    assert res == {"ok": True}
    api._download_thread.join(timeout=10)

    done_events = [e for e in events if e[0] == "done"]
    assert done_events and done_events[-1][1]["ok"] is False
    assert done_events[-1][1]["error"] == "Не удалось получить информацию"


def test_cancel(env, monkeypatch):
    import yt_dlp
    import time as _time

    class CancellableYDL:
        def __init__(self, opts=None, **kwargs):
            self.opts = opts or {}
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def extract_info(self, url, download=False):
            return {
                "_type": "playlist",
                "entries": [{
                    "id": "dQw4w9WgXcQ", "title": "Track",
                    "artist": "Artist", "uploader": "Artist",
                    "webpage_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                    "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
                }],
            }
        def download(self, urls):
            # Небольшая задержка, чтобы отмена успела прийти до тика прогресса.
            _time.sleep(0.2)
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "downloading"})  # поднимет DownloadCancelled

    monkeypatch.setattr(yt_dlp, "YoutubeDL", CancellableYDL)
    monkeypatch.setattr(youtube_source.yt_dlp, "YoutubeDL", CancellableYDL)

    api = env["api"]
    events = []
    api.event_callback = lambda kind, payload: events.append((kind, payload))
    api.start_download("https://youtube.com/watch?v=dQw4w9WgXcQ", "aac")
    api.cancel()
    api._download_thread.join(timeout=10)

    assert api._downloader.cancel_event.is_set()
    done_events = [e for e in events if e[0] == "done"]
    assert done_events and done_events[-1][1]["canceled"] is True


def test_history_and_playlists(env):
    api = env["api"]
    api.db.add_track("Song One", "Artist A", "ytmusic",
                     "https://youtube.com/watch?v=aaa", str(env["music_dir"] / "a.m4a"))
    api.db.add_track("Song Two", "Artist B", "ytmusic",
                     "https://youtube.com/watch?v=bbb", str(env["music_dir"] / "b.m4a"))
    api.db.save_playlist("Моя подборка",
                         "https://youtube.com/playlist?list=xyz", "ytmusic",
                         str(env["music_dir"]))

    history = api.history()
    assert len(history) == 2
    titles = {row[0] for row in history}
    assert titles == {"Song One", "Song Two"}

    playlists = api.playlists()
    assert len(playlists) == 1
    assert playlists[0][1] == "Моя подборка"


def test_history_search(env):
    api = env["api"]
    api.db.add_track("UniqueTrackName", "Artist", "ytmusic",
                     "https://youtube.com/watch?v=uniq", str(env["music_dir"] / "u.m4a"))
    assert len(api.history(search="UniqueTrackName")) == 1
    assert len(api.history(search="Несуществующее")) == 0


def test_settings_get_and_set(env):
    api = env["api"]
    current = api.settings()
    assert isinstance(current, dict)
    assert "format" in current and "quality" in current

    res = api.settings({"format": "mp3", "quality": "320"})
    assert res == {"ok": True}
    updated = api.settings()
    assert updated["format"] == "mp3"
    assert updated["quality"] == "320"

    # некорректный формат отбрасывается
    res = api.settings({"format": "not-a-format"})
    assert res == {"ok": True}
    assert api.settings()["format"] == "mp3"  # осталось прежнее


def test_cancel_without_download(env):
    api = env["api"]
    assert api.cancel() == {"ok": True}


def test_open_current_folder(env):
    api = env["api"]
    res = api.open_current_folder()
    assert res == {"ok": True, "path": str(env["music_dir"])}
