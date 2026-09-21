"""
Модуль загрузки с YouTube Music
Специализированный экстрактор для music.youtube.com
"""

import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from config import logger
from database import DB
from utils import clean_filename, normalize_url
from .youtube_source import YouTubeDownloader


class YouTubeMusicDownloader(YouTubeDownloader):
    """Загрузчик треков и плейлистов с YouTube Music"""
    
    def download(self, url, mode, target_folder, quality):
        """Основной метод загрузки с YouTube Music"""
        if not self.ffmpeg_path:
            self._log("❌ FFmpeg не найден!")
            self.parent._finish(ok=False, error="FFmpeg не найден")
            return

        os.makedirs(target_folder, exist_ok=True)

        try:
            self._log("🎵 ShipTones: Получение информации о YouTube Music...")
            url = normalize_url(url)

            # Специфичные настройки для YouTube Music
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
                self._log(f"📁 Плейлист YouTube Music: {playlist_title}")
                DB.save_playlist(playlist_title, url, "ytmusic", playlist_dir)
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
