import io
import os
import re
import string
import subprocess
import sys
import urllib.request

from config import BASE_DIR, logger

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".flac", ".opus", ".ogg", ".webm")

try:
    from PIL import Image
except ImportError:
    Image = None

# ============================================================
# СИСТЕМНЫЕ УТИЛИТЫ
# ============================================================
def find_removable_drives():
    """ФИКС: os.path.exists() на сетевом диске может зависнуть на много секунд,
    если сеть недоступна. GetLogicalDrives() читает битовую маску из ОС
    напрямую, не трогая файловую систему — не может зависнуть."""
    if sys.platform == "win32":
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            drives = [f"{chr(65 + i)}:" for i in range(26) if bitmask & (1 << i)]
            return drives or ["❌ Флешка не найдена"]
        except Exception:
            pass

    drives = []
    for letter in string.ascii_uppercase:
        if os.path.exists(f"{letter}:\\"):
            drives.append(f"{letter}:")
    return drives or ["❌ Флешка не найдена"]

def check_internet(timeout=5):
    try:
        urllib.request.urlopen("https://www.youtube.com", timeout=timeout)
        return True
    except Exception:
        return False

def find_ffmpeg():
    """ФИКС: yt-dlp требует ПАПКУ с ffmpeg, а не путь к exe.
    shutil.which возвращает exe → берём os.path.dirname."""
    import glob as _glob
    candidates = [
        getattr(sys, "_MEIPASS", ""),  # папка, куда PyInstaller распаковывает вшитые файлы (onefile)
        str(BASE_DIR),
        r"C:\ffmpeg\bin",
        r"C:\Program Files\ffmpeg\bin",
        r"G:\ffmpeg-8.1.2-essentials_build\bin",
        r"D:\ffmpeg\bin",
    ]
    # динамически ищем папки ffmpeg-* рядом с программой
    candidates += _glob.glob(str(BASE_DIR / "ffmpeg-*"))

    for path in candidates:
        ff = os.path.join(path, "ffmpeg.exe")
        if os.path.isfile(ff):
            return path  # возвращаем ПАПКУ

    import shutil
    exe_path = shutil.which("ffmpeg")
    if exe_path:
        return os.path.dirname(exe_path)  # ФИКС: только папка!
    return None

def update_ytdlp():
    """Фишка 14: авто-обновление yt-dlp. Возвращает (ok, сообщение)."""
    if getattr(sys, "frozen", False):
        return False, "в .exe версия yt-dlp зашита — для обновления пересобери exe"
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                           capture_output=True, text=True, timeout=180)
        last = (r.stdout or r.stderr or "").strip().splitlines()
        return r.returncode == 0, (last[-1] if last else "готово")
    except Exception as e:
        return False, str(e)

# ============================================================
# СТРОКИ И ХЭШИ
# ============================================================
def clean_filename(name: str, fallback: str = "Unknown Track") -> str:
    if not name:
        return fallback
    cleaned = "".join(c for c in name if c.isalnum() or c in ' _-()[]\'').strip()
    cleaned = cleaned[:100].rstrip(" ")
    return cleaned if cleaned else fallback

def normalize_url(url: str) -> str:
    """Переписывает ТОЛЬКО youtube-домены, не ломает ytsearch1:-запросы"""
    url = url.strip()
    url = re.sub(r'://www\.youtube\.com', '://youtube.com', url)
    url = re.sub(r'://music\.youtube\.com', '://youtube.com', url)
    return url

def find_audio_file(directory, stem):
    """Возвращает путь к уже записанному аудиофайлу, независимо от расширения,
    которое yt-dlp/ffmpeg фактически выбрали."""
    for ext in AUDIO_EXTENSIONS:
        path = os.path.join(directory, f"{stem}{ext}")
        if os.path.isfile(path) and os.path.getsize(path) > 1024:
            return path
    return None


def open_folder(path):
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception as e:
        logger.error(f"Не удалось открыть папку {path}: {e}")
        return False


def hash_file_fast(path, chunk_size: int = 2 * 1024 * 1024):
    """Хэш начало+конец файла — защита от коллизий одинаковых заголовков"""
    from pathlib import Path
    import hashlib
    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.md5()
    try:
        size = p.stat().st_size
        with open(p, 'rb') as f:
            h.update(f.read(chunk_size))
            if size > chunk_size * 2:
                f.seek(-chunk_size, 2)
                h.update(f.read(chunk_size))
        return h.hexdigest()
    except Exception as e:
        logger.error(f"Ошибка хэширования {p}: {e}")
        return None

# ============================================================
# ОБЛОЖКИ
# ============================================================
def get_youtube_thumbnail_url(video_id: str) -> str:
    variants = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for url in variants:
        try:
            # ФИКС: HEAD вместо GET — не тянем тело картинки только чтобы узнать её размер.
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}, method='HEAD')
            with urllib.request.urlopen(req, timeout=5) as response:
                length = response.getheader('Content-Length')
                # ФИКС: если сервер не прислал Content-Length, не отбрасываем
                # картинку молча — она может быть валидной.
                if length is None or int(length) > 10000:
                    return url
        except Exception:
            continue
    return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"

def embed_car_friendly_cover(mp3_path: str, image_url: str) -> bool:
    """Обложка 500x500 JPEG <=150KB — магнитола не зависнет"""
    if Image is None:
        return False
    try:
        if not os.path.exists(mp3_path):
            return False

        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            img_data = response.read()

        from PIL import ImageOps

        img = Image.open(io.BytesIO(img_data)).convert('RGB')
        # ФИКС: YouTube-превью не квадратные (обычно 16:9) — обычный resize
        # искажал картинку. ImageOps.fit обрезает по центру, сохраняя пропорции.
        img_resized = ImageOps.fit(img, (500, 500), Image.Resampling.LANCZOS)

        quality = 85
        jpeg_data = b""
        while quality >= 20:
            buf = io.BytesIO()
            img_resized.save(buf, format='JPEG', quality=quality, optimize=True, subsampling=2)
            jpeg_data = buf.getvalue()
            if len(jpeg_data) / 1024 <= 150:
                break
            quality -= 10

        if len(jpeg_data) / 1024 > 150:
            logger.warning(f"Обложка >150KB для {mp3_path}. Пропуск.")
            return False

        ext = os.path.splitext(mp3_path)[1].lower()
        if ext == ".mp3":
            from mutagen.mp3 import MP3
            from mutagen.id3 import APIC
            audio = MP3(mp3_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg_data))
            audio.save()
            return True
        if ext in (".m4a", ".aac", ".mp4"):
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(mp3_path)
            audio["covr"] = [MP4Cover(jpeg_data, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
            return True
        if ext == ".flac":
            from mutagen.flac import FLAC, Picture
            audio = FLAC(mp3_path)
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = jpeg_data
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()
            return True
        if ext in (".opus", ".ogg"):
            import base64
            from mutagen.flac import Picture
            from mutagen.oggopus import OggOpus
            pic = Picture()
            pic.type = 3
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = jpeg_data
            audio = OggOpus(mp3_path)
            audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            audio.save()
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обложки {mp3_path}: {e}")
        return False