# =====================================================================
# ShipTones: сборка Android APK (debug)
#
# Запуск из корня репозитория:
#   powershell -ExecutionPolicy Bypass -File .\build-android.ps1
#
# Скрипт сам проверяет/скачивает тулчейн (JDK 17, Gradle 8.7, Android SDK),
# подготавливает библиотеки и запускает gradle assembleDebug.
# Готовый APK копируется в Releases\ShipTones-<версия>-android-debug.apk
#
# Зависимости сети при первом запуске:
#   - JDK 17      (adoptium),   ~180 MB
#   - Gradle 8.7  (gradle.org), ~120 MB
#   - Android SDK cmdline-tools + platform-34 + build-tools 34.0.0, ~500 MB
#   - ffmpeg-kit full-gpl AAR (см. $FfmpegKitUrl), ~85 MB
#
# Повторные запуски работают офлайн и занимают секунды.
# =====================================================================

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AndroidDir = Join-Path $RepoRoot 'android'
$ToolsRoot = Join-Path $HOME 'tools'
$ReleasesDir = Join-Path $RepoRoot 'Releases'

# --- Параметры инструментов -------------------------------------------
$JdkDir = Join-Path $ToolsRoot 'jdk\jdk17'
$GradleDir = Join-Path $ToolsRoot 'gradle\gradle-8.7'
$SdkDir = Join-Path $ToolsRoot 'android-sdk'

$JdkUrl = 'https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse'
$JdkZip = Join-Path $ToolsRoot 'jdk\jdk17-download.zip'
$GradleUrl = 'https://services.gradle.org/distributions/gradle-8.7-bin.zip'
$GradleZip = Join-Path $ToolsRoot 'gradle\gradle-8.7-download.zip'
$CmdlineToolsUrl = 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip'

# ffmpeg-kit full-gpl (зеркало с 16 КБ страницами; официальный репозиторий удалён)
$FfmpegKitUrl = 'https://github.com/ClutchSports-Tech/ffmpeg-kit-16kb/releases/download/v1.0.0/ffmpeg-kit-full-gpl-v1.0.0.aar'
$FfmpegKitAar = Join-Path $AndroidDir 'libs\ffmpeg-kit-full-gpl.aar'

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Force -Path $path | Out-Null }
}

