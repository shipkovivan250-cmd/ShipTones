import io
import os
import re
import string
import subprocess
import sys
import urllib.request

from config import BASE_DIR, logger

try:
    from PIL import Image
except ImportError:
    Image = None

# ============================================================
# СИСТЕМНЫЕ УТИЛИТЫ
# ============================================================
def find_removable_drives():
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
    cleaned = cleaned[:100]
    return cleaned if cleaned else fallback

def normalize_url(url: str) -> str:
    """Переписывает ТОЛЬКО youtube-домены, не ломает ytsearch1:-запросы"""
    url = url.strip()
    url = re.sub(r'://www\.youtube\.com', '://youtube.com', url)
    url = re.sub(r'://music\.youtube\.com', '://youtube.com', url)
    return url

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
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.getheader('Content-Length'):
                    if int(response.getheader('Content-Length')) > 10000:
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

        img = Image.open(io.BytesIO(img_data)).convert('RGB')
        img_resized = img.resize((500, 500), Image.Resampling.LANCZOS)

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

        from mutagen.mp3 import MP3
        from mutagen.id3 import APIC
        audio = MP3(mp3_path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall('APIC')
        audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=jpeg_data))
        audio.save()
        return True
    except Exception as e:
        logger.error(f"Ошибка обложки {mp3_path}: {e}")
        return False