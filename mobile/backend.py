"""Mobile (Android) бэкенд ShipTones.

Тонкая обёртка над существующими :mod:`downloader`, :mod:`database` и
:mod:`config` для работы внутри Chaquopy на Android.

Не импортирует ``webview`` / ``tkinter`` и не использует десктоп-специфичные
API (диалоги выбора папки, ``os.startfile``, автообновление .exe, drag&drop).
Все пути загрузки и БД находятся внутри приватной директории приложения.
"""

from __future__ import annotations

import json
import itertools
import os
import queue
import threading
from pathlib import Path
from typing import Callable, Optional

from config import APP_VERSION, SOURCES, detect_source, load_settings, save_settings, logger
from database import Database
from downloader import Downloader

try:  # check_internet из utils (сетевая проверка для статусной пилюли)
    from utils import check_internet
except Exception:  # pragma: no cover
    check_internet = None  # type: ignore[assignment]

# Аудио-форматы, которые понимает кодек-карта youtube_source.YouTubeDownloader.
_AUDIO_FORMATS = ("mp3", "aac", "flac", "opus")

# MIME-типы для медиа-хранилища (экспорт в Загрузки).
_MIME_BY_EXT = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/mp4",
    ".flac": "audio/flac", ".opus": "audio/ogg", ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}

# Маркер ffmpeg_path: ffmpeg НЕ запускается как процесс (на Android это запрещено),
# вместо этого все вызовы yt-dlp → ffmpeg перенаправляются на ffmpeg-kit через JNI
# (см. install_mobile_ffmpeg_shim). Значение должно быть правдиво для проверок
# вида `if not self.ffmpeg_path` в sources/youtube_source.py и ytmusic_source.py.
MOBILE_FFMPEG_MARKER = "__mobile_ffmpeg__"


# ============================================================
# МОСТ: yt-dlp ↔ ffmpeg-kit (JNI, только Chaquopy/Android)
# ============================================================
_SHIM_STATE = {"done": False, "ok": False}


def _is_android() -> bool:
    """Истина только внутри Chaquopy (в мостом `java`)."""
    try:
        from java import jclass  # доступен только в Chaquopy

        jclass("java.lang.System")
        return True
    except Exception:
        return False


