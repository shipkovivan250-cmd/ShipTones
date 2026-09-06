"""
Модуль загрузки с YouTube
Использует yt-dlp для извлечения аудио с YouTube и YouTube Music
"""

import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from config import logger
from database import DB
from utils import clean_filename, normalize_url


class YouTubeDownloader:
    """Загрузчик треков и плейлистов с YouTube/YouTube Music"""
    
    def __init__(self, parent_downloader):
        self.parent = parent_downloader
        self.settings = parent_downloader.settings
        self.ffmpeg_path = parent_downloader.ffmpeg_path
        
    def _log(self, msg):
        self.parent._log(msg)
        
    def _check_cancel_hook(self, d):
        if self.parent.cancel_event.is_set():
            raise DownloadCancelled("Отменено пользователем")
    
    def get_ydl_opts(self, quality, playlist_dir, filename_template):
        """Настройки yt-dlp для YouTube"""
        # Карта кодеков: формат -> (codec, default_quality)
        codec_map = {
            "mp3": ("mp3", "320"),
            "aac": ("aac", "256"),
            "flac": ("flac", "0"),
            "opus": ("libopus", "160"),
        }
        
        selected_format = self.settings.get("format", "aac")
        codec, default_quality = codec_map.get(selected_format, ("aac", "256"))
        
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
    
    def download_track(self, entry, playlist_dir, quality):
        """Скачивание одного трека"""
        if self.parent.cancel_event.is_set():
            return

        track_id = entry.get('id')
        artist = entry.get('artist') or entry.get('uploader', 'Unknown Artist')
        title = entry.get('track') or entry.get('title', 'Без названия')
        url = entry.get('webpage_url') or entry.get('url', '')

        if " - " in title:
            final_name = clean_filename(title)
        else:
            final_name = clean_filename(f"{artist} - {title}")

        # Уникальность имени файла
        with self.parent.lock:
            if final_name in self.parent.claimed_names:
                base_name, i = final_name, 2
                while f"{base_name} ({i})" in self.parent.claimed_names:
                    i += 1
                final_name = f"{base_name} ({i})"
            self.parent.claimed_names.add(final_name)

        if not track_id and not url:
            with self.parent.lock:
                self.parent.stats["failed"] += 1
                self.parent.failed_tracks.append((final_name, "Нет ID"))
            self.parent.update_progress_bar()
            return

        # Проверка дубликатов
        if self.settings.get("smart_dedup") and DB.is_track_downloaded(url):
            with self.parent.lock:
                self.parent.stats["duplicates"] += 1
            self._log(f"🔁 Дубликат: {final_name}")
            self.parent.update_progress_bar()
            return

        # Автокаталогизация
        target_dir = playlist_dir
        if self.settings.get("auto_categorize"):
            album = entry.get('album', '')
            target_dir = os.path.join(playlist_dir,
                                      clean_filename(artist),
                                      clean_filename(album) if album else "Unknown Album")
            os.makedirs(target_dir, exist_ok=True)

        # Определение расширения файла
        selected_format = self.settings.get("format", "aac")
        codec_map = {
            "mp3": "mp3",
            "aac": "m4a",
            "flac": "flac",
            "opus": "opus",
        }
        file_ext = codec_map.get(selected_format, "m4a")
        file_path = os.path.join(target_dir, f"{final_name}.{file_ext}")

        if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
            with self.parent.lock:
                self.parent.stats["skipped"] += 1
            self._log(f"⏭ Уже есть: {final_name}")
            self.parent.update_progress_bar()
            return

        self._log(f"📥 Загрузка: {final_name}")

        success = False
        if track_id:
            video_url = f"https://www.youtube.com/watch?v={track_id}"
            success = self._try_download(video_url, quality, target_dir, final_name)
        elif url:
            success = self._try_download(url, quality, target_dir, final_name)

        if not success and not self.parent.cancel_event.is_set():
            self._log(f"🔄 Поиск: {final_name}")
            search_query = f"ytsearch1:{artist} {title} audio"
            success = self._try_download(search_query, quality, target_dir, final_name)

        if success:
            with self.parent.lock:
                self.parent.stats["downloaded"] += 1
                self.parent.downloaded_files.append(file_path)
            self._log(f"✅ Готово: {final_name}")
            final_url = f"https://www.youtube.com/watch?v={track_id}" if track_id else url
            if final_url:
                DB.add_track(title, artist, "youtube", final_url, file_path)
            self.parent.gui_queue.put(("track_card", {"title": title, "artist": artist, "source": "youtube"}))
        elif self.parent.cancel_event.is_set():
            pass
        else:
            with self.parent.lock:
                self.parent.stats["failed"] += 1
                self.parent.failed_tracks.append((final_name, "Не скачано"))
            self._log(f"❌ Ошибка: {final_name}")

        self.parent.update_progress_bar()
    
    def _try_download(self, url_or_query, quality, playlist_dir, final_name):
        """Попытка скачать один трек"""
        opts = self.get_ydl_opts(quality, playlist_dir, f"{final_name}.%(ext)s")
        
        # Определение расширения файла
        selected_format = self.settings.get("format", "aac")
        codec_map = {
            "mp3": "mp3",
            "aac": "m4a",
            "flac": "flac",
            "opus": "opus",
        }
        file_ext = codec_map.get(selected_format, "m4a")
        file_path = os.path.join(playlist_dir, f"{final_name}.{file_ext}")

        url_or_query = normalize_url(url_or_query)

        max_retries = 2
        for attempt in range(max_retries):
            if self.parent.cancel_event.is_set():
                return False

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url_or_query])
            except DownloadCancelled:
                break
            except Exception as e:
                self._log(f"⚠ [{final_name}] {str(e)[:120]}")
                error_msg = str(e).lower()
                if "timed out" in error_msg and attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    break

            if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
                return True

        if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
            return True

        for ext in [".webm", ".m4a", ".webp", ".jpg", ".part"]:
            temp = os.path.join(playlist_dir, f"{final_name}{ext}")
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except Exception:
                    pass
        return False
    
    def download(self, url, mode, target_folder, quality):
        """Основной метод загрузки с YouTube"""
        if not self.ffmpeg_path:
            self._log("❌ FFmpeg не найден!")
            self.parent._finish(ok=False, error="FFmpeg не найден")
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
                'playlistend': 1 if mode == "single" else 0,
                'extractor_args': {
                    'youtube': {
                        'player_client': ['web', 'android', 'tv'],
                        'skip': ['dash', 'hls']
                    }
                }
            }

            with yt_dlp.YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                self._log("❌ Не удалось получить информацию о треке/плейлисте")
                self.parent._finish(ok=False, error="Не удалось получить информацию")
                return

            entries = []
            if mode == "single":
                if info.get('ie_key') == 'YoutubePlaylist':
                    entries = info.get('entries', [])[:1]
                else:
                    entries = [info]
            else:
                if info.get('ie_key') == 'YoutubePlaylist':
                    entries = list(info.get('entries', []))
                else:
                    entries = [info]

            valid_entries = [e for e in entries if e and (e.get('id') or e.get('url'))]

            if not valid_entries:
                self._log("❌ Нет доступных треков")
                self.parent._finish(ok=False, error="Нет доступных треков")
                return

            playlist_title = info.get('title', 'Без названия')
            if mode == "single" and valid_entries:
                first = valid_entries[0]
                playlist_title = f"{first.get('artist', 'Unknown')} - {first.get('title', 'Unknown')}"

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_title = clean_filename(playlist_title)[:80]
            playlist_dir = os.path.join(target_folder, f"{safe_title}_{timestamp}")
            os.makedirs(playlist_dir, exist_ok=True)

            with self.parent.lock:
                self.parent.stats["total"] = len(valid_entries)

            self.parent.update_progress_bar()

            if mode == "playlist":
                self._log(f"📁 Плейлист: {playlist_title}")
                DB.save_playlist(playlist_title, url, "youtube", playlist_dir)
                self.parent.gui_queue.put(("refresh_playlists", None))
            self._log(f"🎵 Всего треков: {self.parent.stats['total']}")
            self._log(f" ShipTones: Запуск в {self.parent.max_workers} потока...")

            with ThreadPoolExecutor(max_workers=self.parent.max_workers) as executor:
                futures = []
                for entry in valid_entries:
                    if self.parent.cancel_event.is_set():
                        break
                    futures.append(executor.submit(self.download_track, entry, playlist_dir, quality))

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        with self.parent.lock:
                            self.parent.stats["failed"] += 1
                        logger.exception("Download worker failed")
                        self._log(f"❌ Ошибка потока: {e}")

            # Обработка тегов и обложек
            self.parent._process_tags_and_covers(playlist_dir, valid_entries)

            if mode == "playlist" and not self.parent.cancel_event.is_set():
                DB.touch_playlist(url)
                self.parent.gui_queue.put(("refresh_playlists", None))

            if not self.parent.cancel_event.is_set():
                elapsed = time.time() - self.parent.start_time
                self._log("\n" + "═" * 40)
                self._log(f"✅ ShipTones: Скачано: {self.parent.stats['downloaded']}")
                self._log(f"🔁 Дубли: {self.parent.stats['duplicates']}")
                self._log(f"⏭ Пропущено: {self.parent.stats['skipped']}")
                self._log(f"❌ Ошибок: {self.parent.stats['failed']}")
                self._log(f"⏱ {int(elapsed//60)}м {int(elapsed%60)}с")
                self._log(f"📁 Папка: {playlist_dir}")
                self._log("═" * 40)
                self.parent._save_report(playlist_title, playlist_dir)
                self.parent._finish(ok=True)
            else:
                self._log("🛑 ShipTones: ЗАГРУЗКА ПРЕРВАНА")
                self.parent._finish(ok=True)

        except Exception as e:
            self._log(f"❌ Ошибка: {e}")
            logger.exception("Download failed")
            self.parent._finish(ok=False, error=str(e))
