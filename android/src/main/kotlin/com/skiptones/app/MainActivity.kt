package com.shiptones.app

import android.os.Bundle
import android.os.Environment
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.File
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.arthenica.mobileffmpeg.FFmpeg

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var pyBridge: PyBridge

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val python = Python.getInstance()

        // Папка загрузок — приватная директория приложения (MUSIC).
        // Не требует READ/WRITE внешнего хранилища (app-scoped директория).
        val musicFile = getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        val baseDir = if (musicFile != null) musicFile.parentFile else filesDir
        val musicDir = File(baseDir, Environment.DIRECTORY_MUSIC).absolutePath
        File(musicDir).mkdirs()

        // mobile-ffmpeg: бинарник для конвертации/нормализации аудио на устройстве.
        var ffmpegPath: String? = null
        try {
            FFmpeg.getInstance(this)
            ffmpegPath = resolveFfmpegBinaryPath()
        } catch (e: Exception) {
            // Без ffmpeg конвертация недоступна; загрузка исходного аудио остаётся возможной.
        }

        // Python Api: модуль mobile.backend, класс Api.
        val backend = python.getModule("mobile.backend")
        val apiClass = backend.getAttr("Api")
        val api = if (ffmpegPath != null) {
            apiClass.__call__(musicDir, ffmpegPath)
        } else {
            apiClass.__call__(musicDir)
        }

        // Python → JS: события загрузчика доставляются в WebView через evaluateJavascript.
        api.setAttr("event_callback",
            backend.getAttr("_make_js_callback").__call__(pyBridge))

        webView = WebView(this)
        val ws = webView.settings
        ws.javaScriptEnabled = true
        ws.domStorageEnabled = true
        ws.setSupportZoom(false)
        ws.builtInZoomControls = false
        ws.cacheMode = WebSettings.LOAD_DEFAULT
        ws.userAgentString = "Mozilla/5.0 (Linux; Android 11; ShipTones) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"

        pyBridge = PyBridge(this, webView, api, python)
        webView.addJavascriptInterface(pyBridge, "ShipTones")

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
            }
        }

        // Интерфейс загружается из assets/web/index.html.
        webView.loadUrl("file:///android_asset/web/index.html")

        setContentView(webView)
    }

    /** Путь к бинарнику ffmpeg, извлечённому mobile-ffmpeg в приватную директорию приложения. */
    private fun resolveFfmpegBinaryPath(): String? {
        val root = filesDir.absolutePath
        val candidates = listOf(
            "$root/ffmpeg",
            "$root/app_ffmpeg/ffmpeg",
        )
        return candidates.firstOrNull { File(it).exists() } ?: null
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
