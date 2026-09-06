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
    # ОПЦИИ YT-DLP — ДОСЛОВНО КАК В РАБОЧЕМ КОДЕ
    # ============================================================
    def get_base_ydl_opts(self, quality, playlist_dir, filename_template):
        # Карта кодеков: формат -> (codec, default_quality)
        codec_map = {
            "mp3": ("mp3", "320"),
            "aac": ("aac", "256"),
            "flac": ("flac", "0"),
            "opus": ("libopus", "160"),
        }
        
        selected_format = self.settings.get("format", "aac")
        codec, default_quality = codec_map.get(selected_format, ("aac", "256"))
        
        # Если качество не указано в вызове, используем дефолтное для формата
        if not quality:
            quality = self.settings.get("quality", default_quality)
        
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
            'progress_hooks': [self._check_cancel_hook],
        }
        
        # Настраиваем постпроцессор для выбранного кодека
        opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': codec, 'preferredquality': quality},
            {'key': 'FFmpegMetadata'},
        ]

        # Нормализация громкости как в YouTube Music (-14 LUFS)
        filters = []
        if self.settings.get("normalize_volume"):
            # Точная нормализация EBU R128 как в YouTube Music
            filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")
            
            # Лёгкий бас-буст для лучшего звучания в автомобиле
            filters.append("equalizer=f=60:width_type=o:width=2:g=2")
            
            # Мягкая компрессия для стабильной громкости
            filters.append("acompressor=threshold=-20dB:ratio=2:attack=200:release=1000")
        
        if self.settings.get("remove_silence"):
            filters.append("silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB:detection=peak,"
                           "aformat=dblp,areverse,"
                           "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB:detection=peak,areverse")
        
        if filters:
            opts['postprocessor_args'] = {'FFmpegExtractAudio': ['-af', ','.join(filters)]}

        return opts

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
        tag_success = 0

        for audio_file in downloaded_files:
            try:
                name = os.path.basename(audio_file)
                name_clean, file_ext = os.path.splitext(name)

                video_id = video_ids_dict.get(name_clean)
                entry = info_by_title.get(name_clean)
                if not video_id and " - " in name_clean:
                    _, pure_title = name_clean.split(" - ", 1)
                    video_id = video_ids_dict.get(pure_title.strip())
                    entry = entry or info_by_title.get(pure_title.strip())

                if video_id and re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                    thumb_url = get_youtube_thumbnail_url(video_id)
                    if embed_car_friendly_cover(audio_file, thumb_url):
                        cover_success += 1

                parts = name_clean.split(" - ", 1)
                artist = parts[0].strip() if len(parts) > 1 else "Unknown Artist"
                title = parts[1].strip() if len(parts) > 1 else name_clean.strip()

                try:
                    # Определяем тип файла по расширению и используем соответствующий класс
                    file_ext_lower = file_ext.lower()
                    if file_ext_lower in ['.flac']:
                        from mutagen.flac import FLAC
                        audio = FLAC(audio_file)
                    elif file_ext_lower in ['.opus', '.ogg']:
                        from mutagen.oggopus import OggOpus
                        audio = OggOpus(audio_file)
                    elif file_ext_lower in ['.m4a', '.aac', '.mp4']:
                        from mutagen.mp4 import MP4
                        audio = MP4(audio_file)
                        # Для MP4 используем другой подход к тегам
                        audio['\xa9nam'] = title  # Название
                        audio['\xa9ART'] = artist  # Артист
                        if self.settings.get("extended_tags", True) and entry:
                            if entry.get('album'):
                                audio['\xa9alb'] = entry['album']
                            if entry.get('genre'):
                                audio['\xa9gen'] = entry['genre']
                        audio.save()
                        tag_success += 1
                        continue
                    else:
                        # По умолчанию считаем MP3
                        audio = MP3(audio_file)
                    
                    # Обработка тегов ID3 для MP3 и других форматов с поддержкой ID3
                    if hasattr(audio, 'tags'):
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
                except Exception as e:
                    logger.exception(f"Tag error for {audio_file}")
                    self._log(f"⚠️ Ошибка тегов: {os.path.basename(audio_file)} — {str(e)[:50]}")

            except Exception as e:
                self._log(f"⚠️ Ошибка обработки {name}: {str(e)[:50]}")

        self._log(f"✅ ShipTones: Обложки встроены: {cover_success}/{len(downloaded_files)}")
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
    def _finish(self, ok, error=None):
        """Завершение загрузки, вызывается из модулей источников"""
        self.gui_queue.put(("done", {
            "ok": ok,
            "canceled": self.cancel_event.is_set(),
            "stats": self.stats,
            "error": error,
        }))

    def download(self, url, mode, target_folder, quality, source="youtube"):
        """Диспетчер по источнику. КАЖДЫЙ источник — отдельный изолированный модуль
        в папке sources/ со своей собственной обработкой ошибок: баг или сбой в 
        недоделанном VK/Яндекс не может затронуть рабочую YouTube-загрузку."""
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