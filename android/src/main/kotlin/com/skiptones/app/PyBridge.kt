package com.shiptones.app

import android.app.Activity
import android.webkit.WebView
import com.chaquo.python.PyObject
import com.chaquo.python.Python

/**
 * Мост JS → Python и Python → JS.
 *
 * JS вызывает методы с аннотацией @JavascriptInterface (старт/отмена загрузки,
 * история, плейлисты, настройки). Python через [emit] вызывает evaluateJavascript
 * и доставляет события в window.ShipTones.onEvent.
 */
class PyBridge(
    private val activity: Activity,
    private val webView: WebView,
    private val api: PyObject,
    private val python: Python
) {
    private val json = python.getModule("json")

    private fun toJson(obj: PyObject): String {
        return try {
            json.getAttr("dumps").__call__(obj).toString()
        } catch (e: Exception) {
            "{\"error\":\"serialization failed\"}"
        }
    }

    @JavascriptInterface
    fun startDownload(url: String, fmt: String): String {
        return try {
            toJson(api.callAttr("start_download", url, fmt))
        } catch (e: Exception) {
            "{\"error\":\"${e.message ?: "unknown"}\"}"
        }
    }

    @JavascriptInterface
    fun cancel(): String {
        return try {
            toJson(api.callAttr("cancel"))
        } catch (e: Exception) {
            "{\"error\":\"${e.message ?: "unknown"}\"}"
        }
    }

    @JavascriptInterface
    fun history(): String {
        return try {
            toJson(api.callAttr("history"))
        } catch (e: Exception) {
            "[]"
        }
    }

    @JavascriptInterface
    fun history(search: String): String {
        return try {
            toJson(api.callAttr("history", search))
        } catch (e: Exception) {
            "[]"
        }
    }

    @JavascriptInterface
    fun playlists(): String {
        return try {
            toJson(api.callAttr("playlists"))
        } catch (e: Exception) {
            "[]"
        }
    }

    @JavascriptInterface
    fun settingsGet(): String {
        return try {
            toJson(api.callAttr("settings"))
        } catch (e: Exception) {
            "{}"
        }
    }

    @JavascriptInterface
    fun settingsSet(patch: String): String {
        return try {
            toJson(api.callAttr("settings", patch))
        } catch (e: Exception) {
            "{\"error\":\"${e.message ?: "unknown"}\"}"
        }
    }

    /** Python → JS: доставить событие загрузчика в WebView (обязательно в UI-потоке). */
    fun emit(kind: String, payload: String) {
        activity.runOnUiThread {
            val k = kind.replace("\\", "\\\\").replace("\"", "\\\"")
            // payload — уже готовый JSON-объект (литерал), вставляем без кавычек.
            webView.evaluateJavascript(
                "if(window.ShipTones){window.ShipTones.onEvent(\"${k}\",${payload});}",
                null)
        }
    }
}
