package com.shiptones.app

import android.app.Activity
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.chaquo.python.PyObject
import com.chaquo.python.Python

/**
 * Мост JS → Python и Python → JS.
 *
 * JS вызывает методы с аннотацией @JavascriptInterface (старт/отмена загрузки,
 * история, плейлисты, настройки, версия, источник, интернет). Python через [emit]
 * вызывает evaluateJavascript и доставляет события в window.ShipTones.onEvent.
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
            json.get("dumps")!!.call(obj).toString()
        } catch (e: Exception) {
            "{\"error\":\"serialization failed\"}"
        }
    }

    @JavascriptInterface
    fun startDownload(url: String, fmt: String, mode: String): String {
        return try {
            toJson(api.callAttr("start_download", url, fmt, mode))
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
    fun deletePlaylist(playlistId: Int): String {
        return try {
            toJson(api.callAttr("delete_playlist", playlistId))
        } catch (e: Exception) {
            "{\"error\":\"${e.message ?: "unknown"}\"}"
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

    @JavascriptInterface
    fun getSources(): String {
        return try {
            toJson(api.callAttr("get_sources"))
        } catch (e: Exception) {
            "{}"
        }
    }

    @JavascriptInterface
    fun detectSource(url: String): String {
        return try {
            toJson(api.callAttr("detect_source", url))
        } catch (e: Exception) {
            "null"
        }
    }

    @JavascriptInterface
    fun getAppVersion(): String {
        return try {
            api.callAttr("get_app_version").toString()
        } catch (e: Exception) {
            ""
        }
    }

    @JavascriptInterface
    fun downloadDir(): String {
        return try {
            toJson(api.callAttr("download_dir"))
        } catch (e: Exception) {
            ""
        }
    }

    @JavascriptInterface
    fun checkInternet(): String {
        return try {
            toJson(api.callAttr("check_internet"))
        } catch (e: Exception) {
            "false"
        }
    }

    /** JS → Kotlin: системное «Поделиться» для скачанного трека.
     *  Python возвращает путь файла, Kotlin отправляет его через FileProvider
     *  (папка загрузок Android приватна — обычные файловые менеджеры её не видят). */
    @JavascriptInterface
    fun shareTrack(trackId: Int): String {
        val pathJson = try {
            toJson(api.callAttr("share_file", trackId))
        } catch (e: Exception) {
            "{\"error\":\"share failed: ${e.message ?: "unknown"}\"}"
        }
        val path = try {
            org.json.JSONObject(pathJson).optString("path")
        } catch (e: Exception) {
            ""
        }
        if (path.isBlank()) return pathJson
        return try {
            val file = java.io.File(path)
            val uri = androidx.core.content.FileProvider.getUriForFile(
                activity, "${activity.packageName}.fileprovider", file)
            val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                type = "audio/*"
                putExtra(android.content.Intent.EXTRA_STREAM, uri)
                putExtra(android.content.Intent.EXTRA_TITLE, "ShipTones — трек")
                addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            activity.runOnUiThread {
                activity.startActivity(
                    android.content.Intent.createChooser(intent, "Отправить трек"))
            }
            "{\"ok\":true}"
        } catch (e: Exception) {
            "{\"error\":\"share failed: ${e.message ?: "unknown"}\"}"
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
