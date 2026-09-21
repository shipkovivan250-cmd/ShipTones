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

## 📁 Структура папки Releases

```
Releases/
└── ShipTones.exe    # Готовый к использованию загрузчик
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
