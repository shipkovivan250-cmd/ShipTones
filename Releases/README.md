# 📦 ShipTones - Сборка и Публикация

## ✅ Готово к сборке!

Я настроил автоматическую сборку Windows EXE через GitHub Actions.

---

## 🚀 Как собрать EXE файл

### Вариант 1: Автоматически через GitHub Actions (Рекомендуется)

1. **Создайте тег версии** (например, v1.0.0):
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. **GitHub автоматически:**
   - Запустит сборку на Windows
   - Скачает FFmpeg
   - Соберёт ShipTones.exe со всеми зависимостями
   - Создаст релиз на GitHub с прикреплённым файлом

3. **Или запустите вручную:**
   - Перейдите в Actions → Build ShipTones for Windows
   - Нажмите "Run workflow"
   - Скачайте артефакт после завершения

---

### Вариант 2: Локально на Windows

```powershell
# Установите зависимости
pip install -r requirements.txt
pip install pyinstaller

# Скачайте FFmpeg
Invoke-WebRequest -Uri "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile ffmpeg.zip
Expand-Archive ffmpeg.zip -DestinationPath ffmpeg_extracted
$ffmpegExe = Get-ChildItem -Path ffmpeg_extracted -Recurse -Filter ffmpeg.exe | Select-Object -First 1
Copy-Item $ffmpegExe.FullName -Destination ffmpeg.exe

# Соберите EXE
pyinstaller --onefile --windowed --name ShipTones `
  --add-binary "ffmpeg.exe;." `
  --add-data "web;web" `
  --hidden-import=yt_dlp `
  --hidden-import=mutagen `
  --hidden-import=PIL `
  --hidden-import=mutagen.mp3 `
  --hidden-import=mutagen.mp4 `
  --hidden-import=mutagen.flac `
  --hidden-import=mutagen.ogg `
  --collect-all pywebview `
  --collect-all pythonnet `
  --collect-all clr_loader `
  --clean `
  --noconfirm `
  main.py

# Готовый файл будет в: dist\ShipTones.exe
```

---

## 🤖 Сборка Android APK

Приложение под Android — это тот же Python-бэкенд (yt-dlp + Chaquopy) внутри WebView.
Сборка полностью автоматизирована, тулчейн ставится сам при первом запуске.

### Сборка одной командой

```powershell
powershell -ExecutionPolicy Bypass -File .\build-android.ps1
```

Скрипт сам:
1. Устанавливает JDK 17, Gradle 8.7 и Android SDK (platform 34, build-tools 34.0.0) в `~\tools\` — если их нет.
2. Скачивает ffmpeg-kit full-gpl AAR в `android\libs\` — если его нет.
3. Запускает `gradle assembleDebug`.
4. Копирует APK в `Releases\ShipTones-<версия>-android-debug.apk`.

Повторные запуски работают офлайн и занимают секунды. Для release-сборки — `gradle -p android assembleRelease` (нужен собственный keystore, debug подписан автоматически).

### Что внутри APK

| Компонент | Описание |
|-----------|----------|
| **Chaquopy 17.2.0** | CPython 3.13 внутри приложения (Python-код в APK уже откомпилирован в `.pyc`) |
| **yt-dlp, mutagen, Pillow** | ставятся в APK как pip-зависимости через Chaquopy |
| **ffmpeg-kit full-gpl** | JNI-библиотека FFmpeg (`libavcodec`, `libavfilter` и др.) — subprocess на Android запрещён, поэтому `mobile/backend.py` перенаправляет ffmpeg-вызовы yt-dlp на FFmpegKit |
| **Архитектуры** | `arm64-v8a` (реальные устройства) + `x86_64` (эмулятор) |
| **Размер** | ~139 МБ (обусловлено ffmpeg-kit full-gpl под 2 архитектуры) |

### Загрузки сохраняются в приватную папку приложения

`Android/data/com.shiptones.app/files/Music` — app-scoped директория, не требующая разрешений на хранилище.

### Требования к устройству

- Android 7.0+ (minSdk 24)
- 64-битная архитектура (arm64-v8a или x86_64)

### Gradle-команды (если нужен полный контроль)

```powershell
$env:JAVA_HOME = "$env:USERPROFILE\tools\jdk\jdk17"
$env:ANDROID_HOME = "$env:USERPROFILE\tools\android-sdk"
& "$env:USERPROFILE\tools\gradle\gradle-8.7\bin\gradle.bat" -p "$env:USERPROFILE\Projects\ShipTones\android" assembleDebug
```

> Python-исходники для APK фильтруются gradle-таской `syncPythonSources` (whitelist:
> `config.py`, `database.py`, `downloader.py`, `utils.py`, `sources/**`, `mobile/**`),
> чтобы web/, tests/ и dist/ не попали в пакет.

---

## 📁 Структура папки Releases

```
Releases/
├── ShipTones-1.2.9-android-debug.apk    # Android APK v1.2.9 (диагностика + экспорт на каждый трек)
├── ShipTones.exe                        # ПК-версия v1.2.8 (Windows, автообновление из интерфейса)
└── ShipTones-1.2.8.exe                  # ПК-версия v1.2.8 (копия с номером)
```

---

## 🔧 Что включено в сборку

| Компонент | Версия | Описание |
|-----------|--------|----------|
| **Python** | 3.12 | Последняя стабильная версия |
| **yt-dlp** | latest | Загрузка с YouTube Music |
| **FFmpeg** | latest | Конвертация аудио (AAC/MP3/FLAC/Opus) |
| **mutagen** | latest | Редактирование метаданных |
| **Pillow** | latest | Обработка обложек альбомов |
| **pywebview** | latest | Веб-интерфейс приложения |

---

## 🎯 Поддерживаемые источники

| Источник | Статус | Форматы |
|----------|--------|---------|
| **YouTube Music** | ✅ Работает | AAC 256, MP3 320, FLAC, Opus |
| **VK Музыка** | ⏳ В разработке | Требуется API ключ |
| **Яндекс Музыка** | ⏳ В разработке | Требуется OAuth токен |

---

## 🎵 Настройки качества по умолчанию

```json
{
  "format": "aac",           // AAC как в YouTube Music
  "quality": "256",          // 256 кбит/с
  "normalize_volume": true,  // Нормализация -14 LUFS
  "source": "ytmusic"        // Источник по умолчанию
}
```

---

## 📝 Следующие шаги

1. **Проверьте код:** Убедитесь, что все изменения работают корректно
2. **Создайте тег:** `git tag v1.0.0 && git push origin v1.0.0`
3. **Дождитесь сборки:** GitHub Actions автоматически создаст релиз
4. **Протестируйте:** Скачайте ShipTones.exe из Releases и проверьте работу

---

## ⚠️ Важно для Linux/macOS

Сборка под Windows **невозможна** в Linux/macOS напрямую. Используйте:
- GitHub Actions (рекомендуется)
- Виртуальную машину Windows
- Wine (не рекомендуется, возможны ошибки)

---

## 🆘 Troubleshooting

**Ошибка: "ModuleNotFoundError: No module named 'js'"**
- Это нормально, urllib3 пытается импортировать модуль для Emscripten
- Игнорируйте предупреждение, сборка продолжится

**Ошибка: "tkinter installation is broken"**
- Приложение использует веб-интерфейс (pywebview), tkinter не нужен
- Игнорируйте предупреждение

**EXE не запускается:**
- Убедитесь, что у вас Windows 10/11 x64
- Проверьте антивирус (может блокировать)
- Запустите от имени администратора

---

## 📞 Контакты

Вопросы и предложения: [GitHub Issues](https://github.com/yourusername/shiptones/issues)
