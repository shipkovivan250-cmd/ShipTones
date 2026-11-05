import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from config import DATA_DIR, HAS_MUTAGEN, HAS_PIL, logger
from database import DB
from utils import (clean_filename, normalize_url, find_ffmpeg,
                   get_youtube_thumbnail_url, embed_car_friendly_cover)
from sources import get_downloader

if HAS_MUTAGEN:
    from mutagen.id3 import TIT2, TPE1, TALB, TRCK, TCON, TDRC
    from mutagen.mp3 import MP3

# ============================================================
# ЯДРО ЗАГРУЗКИ — механика ВЗЯТА ИЗ РАБОЧЕЙ ВЕРСИИ v1.0
# ============================================================
class Downloader:
    def __init__(self, gui_queue, settings=None, max_workers=3):
        self.gui_queue = gui_queue
        self.settings = settings or {}
        self.max_workers = max_workers
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "duplicates": 0}
        self.failed_tracks = []
        self.downloaded_files = []
        self.claimed_names = set()
        self.ffmpeg_path = find_ffmpeg()
        self.start_time = None

    def stop(self):
        self.cancel_event.set()

    def _log(self, msg):
        self.gui_queue.put(("log", msg))

    def _update_status(self):
        elapsed = time.time() - self.start_time if self.start_time else 0
        with self.lock:
            self.gui_queue.put(("status", {**self.stats, "elapsed": elapsed}))

    def _check_cancel_hook(self, d):
        """ФИКС (нормальный stop): раньше отмена проверялась только МЕЖДУ файлами —
        yt-dlp уже начатый файл докачивал до конца, что угодно ни делай. progress_hook
        вызывается на каждый прогресс-тик (несколько раз в секунду), и если тут
        поднять DownloadCancelled — yt-dlp прерывает СКАЧИВАНИЕ ЭТОГО ФАЙЛА немедленно
        и пробрасывает исключение наружу из ydl.download(), а не глотает его."""
        if self.cancel_event.is_set():
            raise DownloadCancelled("Отменено пользователем")

    def update_progress_bar(self):
        with self.lock:
            completed = (self.stats["downloaded"] + self.stats["failed"] +
                         self.stats["skipped"] + self.stats["duplicates"])
            total = self.stats["total"]
            pct = (completed / total * 100) if total > 0 else 0
            self.gui_queue.put(("progress", {"percent": str(pct), "speed": "Многопоток", "eta": f"{completed}/{total}"}))
        self._update_status()

    # ============================================================
    # ОПЦИИ YT-DLP — находятся в sources/youtube_source.py
    # (get_ydl_opts). Здесь только общий код ядра: статус, прогресс,
    # теги/обложки, отчёты и диспетчер источников.
    # ============================================================

    # ============================================================
    # ПАКЕТНАЯ ОБРАБОТКА ТЕГОВ И ОБЛОЖЕК — КАК В РАБОЧЕМ КОДЕ
    # ============================================================
    def _process_tags_and_covers(self, playlist_dir, entries):
        if not HAS_MUTAGEN or not HAS_PIL:
            self._log("⚠️ Mutagen или Pillow не установлены. Пропуск обработки.")
            return

        self._log("🎨 ShipTones: Встраивание обложек для автомобильных магнитол...")

        video_ids_dict = {}
        info_by_title = {}
        for entry in entries or []:
            if entry and entry.get('title') and entry.get('id'):
                title_safe = clean_filename(entry['title'])
                video_ids_dict[title_safe] = entry['id']
                info_by_title[title_safe] = entry

        # ФИКС: обрабатываем только файлы, скачанные В ЭТОЙ сессии, а не все
        # файлы в папке — иначе при каждой загрузке заново перебивались бы теги
        # и обложки у ранее скачанных треков.
        downloaded_files = list(self.downloaded_files)

        cover_success = 0
        cover_failed = 0
        tag_success = 0

        def _process_one_track(audio_file):
            """Один трек целиком: обложка (сеть) + теги (локально).
            Возвращает (cover_ok, tag_ok, error); error — при исключении, уже
            залогировано внутри. Операции над разными файлами независимы, поэтому
            метод безопасен для параллельного выполнения в ThreadPoolExecutor."""
            name = os.path.basename(audio_file)
            name_clean, file_ext = os.path.splitext(name)
            error = None
            cover_ok = False
            tag_ok = False
            try:
                video_id = video_ids_dict.get(name_clean)
                entry = info_by_title.get(name_clean)
                if not video_id and " - " in name_clean:
                    _, pure_title = name_clean.split(" - ", 1)
                    video_id = video_ids_dict.get(pure_title.strip())
                    entry = entry or info_by_title.get(pure_title.strip())

                if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    thumb_url = get_youtube_thumbnail_url(video_id)
                    if embed_car_friendly_cover(audio_file, thumb_url):
                        cover_ok = True
                    else:
                        self._log(f"❌ Обложка не обработана: {os.path.basename(audio_file)}")
                else:
                    self._log(f"❌ Обложка не найдена (video_id): {os.path.basename(audio_file)}")

                parts = name_clean.split(" - ", 1)
                artist = parts[0].strip() if len(parts) > 1 else "Unknown Artist"
                title = parts[1].strip() if len(parts) > 1 else name_clean.strip()
                if entry:
                    artist = entry.get("artist") or entry.get("uploader") or artist
                    title = entry.get("track") or entry.get("title") or title

                try:
                    extra = {}
                    if self.settings.get("extended_tags", True) and entry:
                        extra = {
                            "album": entry.get("album"),
                            "track": entry.get("track_number") or entry.get("track_number_str"),
                            "genre": entry.get("genre"),
                            "year": (entry.get("upload_date") or "")[:4] or None,
                        }
                    tag_ok = self._write_tags(audio_file, file_ext.lower(), title, artist, extra) or False
                except Exception as e:
                    logger.exception(f"Tag error for {audio_file}")
                    self._log(f"⚠️ Ошибка тегов: {os.path.basename(audio_file)} — {str(e)[:50]}")

            except Exception as e:
                error = str(e)
                self._log(f"⚠️ Ошибка обработки {name}: {str(e)[:50]}")

            return cover_ok, tag_ok, error

        results = []
        if len(downloaded_files) > 1:
            # ФИКС СКОРОСТЫ: обработка обложек и тегов большого плейлиста
            # выполнялась последовательно — после скачивания всех треков весь
            # список треков обрабатывался в одну цепочку (сеть + mutagen).
            # Теперь треки обрабатываются параллельно (ThreadPoolExecutor,
            # с лимитом max_workers) — операции над разными файлами независимы.
            workers = max(1, min(int(self.max_workers or 1), len(downloaded_files)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_process_one_track, f) for f in downloaded_files]
                results = [fut.result() for fut in futures]
        else:
            results = [_process_one_track(f) for f in downloaded_files]

        for cover_ok, tag_ok, error in results:
            if error is not None:
                continue  # ошибка уже залогирована внутри трека
            if cover_ok:
                cover_success += 1
            else:
                cover_failed += 1
            if tag_ok:
                tag_success += 1

        self._log(f"✅ ShipTones: Обложки встроены: {cover_success}/{len(downloaded_files)}"
                 f" (не удалось: {cover_failed})")
        self._log(f"✅ ShipTones: Теги обновлены: {tag_success}/{len(downloaded_files)}")

    # ============================================================
    # СОХРАНЕНИЕ ОТЧЁТА — общий метод для всех источников
    # ============================================================
    def _save_report(self, playlist_title, playlist_dir):
        mp3_files = list(self.downloaded_files)
        stats = self.stats
        lines = [
            f"Плейлист: {playlist_title}",
            f"Дата: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Папка: {playlist_dir}",
            f"Всего треков: {stats['total']}",
            f"Скачано: {stats['downloaded']}",
            f"Дубли: {stats['duplicates']}",
            f"Пропущено: {stats['skipped']}",
            f"Ошибки: {stats['failed']}",
            "=" * 50,
            "Скачанные файлы:",
        ]
        for f in sorted(mp3_files):
            lines.append(f"  ✓ {os.path.basename(f)}")

        if self.failed_tracks:
            lines += ["", "❌ Не скачано:"] + [f"  [X] {t} — {e}" for t, e in self.failed_tracks]

        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        filename = log_dir / f"report_{datetime.now():%Y-%m-%d_%H-%M}.txt"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._log(f"📄 ShipTones: Отчёт сохранён")
        except Exception as e:
            self._log(f"⚠ Не сохранён отчёт: {e}")

    # ============================================================
    # ФИНИШ И ДИСПЕТЧЕР — вызываются из модулей sources/
    # ============================================================
    def _write_tags(self, audio_file, file_ext, title, artist, extra=None):
        extra = extra or {}
        album = extra.get("album")
        track = extra.get("track")
        genre = extra.get("genre")
        year = extra.get("year")

        if file_ext in (".flac",):
            from mutagen.flac import FLAC
            audio = FLAC(audio_file)
            audio["title"] = title
            audio["artist"] = artist
            if album:
                audio["album"] = album
            if track:
                audio["tracknumber"] = str(track)
            if genre:
                audio["genre"] = genre
            if year:
                audio["date"] = str(year)
            audio.save()
            return True

        if file_ext in (".opus", ".ogg"):
            from mutagen.oggopus import OggOpus
            audio = OggOpus(audio_file)
            audio["title"] = title
            audio["artist"] = artist
            if album:
                audio["album"] = album
            if track:
                audio["tracknumber"] = str(track)
            if genre:
                audio["genre"] = genre
            if year:
                audio["date"] = str(year)
            audio.save()
            return True

        if file_ext in (".m4a", ".aac", ".mp4"):
            from mutagen.mp4 import MP4
            audio = MP4(audio_file)
            audio["\xa9nam"] = title
            audio["\xa9ART"] = artist
            if album:
                audio["\xa9alb"] = album
            if genre:
                audio["\xa9gen"] = genre
            if year:
                audio["\xa9day"] = str(year)
            if track:
                try:
                    audio["trkn"] = [(int(track), 0)]
                except (TypeError, ValueError):
                    pass
            audio.save()
            return True

        if file_ext != ".mp3":
            return False

        audio = MP3(audio_file)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        if album:
            audio.tags.add(TALB(encoding=3, text=album))
        if track:
            audio.tags.add(TRCK(encoding=3, text=str(track)))
        if genre:
            audio.tags.add(TCON(encoding=3, text=genre))
        if year:
            audio.tags.add(TDRC(encoding=3, text=str(year)))
        audio.save()
        return True

    def _finish(self, ok, error=None, folder=None):
        """Завершение загрузки, вызывается из модулей источников"""
        self.gui_queue.put(("done", {
            "ok": ok,
            "canceled": self.cancel_event.is_set(),
            "stats": self.stats,
            "error": error,
            "folder": folder,
        }))

    def download(self, url, mode, target_folder, quality, source="ytmusic"):
        """Диспетчер по источнику. КАЖДЫЙ источник — отдельный изолированный модуль
        в папке sources/ со своей собственной обработкой ошибок: баг или сбой в 
        недоделанном VK/Яндекс не может затронуть рабочую YouTube Music-загрузку."""
        self.cancel_event.clear()
        self.start_time = time.time()
        with self.lock:
            self.stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "duplicates": 0}
            self.failed_tracks = []
            self.downloaded_files = []
            self.claimed_names = set()

        try:
            # Получаем загрузчик для нужного источника
            source_downloader = get_downloader(source, self)
            source_downloader.download(url, mode, target_folder, quality)
        except ValueError as e:
            self._log(f"❌ Неизвестный источник: {source}")
            self._finish(ok=False, error=f"Неизвестный источник: {source}")
        except Exception as e:
            self._log(f"❌ Ошибка источника {source}: {e}")
            logger.exception("Source download failed")
            self._finish(ok=False, error=str(e))