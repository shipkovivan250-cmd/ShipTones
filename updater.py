import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from config import APP_VERSION, GITHUB_REPO, logger

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(v):
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def check_for_update(timeout=8):
    """Возвращает {version, notes, url, size}, если на GitHub есть релиз новее
    текущей версии, иначе None. Никогда не бросает исключение — обрыв сети
    или отсутствие релизов просто означает "обновлений нет"."""
    try:
        req = urllib.request.Request(
            API_URL,
            headers={"User-Agent": "ShipTones-Updater", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)

        tag = data.get("tag_name", "")
        if _parse_version(tag) <= _parse_version(APP_VERSION):
            return None

        asset = next((a for a in data.get("assets", []) if a.get("name", "").lower().endswith(".exe")), None)
        if not asset:
            return None

        return {
            "version": tag,
            "notes": (data.get("body") or "")[:500],
            "url": asset["browser_download_url"],
            "size": asset.get("size", 0),
        }
    except Exception as e:
        logger.info(f"check_for_update: обновление недоступно ({e})")
        return None


def download_update(url, on_progress=None, timeout=15):
    """Скачивает exe новой версии во временный файл и возвращает путь к нему."""
    fd, dest = tempfile.mkstemp(prefix="ShipTones_update_", suffix=".exe")
    os.close(fd)
    req = urllib.request.Request(url, headers={"User-Agent": "ShipTones-Updater"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
            total = int(resp.getheader("Content-Length") or 0)
            read = 0
            chunk_size = 256 * 1024
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                read += len(chunk)
                if on_progress and total:
                    on_progress(min(100, read * 100 // total))
    except Exception:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise
    return dest


def apply_update_and_restart(new_exe_path):
    """Готовит замену текущего exe новым файлом и перезапуск, затем возвращает
    управление — вызывающий код должен сам закрыть окно/выйти из приложения.
    Реальная замена происходит в отдельном .bat ПОСЛЕ выхода процесса (Windows
    не даёт перезаписать exe, который сам сейчас исполняется)."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление доступно только в собранном .exe, не при запуске из исходников")

    current_exe = sys.executable
    bat_fd, bat_path = tempfile.mkstemp(prefix="ShipTones_update_", suffix=".bat")
    os.close(bat_fd)

    # ФИКС: не гадаем с фиксированной паузой — ждём в цикле, пока файл exe не
    # освободится (процесс мог не успеть закрыться за 1-2 секунды на медленной машине).
    script = (
        "@echo off\r\n"
        f'set "NEWFILE={new_exe_path}"\r\n'
        f'set "OLDFILE={current_exe}"\r\n'
        ":retry\r\n"
        'move /y "%NEWFILE%" "%OLDFILE%" >nul 2>&1\r\n'
        'if exist "%NEWFILE%" (\r\n'
        "  timeout /t 1 /nobreak >nul\r\n"
        "  goto retry\r\n"
        ")\r\n"
        'start "" "%OLDFILE%"\r\n'
        'del "%~f0"\r\n'
    )
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(script)

    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
