package com.shiptones.app

import android.app.AlertDialog
import android.os.Bundle
import android.os.Environment
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.File
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var pyBridge: PyBridge

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Chaquopy: запуск интерпретатора Python — обязателен до Python.getInstance().
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        val python = Python.getInstance()

        // Папка загрузок — приватная директория приложения (MUSIC).
        // Не требует READ/WRITE внешнего хранилища (app-scoped директория).
        val musicFile = getExternalFilesDir(Environment.DIRECTORY_MUSIC)
        val baseDir = if (musicFile != null) musicFile.parentFile else filesDir
        val musicDir = File(baseDir, Environment.DIRECTORY_MUSIC).absolutePath
        File(musicDir).mkdirs()

        // Python Api: модуль mobile.backend, класс Api.
        // ffmpeg_path не передаётся: конвертация на Android идёт через mobile-ffmpeg
        // внутри Python (см. mobile/backend.py).
        val backend = python.getModule("mobile.backend")
        val apiClass = backend.get("Api")!!
        val api = apiClass.call(musicDir)

        // Сначала создаём WebView и JS-мост, и только потом подключаем колбэк
        // Python → JS (pyBridge должен быть инициализирован к моменту вызова).
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

        // Python → JS: события загрузчика доставляются в WebView через evaluateJavascript.
        api.put("event_callback",
            backend.get("_make_js_callback")!!.call(pyBridge))

        // confirm()/alert() из JS работают только с WebChromeClient.
        webView.webChromeClient = object : WebChromeClient() {
            override fun onJsAlert(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton("OK") { _, _ -> result?.confirm() }
                    .setOnCancelListener { result?.cancel() }
                    .show()
                return true
            }
            override fun onJsConfirm(view: WebView?, url: String?, message: String?, result: JsResult?): Boolean {
                AlertDialog.Builder(this@MainActivity)
                    .setMessage(message)
                    .setPositiveButton("OK") { _, _ -> result?.confirm() }
                    .setNegativeButton("Отмена") { _, _ -> result?.cancel() }
                    .setOnCancelListener { result?.cancel() }
                    .show()
                return true
            }
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
            }
        }

        setContentView(webView)

        // Интерфейс загружается из assets/web/index.html.
        webView.loadUrl("file:///android_asset/web/index.html")
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }
}