function Download($url, $dest) {
    Write-Host "Скачиваю: $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

# --- JDK 17 ------------------------------------------------------------
if (-not (Test-Path (Join-Path $JdkDir 'bin\java.exe'))) {
    Write-Host '>> Устанавливаю JDK 17...'
    Ensure-Dir (Join-Path $ToolsRoot 'jdk')
    Download $JdkUrl $JdkZip
    Expand-Archive -Path $JdkZip -DestinationPath (Join-Path $ToolsRoot 'jdk') -Force
    Remove-Item $JdkZip -Force
    Get-ChildItem (Join-Path $ToolsRoot 'jdk') -Directory |
        Where-Object { $_.Name -like 'jdk-17*' } |
        ForEach-Object { Rename-Item $_.FullName 'jdk17' -Force }
    if (-not (Test-Path (Join-Path $JdkDir 'bin\java.exe'))) {
        throw 'JDK 17 не установился: не найден bin\java.exe'
    }
} else { Write-Host 'JDK 17: уже установлен' }

# --- Gradle 8.7 --------------------------------------------------------
if (-not (Test-Path (Join-Path $GradleDir 'bin\gradle.bat'))) {
    Write-Host '>> Устанавливаю Gradle 8.7...'
    Ensure-Dir (Join-Path $ToolsRoot 'gradle')
    Download $GradleUrl $GradleZip
    Expand-Archive -Path $GradleZip -DestinationPath (Join-Path $ToolsRoot 'gradle') -Force
    Remove-Item $GradleZip -Force
    if (-not (Test-Path (Join-Path $GradleDir 'bin\gradle.bat'))) {
        throw 'Gradle не установился: не найден bin\gradle.bat'
    }
} else { Write-Host 'Gradle 8.7: уже установлен' }

# --- Android SDK -------------------------------------------------------
if (-not (Test-Path (Join-Path $SdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'))) {
    Write-Host '>> Устанавливаю Android SDK cmdline-tools...'
    Ensure-Dir $SdkDir
    $ctZip = Join-Path $SdkDir 'cmdline-tools.zip'
    Download $CmdlineToolsUrl $ctZip
    Expand-Archive -Path $ctZip -DestinationPath $SdkDir -Force
    Remove-Item $ctZip -Force
    Move-Item (Join-Path $SdkDir 'cmdline-tools') (Join-Path $SdkDir 'cmdline-tools-raw') -Force
    Ensure-Dir (Join-Path $SdkDir 'cmdline-tools')
    Move-Item (Join-Path $SdkDir 'cmdline-tools-raw') (Join-Path $SdkDir 'cmdline-tools\latest') -Force
}

$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $SdkDir
$SdkManager = Join-Path $SdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'

# Компоненты: platform-tools, android-34, build-tools 34.0.0
$NeedSdkComponents = $false
foreach ($c in @('platform-tools', 'platforms;android-34', 'build-tools;34.0.0')) {
    $probe = switch -wildcard ($c) {
        'platform-tools' { Join-Path $SdkDir 'platform-tools\adb.exe' }
        'build-tools;34.0.0' { Join-Path $SdkDir 'build-tools\34.0.0\aapt2.exe' }
        default { Join-Path $SdkDir 'platforms\android-34\android.jar' }
    }
    if (-not (Test-Path $probe)) { $NeedSdkComponents = $true }
}
if ($NeedSdkComponents) {
    Write-Host '>> Устанавливаю компоненты Android SDK (принимаю лицензии)...'
    $licensePkg = "$env:TEMP\sdk-licenses.yml"
    @"
y
y
y
y
y
y
y
y
y
"@ | Set-Content -Path $licensePkg -Encoding Ascii
    # PowerShell не поддерживает перенаправление stdin через '<' — используем пайп
    Get-Content $licensePkg | & $SdkManager --licenses | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "sdkmanager --licenses завершился с кодом $LASTEXITCODE" }
    Get-Content $licensePkg | & $SdkManager 'platform-tools' 'platforms;android-34' 'build-tools;34.0.0'
    if ($LASTEXITCODE -ne 0) { throw "sdkmanager завершился с кодом $LASTEXITCODE" }
} else { Write-Host 'Android SDK: компоненты уже установлены' }

# Лицензии предзадаются, чтобы gradle не спрашивал подтверждение
$licensesDir = Join-Path $SdkDir 'licenses'
Ensure-Dir $licensesDir
@'
24333f8a63b6825ea9c5514f83c2829b004d1fee
8933bad161af4178b1185d1a37fbf41ea5269c55
d56f5187479451eabf01fb78af6dfcb131a6481e
'@ -split '\s+' | Where-Object { $_ } | ForEach-Object {
    Set-Content -Path (Join-Path $licensesDir $_) -Value 'Prepopulated License' -Encoding Ascii
}

# --- ffmpeg-kit AAR ----------------------------------------------------
if (-not (Test-Path $FfmpegKitAar)) {
    Write-Host '>> Скачиваю ffmpeg-kit full-gpl AAR (~85 MB)...'
    Ensure-Dir (Join-Path $AndroidDir 'libs')
    Download $FfmpegKitUrl $FfmpegKitAar
} else { Write-Host 'ffmpeg-kit AAR: уже на месте' }

# --- Сборка ------------------------------------------------------------
$env:PATH = "$JdkDir\bin;$env:PATH"
Write-Host "`n>> Запускаю gradle assembleDebug..."
& (Join-Path $GradleDir 'bin\gradle.bat') -p $AndroidDir assembleDebug --console=plain
if ($LASTEXITCODE -ne 0) { throw "Сборка завершилась с кодом $LASTEXITCODE" }

# --- Копирование APK в Releases -----------------------------------------
$ApkPath = Get-ChildItem (Join-Path $AndroidDir 'build\outputs\apk\debug') -Filter '*.apk' |
    Select-Object -First 1
if (-not $ApkPath) { throw 'APK не найден в outputs' }

# версия из build.gradle (versionName '...'), фолбэк: из манифеста APK
$buildGradle = Get-Content (Join-Path $AndroidDir 'build.gradle') -Raw
$version = if ($buildGradle -match "versionName\s+'([\d.]+)'") { $Matches[1] } else { 'unknown' }

Ensure-Dir $ReleasesDir
$dest = Join-Path $ReleasesDir "ShipTones-$version-android-debug.apk"
Copy-Item $ApkPath.FullName $dest -Force

Write-Host "`n✅ Готово. APK: $dest"