def install_mobile_ffmpeg_shim() -> bool:
    """Направляет все вызовы ffmpeg из yt-dlp на ffmpeg-kit (JNI).

    На Android процесс subprocess для файлов в каталоге приложения запрещён,
    поэтому yt-dlp перенаправляется на встроенный ffmpeg-kit:
      - ``FFmpegPostProcessor.real_run_ffmpeg`` → ``FFmpegKit.execute(cmd)``;
      - ``get_audio_codec`` → ``FFprobeKit.getMediaInformation``;
      - сигнатуры доступности/версий отдаются статично (без запуска бинарников).
    Исполняется один раз, безопасно вызывается повторно.
    """
    if _SHIM_STATE["done"]:
        return _SHIM_STATE["ok"]
    _SHIM_STATE["done"] = True
    if not _is_android():
        return False

    try:
        from java import jclass

        _jffmpeg_kit = jclass("com.arthenica.ffmpegkit.FFmpegKit")
        _jffprobe_kit = jclass("com.arthenica.ffmpegkit.FFprobeKit")
    except Exception as e:
        logger.error(f"ffmpeg-kit недоступен: {e}")
        return False

    try:
        from yt_dlp.postprocessor import ffmpeg as _yff
        from yt_dlp.utils import encodeArgument, variadic
    except Exception as e:
        logger.error(f"yt-dlp postprocessor недоступен: {e}")
        return False

    def _quote_arg(text) -> str:
        text = str(text)
        if not text or any(c in text for c in ' \t"\r\n'):
            text = text.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{text}"'
        return text

    def _real_run(self_, input_path_opts, output_path_opts, *, expected_retcodes=(0,)):
        """Копия yt_dlp.postprocessor.ffmpeg.FFmpegPostProcessor.real_run_ffmpeg
        с заменой subprocess.Popen на FFmpegKit.execute (JNI)."""
        self_.check_version()

        oldest_mtime = min(
            os.stat(path).st_mtime for path, _ in input_path_opts if path)

        cmd = ["ffmpeg", "-y", "-loglevel", "repeat+info"]

        def make_args(file, args, name, number):
            keys = [f"_{name}{number}", f"_{name}"]
            if name == "o":
                args += ["-movflags", "+faststart"]
                if number == 1:
                    keys.append("")
            args += self_._configuration_args("ffmpeg", keys)
            if name == "i":
                args.append("-i")
            return (
                [encodeArgument(arg) for arg in args]
                + [self_._ffmpeg_filename_argument(file)]
            )

        for arg_type, path_opts in (("i", input_path_opts), ("o", output_path_opts)):
            cmd += itertools.chain.from_iterable(
                make_args(path, list(opts), arg_type, i + 1)
                for i, (path, opts) in enumerate(path_opts) if path)

        command_line = " ".join(_quote_arg(arg) for arg in cmd)
        session = _jffmpeg_kit.execute(command_line)
        rc = session.getReturnCode()
        retcode = rc.getValue() if rc is not None else -1
        if retcode not in variadic(expected_retcodes):
            detail = ""
            try:
                detail = session.getAllLogsAsString()
            except Exception:
                pass
            tail = (detail or "").strip().splitlines()
            raise _yff.FFmpegPostProcessorError(
                tail[-1] if tail else f"ffmpeg exited with code {retcode}")
        for out_path, _ in output_path_opts:
            if out_path:
                self_.try_utime(out_path, oldest_mtime, oldest_mtime)
        return ""

    def _get_audio_codec(self_, path):
        """Кодек аудиопотока через ffprobe ffmpeg-kit (замена subprocess-варианта)."""
        try:
            session = _jffprobe_kit.getMediaInformation(str(path))
            info = session.getMediaInformation()
            if info is None:
                return None
            for s in info.getStreams():
                if str(s.getType() or "").lower() == "audio":
                    codec = s.getCodec()
                    return str(codec) if codec else None
        except Exception as e:
            logger.debug(f"get_audio_codec failed: {e}")
        return None

    def _fake_version(self_, prog):
        return "6.0", {"fdk": False, "setts": False, "needs_adtstoasc": False}

    _yff.FFmpegPostProcessor.available = property(lambda self_: True)
    _yff.FFmpegPostProcessor.probe_available = property(lambda self_: False)
    _yff.FFmpegPostProcessor.executable = property(lambda self_: "ffmpeg")
    _yff.FFmpegPostProcessor.probe_executable = property(lambda self_: None)
    _yff.FFmpegPostProcessor.basename = property(lambda self_: "ffmpeg")
    _yff.FFmpegPostProcessor.probe_basename = property(lambda self_: None)
    _yff.FFmpegPostProcessor._version = property(lambda self_: "6.0")
    _yff.FFmpegPostProcessor._probe_version = property(lambda self_: "6.0")
    _yff.FFmpegPostProcessor._features = {"fdk": False, "setts": False, "needs_adtstoasc": False}
    _yff.FFmpegPostProcessor._get_ffmpeg_version = _fake_version
    _yff.FFmpegPostProcessor.real_run_ffmpeg = _real_run
    _yff.FFmpegPostProcessor.get_audio_codec = _get_audio_codec
    # Без ffprobe-хелпера:	ffprobe-пути (главы всё равно приходят из info-словаря).
    _yff.FFmpegPostProcessor._fixup_chapters = lambda self_, info: None

    logger.info("ffmpeg-kit JNI-шим установлен")
    _SHIM_STATE["ok"] = True
    return True


