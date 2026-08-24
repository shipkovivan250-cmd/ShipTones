import glob
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import yt_dlp

from config import BASE_DIR, HAS_MUTAGEN, HAS_PIL, logger
from database import DB
from utils import (clean_filename, normalize_url, find_ffmpeg,
                   get_youtube_thumbnail_url, embed_car_friendly_cover)

if HAS_MUTAGEN:
    from mutagen.id3 import TIT2, TPE1, TALB, TRCK, TCON, TDRC, TXXX
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

    def update_progress_bar(self):
        with self.lock:
            completed = (self.stats["downloaded"] + self.stats["failed"] +
                         self.stats["skipped"] + self.stats["duplicates"])
            total = self.stats["total"]
            pct = (completed / total * 100) if total > 0 else 0
            self.gui_queue.put(("progress", {"percent": str(pct), "speed": "Многопоток", "eta": f"{completed}/{total}"}))
        self._update_status()

    # ============================================================
    # ОПЦИИ YT-DLP — ДОСЛОВНО КАК В РАБОЧЕМ КОДЕ
    # ============================================================
    def get_base_ydl_opts(self, quality, playlist_dir, filename_template):
        opts = {
            'ignoreerrors': True,
            'quiet': True,
            'no_warnings': True,
            'rm_cached_metadata': True,
            'ffmpeg_location': self.ffmpeg_path,
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(playlist_dir, filename_template),
            'writethumbnail': False,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'android', 'tv'],
                    'skip': ['dash', 'hls']
                }
            },
            'socket_timeout': 15,
            'retries': 2,
            'fragment_retries': 2,
        }
        opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': quality},
            {'key': 'FFmpegMetadata'},
        ]

        # ФИКС (стабильность v1.0): аудио-фильтры ВРЕМЕННО отключены —
        # на некоторых сборках FFmpeg silenceremove/loudnorm ломали постпроцессор.
        # Захочешь вернуть — раскомментируй два if ниже.
        filters = []
        # if self.settings.get("normalize_volume"):
        #     filters.append("loudnorm=I=-14:TP=-1:LRA=11")
        # if self.settings.get("remove_silence"):
        #     filters.append("silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB:detection=peak,"
        #                    "aformat=dblp,areverse,"
        #                    "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB:detection=peak,areverse")
        if filters:
            opts['postprocessor_args'] = {'FFmpegExtractAudio': ['-af', ','.join(filters)]}

        return opts

    # ============================================================
    # ПАКЕТНАЯ ОБРАБОТКА ТЕГОВ И ОБЛОЖЕК — КАК В РАБОЧЕМ КОДЕ
    # ============================================================
    def _process_tags_and_covers(self, playlist_dir, info):
        if not HAS_MUTAGEN or not HAS_PIL:
            self._log("⚠️ Mutagen или Pillow не установлены. Пропуск обработки.")
            return

        self._log("🎨 ShipTones: Встраивание обложек для автомобильных магнитол...")

        video_ids_dict = {}
        info_by_title = {}
        if info and 'entries' in info:
            for entry in info['entries']:
                if entry and entry.get('title') and entry.get('id'):
                    title_safe = clean_filename(entry['title'])
                    video_ids_dict[title_safe] = entry['id']
                    info_by_title[title_safe] = entry

        mp3_files = glob.glob(os.path.join(playlist_dir, "**", "*.mp3"), recursive=True)

        cover_success = 0
        tag_success = 0

        for mp3_file in mp3_files:
            try:
                name = os.path.basename(mp3_file)
                name_clean, _ = os.path.splitext(name)

                video_id = video_ids_dict.get(name_clean)
                entry = info_by_title.get(name_clean)
                if not video_id and " - " in name_clean:
                    _, pure_title = name_clean.split(" - ", 1)
                    video_id = video_ids_dict.get(pure_title.strip())
                    entry = entry or info_by_title.get(pure_title.strip())

                if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    thumb_url = get_youtube_thumbnail_url(video_id)
                    if embed_car_friendly_cover(mp3_file, thumb_url):
                        cover_success += 1

                parts = name_clean.split(" - ", 1)
                artist = parts[0].strip() if len(parts) > 1 else "Unknown Artist"
                title = parts[1].strip() if len(parts) > 1 else name_clean.strip()

                try:
                    audio = MP3(mp3_file)
                    if audio.tags is None:
                        audio.add_tags()

                    audio.tags.add(TIT2(encoding=3, text=title))
                    audio.tags.add(TPE1(encoding=3, text=artist))

                    if self.settings.get("extended_tags", True) and entry:
                        if entry.get('album'):
                            audio.tags.add(TALB(encoding=3, text=entry['album']))
                        if entry.get('track_number'):
                            audio.tags.add(TRCK(encoding=3, text=str(entry['track_number'])))
                        if entry.get('genre'):
                            audio.tags.add(TCON(encoding=3, text=entry['genre']))
                        ud = entry.get('upload_date', '')
                        if ud and len(ud) >= 4:
                            audio.tags.add(TDRC(encoding=3, text=ud[:4]))
                    audio.save()
                    tag_success += 1
                except Exception:
                    pass

            except Exception as e:
                self._log(f"⚠️ Ошибка обработки {name}: {str(e)[:50]}")

        self._log(f"✅ ShipTones: Обложки встроены: {cover_success}/{len(mp3_files)}")
        self._log(f"✅ ShipTones: Теги обновлены: {tag_success}/{len(mp3_files)}")

    # ============================================================
    # ОДНА ПОПЫТКА СКАЧАТЬ — готовый mp3 НЕ удаляется,
    # реальная ошибка видна в логе окна
    # ============================================================
    def _try_download(self, url_or_query, quality, playlist_dir, final_name):
        opts = self.get_base_ydl_opts(quality, playlist_dir, f"{final_name}.%(ext)s")
        mp3_path = os.path.join(playlist_dir, f"{final_name}.mp3")

        url_or_query = normalize_url(url_or_query)

        max_retries = 2
        for attempt in range(max_retries):
            if self.cancel_event.is_set():
                return False

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url_or_query])
            except Exception as e:
                self._log(f"⚠ [{final_name}] {str(e)[:120]}")
                error_msg = str(e).lower()
                if "timed out" in error_msg and attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    break

            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1024:
                return True

        # ФИКС: готовый mp3 НЕ удаляем никогда (убран из списка)
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1024:
            return True

        for ext in [".webm", ".m4a", ".webp", ".jpg", ".part"]:
            temp = os.path.join(playlist_dir, f"{final_name}{ext}")
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except Exception:
                    pass
        return False

    # ============================================================
    # ЗАГРУЗКА ОДНОГО ТРЕКА — ПОПЫТКИ КАК В РАБОЧЕМ КОДЕ
    # ============================================================
    def _download_single_track(self, entry, playlist_dir, quality, source="youtube"):
        if self.cancel_event.is_set():
            return

        track_id = entry.get('id')
        artist = entry.get('artist') or entry.get('uploader', 'Unknown Artist')
        title = entry.get('track') or entry.get('title', 'Без названия')
        url = entry.get('webpage_url') or entry.get('url', '')

        if " - " in title:
            final_name = clean_filename(title)
        else:
            final_name = clean_filename(f"{artist} - {title}")

        if not track_id and not url:
            with self.lock:
                self.stats["failed"] += 1
                self.failed_tracks.append((final_name, "Нет ID"))
            self.update_progress_bar()
            return

        # Фишка 6: умные дубликаты (теперь честные — с проверкой файла на диске)
        if self.settings.get("smart_dedup") and DB.is_track_downloaded(url):
            with self.lock:
                self.stats["duplicates"] += 1
            self._log(f"🔁 Дубликат: {final_name}")
            self.update_progress_bar()
            return

        # Фишка 8: автокаталогизация Artist/Album
        target_dir = playlist_dir
        if self.settings.get("auto_categorize"):
            album = entry.get('album', '')
            target_dir = os.path.join(playlist_dir,
                                      clean_filename(artist),
                                      clean_filename(album) if album else "Unknown Album")
            os.makedirs(target_dir, exist_ok=True)

        mp3_path = os.path.join(target_dir, f"{final_name}.mp3")

        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1024:
            with self.lock:
                self.stats["skipped"] += 1
            self._log(f"⏭ Уже есть: {final_name}")
            self.update_progress_bar()
            return

        self._log(f"📥 Загрузка: {final_name}")

        success = False
        if source == "youtube" and track_id:
            video_url = f"https://www.youtube.com/watch?v={track_id}"
            success = self._try_download(video_url, quality, target_dir, final_name)
        elif url:
            success = self._try_download(url, quality, target_dir, final_name)

        if not success and not self.cancel_event.is_set():
            self._log(f"🔄 Поиск: {final_name}")
            search_query = f"ytsearch1:{artist} {title} audio"
            success = self._try_download(search_query, quality, target_dir, final_name)

        if success:
            with self.lock:
                self.stats["downloaded"] += 1
            self._log(f"✅ Готово: {final_name}")
            DB.add_track(title, artist, source, url, mp3_path)
            self.gui_queue.put(("track_card", {"title": title, "artist": artist, "source": source}))
        else:
            with self.lock:
                self.stats["failed"] += 1
                self.failed_tracks.append((final_name, "Не скачано"))
            self._log(f"❌ Ошибка: {final_name}")

        self.update_progress_bar()

    # ============================================================
    # ОСНОВНОЙ МЕТОД — МЕХАНИКА РАБОЧЕГО КОДА + ПЛЕЙЛИСТЫ/СТАТУС
    # ============================================================
    def download(self, url, mode, target_folder, quality, source="youtube"):
        self.cancel_event.clear()
        self.start_time = time.time()
        with self.lock:
            self.stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0, "duplicates": 0}
            self.failed_tracks = []

        if not self.ffmpeg_path:
            self._log("❌ FFmpeg не найден!")
            self.gui_queue.put(("done", None))
            return

        os.makedirs(target_folder, exist_ok=True)

        try:
            self._log(" ShipTones: Получение информации (быстрый режим)...")
            url = normalize_url(url)

            info_opts = {
                'ignoreerrors': True,
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'android', 'tv'],
                    }
                },
                'rm_cached_metadata': True,
                'socket_timeout': 10,
                'retries': 2,
            }

            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                self._log("❌ Не удалось получить информацию")
                self.gui_queue.put(("done", None))
                return

            if 'entries' in info:
                entries = info.get('entries') or []
            else:
                entries = [info]

            # ФИКС: разрешаем треки, у которых есть хотя бы url (VK/Яндекс/закрытые YT)
            valid_entries = [e for e in entries if e and isinstance(e, dict) and (e.get('id') or e.get('url'))]

            with self.lock:
                self.stats['total'] = len(valid_entries)

            if self.stats['total'] == 0:
                self._log("⚠ Плейлист пуст")
                self.gui_queue.put(("done", None))
                return

            playlist_title = info.get('title', 'Загрузки')
            safe_name = clean_filename(playlist_title)

            if mode == "playlist":
                playlist_dir = os.path.join(target_folder, safe_name)
            else:
                playlist_dir = target_folder

            os.makedirs(playlist_dir, exist_ok=True)

            if mode == "playlist":
                self._log(f"📁 Плейлист: {playlist_title}")
                DB.save_playlist(playlist_title, url, source, playlist_dir)
                self.gui_queue.put(("refresh_playlists", None))
            self._log(f"🎵 Всего треков: {self.stats['total']}")
            self._log(f" ShipTones: Запуск в {self.max_workers} потока...")

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []
                for entry in valid_entries:
                    if self.cancel_event.is_set():
                        break
                    futures.append(executor.submit(self._download_single_track, entry, playlist_dir, quality, source))

                for future in as_completed(futures):
                    pass

            self._process_tags_and_covers(playlist_dir, info)

            if mode == "playlist":
                DB.touch_playlist(url)
                self.gui_queue.put(("refresh_playlists", None))

            if not self.cancel_event.is_set():
                elapsed = time.time() - self.start_time
                self._log("\n" + "═" * 40)
                self._log(f"✅ ShipTones: Скачано: {self.stats['downloaded']}")
                self._log(f"🔁 Дубли: {self.stats['duplicates']}")
                self._log(f"⏭ Пропущено: {self.stats['skipped']}")
                self._log(f"❌ Ошибок: {self.stats['failed']}")
                self._log(f"⏱ {int(elapsed//60)}м {int(elapsed%60)}с")
                self._log(f"📁 Папка: {playlist_dir}")
                self._log("═" * 40)
                self._save_report(playlist_title, playlist_dir)
            else:
                self._log("🛑 ShipTones: ЗАГРУЗКА ПРЕРВАНА")

        except Exception as e:
            self._log(f"❌ Ошибка: {e}")
        finally:
            self.gui_queue.put(("done", None))

    def _save_report(self, playlist_title, playlist_dir):
        mp3_files = glob.glob(os.path.join(playlist_dir, "**", "*.mp3"), recursive=True)
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

        log_dir = BASE_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        filename = log_dir / f"report_{datetime.now():%Y-%m-%d_%H-%M}.txt"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._log(f"📄 ShipTones: Отчёт сохранён")
        except Exception as e:
            self._log(f"⚠ Не сохранён отчёт: {e}")