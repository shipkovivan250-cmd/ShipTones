import io
import os
import re
import string
import subprocess
import sys
import urllib.request

from config import BASE_DIR, logger

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None

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
# ОБЛОЖКИ (автомобильно-дружелюбные: 500×500 JPEG baseline ≤150 КБ)
# ============================================================

# Кэш «для этого видео нет maxresdefault», чтобы не слать HEAD-запросы
# по одному и тому же video_id при каждом треке плейлиста.
_NO_MAXRES_CACHE: set[str] = set()


def get_youtube_thumbnail_url(video_id: str) -> str:
    """URL обложки YouTube.
    hqdefault.jpg (480×360) существует у любого видео — отдаём его сразу,
    без цикла из 3 HEAD-запросов. maxresdefault пробуем одним коротким
    HEAD-запросом как апгрейд качества; отрицательный результат кэшируем
    по video_id, чтобы не повторять неудачные попытки.
    """
    hq_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    if video_id in _NO_MAXRES_CACHE:
        return hq_url
    maxres_url = f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
    try:
        req = urllib.request.Request(maxres_url, headers={'User-Agent': 'Mozilla/5.0'}, method='HEAD')
        with urllib.request.urlopen(req, timeout=3) as response:
            length = response.getheader('Content-Length')
            if length is None or int(length) > 10000:
                return maxres_url
    except Exception:
        _NO_MAXRES_CACHE.add(video_id)
    return hq_url


def prepare_cover_jpeg(image_data: bytes, max_kb: int = 150, size=(500, 500)) -> bytes | None:
    """Общий пайплайн обложки: обрезка по центру (ImageOps.fit) в 500×500,
    JPEG baseline с деградацией качества до ≤ max_kb КБ.
    Возвращает готовые JPEG-байты или None (Pillow не установлен, битая
    картинка или не уложились в лимит размера).
    """
    if Image is None or ImageOps is None:
        return None
    try:
        img = Image.open(io.BytesIO(image_data)).convert('RGB')
        # YouTube-превью не квадратные (обычно 16:9) — ImageOps.fit обрезает
        # по центру, сохраняя пропорции, без искажений.
        # ФИКС: Image.Resampling появился только с Pillow >= 9.1 — на старых
        # версиях (например, старый Pillow в Chaquopy/Android) используем Image.LANCZOS.
        Resampling = getattr(Image, "Resampling", Image)
        img = ImageOps.fit(img, size, Resampling.LANCZOS)
        quality = 85
        jpeg_data = b""
        while quality >= 20:
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=quality, optimize=True, subsampling=2)
            jpeg_data = buf.getvalue()
            if len(jpeg_data) / 1024 <= max_kb:
                break
            quality -= 10
        if len(jpeg_data) / 1024 > max_kb:
            return None
        return jpeg_data
    except Exception as e:
        logger.error(f"Ошибка подготовки обложки: {e}")
        return None


# --- низкоуровневое встраивание уже подготовленного JPEG (без сети) -------

def _embed_mp3_cover(audio_path: str, jpeg_data: bytes) -> bool:
    from mutagen.mp3 import MP3
    from mutagen.id3 import APIC
    audio = MP3(audio_path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags.delall('APIC')
    audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=jpeg_data))
    # ID3v2.3 — читается старыми ГУ (LADA EnjoY Pro), v2.4 может игнорироваться.
    audio.save(v2_version=3)
    return True


def _embed_mp4_cover(audio_path: str, jpeg_data: bytes) -> bool:
    from mutagen.mp4 import MP4, MP4Cover
    audio = MP4(audio_path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags['covr'] = [MP4Cover(jpeg_data, imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()
    return True


def _embed_opus_cover(audio_path: str, jpeg_data: bytes) -> bool:
    from mutagen.flac import Picture
    from mutagen.oggopus import OggOpus
    import base64
    pic = Picture()
    pic.type = 3
    pic.mime = 'image/jpeg'
    pic.desc = 'Cover'
    pic.data = jpeg_data
    pic.width, pic.height, pic.depth = 500, 500, 24
    audio = OggOpus(audio_path)
    audio.tags['METADATA_BLOCK_PICTURE'] = [base64.b64encode(pic.write()).decode('ascii')]
    audio.save()
    return True


def _embed_flac_cover(audio_path: str, jpeg_data: bytes) -> bool:
    from mutagen.flac import FLAC, Picture
    pic = Picture()
    pic.type = 3
    pic.mime = 'image/jpeg'
    pic.desc = 'Cover'
    pic.data = jpeg_data
    pic.width, pic.height, pic.depth = 500, 500, 24
    audio = FLAC(audio_path)
    audio.clear_pictures()
    audio.add_picture(pic)
    audio.save()
    return True


def embed_cover_jpeg(audio_path: str, jpeg_data: bytes) -> bool:
    """Встроить подготовленные JPEG-байты в файл подходящим для формата способом.
    .webm (формат Original) — обложка невозможна без вложений Matroska,
    явно пропускаем с информацией в лог (без спама ошибок).
    """
    ext = os.path.splitext(audio_path)[1].lower()
    try:
        if ext == '.mp3':
            return _embed_mp3_cover(audio_path, jpeg_data)
        if ext in ('.m4a', '.aac', '.mp4'):
            return _embed_mp4_cover(audio_path, jpeg_data)
        if ext in ('.opus', '.ogg'):
            return _embed_opus_cover(audio_path, jpeg_data)
        if ext == '.flac':
            return _embed_flac_cover(audio_path, jpeg_data)
        if ext in ('.webm', '.mkv'):
            logger.info(
                f"Обложка пропущена для {os.path.basename(audio_path)}: "
                f"формат {ext} не хранит вложенные обложки (Matroska).")
            return False
        logger.warning(
            f"Обложка: неподдерживаемый формат {ext} для {os.path.basename(audio_path)}")
        return False
    except Exception as e:
        logger.error(f"Ошибка обложки {os.path.basename(audio_path)}: {e}")
        return False


def fetch_cover_jpeg(video_id: str) -> bytes | None:
    """Скачать обложку YouTube и подготовить JPEG (параллельно скачиванию).
    Возвращает готовые JPEG-байты или None — обложка пропущена для трека.
    """
    try:
        url = get_youtube_thumbnail_url(video_id)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            img_data = response.read()
        return prepare_cover_jpeg(img_data)
    except Exception as e:
        logger.info(f"Обложка не получена для {video_id}: {e}")
        return None


def embed_car_friendly_cover(audio_path: str, image_url: str) -> bool:
    """Обложка для автомобильной магнитолы: скачать → подготовить → встроить.
    Сохранена для обратной совместимости и ручного вызова.
    """
    if Image is None:
        return False
    try:
        if not os.path.exists(audio_path):
            return False
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            img_data = response.read()
        jpeg_data = prepare_cover_jpeg(img_data)
        if jpeg_data is None:
            logger.warning(
                f"Обложка >{150}KB для {os.path.basename(audio_path)}. Пропуск.")
            return False
        return embed_cover_jpeg(audio_path, jpeg_data)
    except Exception as e:
        logger.error(f"Ошибка обложки {os.path.basename(audio_path)}: {e}")
        return False