class Api:
    """Обёртка API для мобильного интерфейса (WebView → JS-мост → Python).

    Args:
        music_dir: папка для закачки аудио (на Android –
            ``getExternalFilesDir(DIRECTORY_MUSIC)``). Если не передана –
            используется домашняя папка пользователя (хост/тесты).
        event_callback: колбэк ``(kind, payload)`` для событий Python → JS.
    """

    def __init__(self, music_dir: Optional[str] = None, ffmpeg_path: Optional[str] = None,
                 event_callback: Optional[Callable[[str, object], None]] = None):
        install_mobile_ffmpeg_shim()
        self._settings = load_settings()
        self._android = _is_android()
        self.event_callback = event_callback
        self._downloader: Optional[Downloader] = None
        self._download_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._current_folder: Optional[str] = None
        self._js_diag_done = False

        # Директория загрузок — на Android приватная директория приложения.
        if music_dir:
            self.music_dir = Path(music_dir)
        else:
            self.music_dir = Path.home() / "ShipTonesMobile" / "Music"
        self.music_dir.mkdir(parents=True, exist_ok=True)

        # БД в приватной директории приложения (рядом с музыкой).
        self.db = Database(db_path=str(self.music_dir.parent / "shiptones.db"))

        # FFmpeg: на Android вызовы идут через ffmpeg-kit (JNI), путь — маркер;
        # на хосте — явно переданный путь (тесты), иначе автопоиск find_ffmpeg().
        if self._android:
            self.ffmpeg_path = MOBILE_FFMPEG_MARKER
        elif ffmpeg_path:
            self.ffmpeg_path = ffmpeg_path
        else:
            try:
                from utils import find_ffmpeg

                self.ffmpeg_path = find_ffmpeg()
            except Exception:
                self.ffmpeg_path = None
        logger.info(
            f"ShipTones Mobile Api: music_dir={self.music_dir} ffmpeg={self.ffmpeg_path}")

    # ---------------- источники / мета ----------------
    def get_sources(self):
        return {k: {"name": v["name"], "icon": v["icon"], "color": v["color"], "fg": v["fg"],
                    "in_dev": bool(v.get("in_dev"))}
                for k, v in SOURCES.items()}

    def detect_source(self, url):
        return detect_source(url)

    def get_app_version(self):
        return APP_VERSION

    def check_internet(self):
        if check_internet:
            try:
                return bool(check_internet())
            except Exception:
                return False
        return False

    # ---------------- настройки ----------------
    def settings(self, patch: Optional[dict | str] = None) -> dict:
        """Геттер/сеттер настроек. Без аргумента — вернуть текущие настройки.

        JS-мост приходит со строкой JSON (WebView → Kotlin → Python),
        поэтому строки парсятся здесь же.
        """
        if patch is None:
            return dict(self._settings)
        if isinstance(patch, str):
            try:
                patch = json.loads(patch) if patch.strip() else {}
            except Exception:
                return {"error": "invalid settings JSON"}
        if not isinstance(patch, dict):
            return {"error": "settings must be an object"}
        settings = dict(self._settings)
        for key, value in patch.items():
            if key == "max_workers":
                try:
                    value = max(1, min(8, int(value)))
                except (TypeError, ValueError):
                    continue
            if key == "quality" and value not in (
                    "0", "96", "128", "160", "192", "256", "320"):
                continue
            if key == "format" and value not in ("mp3", "aac", "flac", "opus"):
                continue
            if key == "download_dir" and not isinstance(value, str):
                continue
            settings[key] = value
        self._settings = settings
        save_settings(self._settings)
        return {"ok": True}

    # ---------------- история / плейлисты ----------------
    def history(self, search=""):
        return [list(row) for row in self.db.get_history(search)]

    def playlists(self):
        return [list(row) for row in self.db.get_playlists()]

    def delete_playlist(self, playlist_id):
        self.db.delete_playlist(playlist_id)
        return {"ok": True}

    # ---------------- загрузка ----------------
    def _is_busy(self):
        return bool(self._download_thread and self._download_thread.is_alive())

    def _emit(self, kind, payload):
        if self.event_callback:
            try:
                self.event_callback(kind, payload)
            except Exception as e:  # pragma: no cover
                logger.error(f"event_callback failed: {e}")

    def _diagnose_js_runtime(self) -> list:
        """Глубокая проверка JS-рантайма перед первой загрузкой (Android).

        yt-dlp молча деградирует без рантайма, поэтому печатаем в лог всё:
        путь nativeLibraryDir, его содержимое, права файла и фактический
        пробный запуск с тем же контрактом, каким пользуется yt-dlp
        (`--help`, затем `--script`).
        """
        if not self._android or self._js_diag_done:
            return []
        self._js_diag_done = True
        lines = []
        try:
            from config import android_native_library_dir
            lib_dir = android_native_library_dir()
        except Exception as e:
            return [f"⚠️ Diag: nativeLibraryDir недоступен: {e}"]
        if not lib_dir:
            lines = ["⚠️ Diag: nativeLibraryDir не определён — источники:"]
            try:
                from config import NATIVE_LIB_DEBUG
                for row in list(NATIVE_LIB_DEBUG)[:6]:
                    lines.append(f"🔎 Diag: {row}")
            except Exception:
                pass
            if len(lines) == 1:
                lines.append("🔎 Diag: ни один источник не вернул каталог")
            return lines
        lines.append(f"🔎 Diag: nativeLibraryDir = {lib_dir}")
        try:
            entries = sorted(os.listdir(lib_dir))
            lines.append(f"🔎 Diag: lib-файлов: {len(entries)} — "
                         f"{', '.join(entries[:6])}{'…' if len(entries) > 6 else ''}")
        except Exception as e:
            lines.append(f"⚠️ Diag: listdir не удался: {e}")
            return lines
        qjs = os.path.join(lib_dir, "libqjs.so")
        if not os.path.isfile(qjs):
            lines.append(
                "❌ Diag: libqjs.so ОТСУТСТВУЕТ в nativeLibraryDir — вероятно, "
                "установлена сборка без рантайма; поставь последнюю APK из Releases")
            return lines
        try:
            st = os.stat(qjs)
            mode = oct(st.st_mode & 0o777)
            f_ok = os.access(qjs, os.F_OK)
            x_ok = os.access(qjs, os.X_OK)
            lines.append(f"🔎 Diag: libqjs.so mode={mode} F_OK={f_ok} X_OK={x_ok}")
        except Exception as e:
            lines.append(f"⚠️ Diag: stat не удался: {e}")
            return lines
        # Пробный запуск №1: контракт yt-dlp для определения версии
        try:
            import subprocess as _sp
            r = _sp.run([qjs, "--help"], capture_output=True, text=True, timeout=12)
            ver = next((ln.strip() for ln in (r.stdout or "").splitlines()
                        if "QuickJS" in ln), "")
            if ver:
                lines.append(f"🧩 Diag: `--help` ок → {ver}")
            else:
                lines.append(f"⚠️ Diag: `--help` rc={r.returncode}, версия в выводе нет")
        except PermissionError:
            lines.append("🚫 Diag: запуск libqjs.so запрещён системой (exec denied) — "
                         "подключи телефон по USB, посмотрю логи и соберу обходной вариант")
            return lines
        except Exception as e:
            lines.append(f"🚫 Diag: пробный запуск не удался: {type(e).__name__}: {e}")
            return lines
        # Пробный запуск №2: контракт yt-dlp для скриптов (--script,
        # успех = rc 0 И пустой stderr)
        try:
            import subprocess as _sp
            import tempfile as _tf
            with _tf.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write('print("shiptones-js-ok");')
                tmp = f.name
            r = _sp.run([qjs, "--script", tmp], capture_output=True, text=True, timeout=12)
            os.unlink(tmp)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            ok = (r.returncode == 0 and out == "shiptones-js-ok" and not err)
            if ok:
                lines.append("🧩 Diag: `--script` ок — решатель челленджей должен работать")
            else:
                lines.append(f"⚠️ Diag: `--script` rc={r.returncode} out={out!r} err={err[:120]!r}")
        except Exception as e:
            lines.append(f"⚠️ Diag: --script проба не удалась: {type(e).__name__}: {e}")
        # Пакет yt-dlp-ejs поставляет yt.solver.lib.js/core.min.js для
        # QuickJS-провайдера. Без него yt-dlp 2026.10.26 не решает sig/n,
        # и каждый трек падает на скачивании.
        try:
            import yt_dlp_ejs
            lines.append(
                f"🧩 Diag: yt-dlp-ejs {getattr(yt_dlp_ejs, 'version', '?')} — "
                "решатель челленджей на месте")
        except Exception as e:
            lines.append(f"⚠️ Diag: пакет yt-dlp-ejs отсутствует: {type(e).__name__}: {e}")
        return lines

    def start_download(self, url: str, fmt: str = "aac", mode: str = None) -> dict:
        """Начать загрузку одного трека или плейлиста.

        ``mode``: "single" | "playlist" — приходит из UI-переключателя
        («Один трек / Плейлист»). Если не передан, определяется по URL:
        ссылка с list=/playlist считается плейлистом.

        Возвращает ``{"ok": True}`` или ``{"error": ...}``.
        События прогресса транслируются через ``event_callback`` в JS.
        """
        if self._is_busy():
            return {"error": "Загрузка уже идёт"}
        if not url or not str(url).strip():
            return {"error": "Введите ссылку"}

        # Диагностика JS-рантайма (однократно за сессию, до старта таски) —
        # без неё падение sig/n-челленджа видно как «Ошибка: …» без причины.
        for diag_line in self._diagnose_js_runtime():
            self._emit("log", diag_line)

        url = str(url).strip()
        fmt = (fmt or self._settings.get("format", "aac")).lower()
        if fmt not in _AUDIO_FORMATS:
            fmt = "aac"
        quality = str(self._settings.get("quality", "256"))
        # Режим из UI (PyBridge → сюда); без него — эвристика по ссылке,
        # чтобы плейлист из 14 треков больше не скачивался как один.
        mode = (mode or "").lower()
        if mode not in ("single", "playlist"):
            low = url.lower()
            mode = ("playlist"
                    if ("list=" in low or "playlist" in low or "/sets/" in low)
                    else "single")

        self._settings["format"] = fmt
        self._settings["quality"] = quality
        save_settings(self._settings)

        target_dir = self.music_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        try:
            workers = int(self._settings.get("max_workers", 4))
        except (TypeError, ValueError):
            workers = 4
        workers = max(1, min(8, workers))

        gui_queue = queue.Queue()
        try:
            downloader = Downloader(gui_queue, self._settings, max_workers=workers)
            # На Android ffmpeg вызывается через ffmpeg-kit (JNI), маркер
            # проходит проверки источников `if not self.ffmpeg_path`.
            # На хосте/тестах — обычный автонайденный путь.
            downloader.ffmpeg_path = (
                MOBILE_FFMPEG_MARKER if self._android else self.ffmpeg_path)
        except Exception as e:
            logger.exception("Failed to create Downloader")
            self._emit("done", {"ok": False, "canceled": False, "error": str(e)})
            return {"error": str(e)}

        self._downloader = downloader

        def _run():
            try:
                downloader.download(url, mode, str(target_dir), quality, "ytmusic")
            except Exception as e:  # pragma: no cover
                logger.exception("Download thread crashed")
                self._emit("done", {"ok": False, "canceled": False, "error": str(e)})

        def _pump():
            while True:
                try:
                    kind, payload = gui_queue.get()
                except Exception:
                    break
                if kind == "done" and isinstance(payload, dict):
                    self._current_folder = payload.get("folder")
                # file_done — новый файл скачан: копируем в Загрузки сразу
                if (kind == "file_done" and isinstance(payload, str)
                        and self._settings.get("export_to_downloads", True)):
                    threading.Thread(
                        target=self.export_files_to_downloads,
                        args=([payload],), daemon=True).start()
                self._emit(kind, payload)
                if kind == "done" and isinstance(payload, dict):
                    # Успешное (не отменённое) завершение → экспорт в Загрузки.
                    ok = bool(payload.get("ok")) and not payload.get("canceled")
                    snapshot = list(getattr(self._downloader, "downloaded_files", []) or [])
                    if ok and snapshot and self._settings.get("export_to_downloads", True):
                        threading.Thread(
                            target=self.export_files_to_downloads,
                            args=(snapshot,), daemon=True).start()
                if kind == "done":
                    break

        self._download_thread = threading.Thread(target=_run, daemon=True)
        pump_thread = threading.Thread(target=_pump, daemon=True)
        self._download_thread.start()
        pump_thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        if self._downloader:
            self._downloader.stop()
        return {"ok": True}

    def open_current_folder(self) -> dict:
        # На Android папка фиксирована (приватная директория приложения).
        return {"ok": True, "path": str(self.music_dir)}

    def download_dir(self) -> str:
        return str(self.music_dir)

    # ---------------- экспорт в Загрузки ----------------
    def export_files_to_downloads(self, paths) -> None:
        """Копирует скачанные треки в системную папку «Загрузки/ShipTones»

        Пользователь не может указать произвольную папку назначения
        (Android запрещает это обычным приложениям), поэтому лучший
        видимый путь — MediaStore.Downloads: файлы появляются в
        файловом менеджере и в любом музыкальном плеере сразу после
        загрузки. Внутренняя приватная копия остаётся (нужна для истории
        и проверки дубликатов).
        Вызывается после успешного завершения загрузки; сам по себе
        безопасен — каждая ошибка файла гасится и попадает в лог.
        """
        if not self._android or not paths:
            return
        try:
            exported, names, errors = self._media_store_copy(paths)
        except Exception as e:  # Chaquopy/java недоступны — тихий выход в лог
            logger.warning(f"export to downloads failed: {e}")
            self._emit("log", f"⚠️ Экспорт в Загрузки не удался: {e}")
            return
        if exported:
            msg = (f"📁 {Path(paths[0]).name}: сохранено в Загрузки/ShipTones"
                   if exported == 1 and len(paths) == 1 else
                   f"📁 Экспортировано в Загрузки/ShipTones: {exported}")
            self._emit("log", msg)
        if names:
            self._emit("log", f"🔁 В Загрузках уже есть: {', '.join(names[:3])}"
                              + (f" +{len(names) - 3}" if len(names) > 3 else ""))
        if errors:
            self._emit("log", f"⚠️ Не экспортировано: {'; '.join(errors[:2])}")

    def _media_store_copy(self, paths):
        """Через MediaStore.Downloads (API 29+) — без разрешений и SAF."""
        from com.chaquo.python.android import AndroidPlatform
        from java import jclass

        app = AndroidPlatform.getApplication()
        sdk_int = jclass("android.os.Build$VERSION").SDK_INT
        if int(sdk_int) < 29:
            return 0, [], []
        media_store = jclass("android.provider.MediaStore")
        values_cls = jclass("android.content.ContentValues")
        media_cols = jclass("android.provider.MediaStore$MediaColumns")
        downloads_uri = (jclass("android.provider.MediaStore$Downloads")
                         .EXTERNAL_CONTENT_URI)
        resolver = app.getContentResolver()
        rel_path = "Download/ShipTones/"

        exported, skipped, errors = 0, [], []
        for file_path in paths:
            name = Path(file_path).name
            try:
                # Дедупликация: не плодим копий (1), (2)… при повторной
                # синхронизации плейлиста — если файл с таким именем уже
                # лежит в Загрузках/ShipTones, пропускаем.
                cursor = resolver.query(
                    downloads_uri,
                    [str(media_cols.DISPLAY_NAME)],
                    f"{media_cols.RELATIVE_PATH} = ? AND {media_cols.DISPLAY_NAME} = ?",
                    (rel_path, name), None)
                exists = bool(cursor.moveToFirst())
                cursor.close()
                if exists:
                    skipped.append(name)
                    continue

                values = values_cls()
                values.put(media_cols.DISPLAY_NAME, name)
                values.put(media_cols.MIME_TYPE,
                           _MIME_BY_EXT.get(Path(file_path).suffix.lower(),
                                            "application/octet-stream"))
                values.put(media_cols.RELATIVE_PATH, rel_path)
                uri = resolver.insert(downloads_uri, values)
                if uri is None:
                    raise RuntimeError("MediaStore insert вернул null")
                stream = resolver.openOutputStream(uri)
                if stream is None:
                    raise RuntimeError("openOutputStream вернул null")
                with open(file_path, "rb") as src:
                    while True:
                        chunk = src.read(512 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                stream.flush()
                stream.close()
                exported += 1
            except Exception as e:
                errors.append(f"{name}: {e}")
        return exported, skipped, errors

    def share_file(self, track_id) -> dict:
        """Данные файла трека для системного «Поделиться» (FileProvider в Kotlin).

        На Android папка загрузок приватна (getExternalFilesDir — обычные
        файловые менеджеры её не видят), поэтому пользователь может вынести
        скачанный трек в любое приложение через системное «Поделиться».
        """
        try:
            track_id = int(track_id)
        except (TypeError, ValueError):
            return {"error": "invalid track id"}
        file_path, title = self.db.get_track_file(track_id)
        if not file_path:
            return {"error": "Трек не найден"}
        if not Path(file_path).exists():
            return {"error": "Файл не найден (удалён или перемещён)"}
        return {"ok": True, "path": str(file_path), "title": title}


def _make_js_callback(bridge):
    """Создаёт колбэк, пересылающий события в JS через Kotlin-мост (PyBridge.emit).

    Kotlin встраивает payload в JS-вызов как ГОТОВЫЙ литерал, поэтому payload
    здесь всегда сериализуется в корректный JSON.
    """

    def _callback(kind, payload):
        try:
            if isinstance(payload, (dict, list, tuple)):
                payload = json.dumps(payload, ensure_ascii=False)
            elif payload is None:
                payload = "null"
            elif isinstance(payload, bool):
                payload = "true" if payload else "false"
            elif isinstance(payload, (int, float)):
                payload = str(payload)
            else:
                payload = json.dumps(str(payload), ensure_ascii=False)
        except Exception:
            payload = "{}"
        bridge.emit(str(kind), payload)

    return _callback
