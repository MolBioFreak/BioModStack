package org.biomodstack.mobile.apkupdate

import android.net.Uri
import android.webkit.WebView
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.apache.cordova.CordovaPlugin
import org.json.JSONObject
import java.net.URI

/** Native-only origin-bound WebMessage bridge with no Cordova action service. */
class BmsApkUpdatePlugin : CordovaPlugin() {
    private lateinit var nativeWebView: WebView
    private lateinit var updater: BmsApkUpdater
    private var listenerRegistered = false

    override fun pluginInitialize() {
        nativeWebView = webView.view as WebView
        val configuredApiOrigin = requireConfiguredApiOrigin(
            preferences.getString("BMS_API_ORIGIN", "") ?: "",
        )
        updater = BmsApkUpdater(cordova.activity, nativeWebView) { configuredApiOrigin }
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) return
        WebViewCompat.addWebMessageListener(
            nativeWebView,
            "BmsAndroidUpdater",
            setOf("https://localhost"),
        ) { _, message, sourceOrigin, isMainFrame, _ ->
            if (!isMainFrame || sourceOrigin.toString() != "https://localhost") return@addWebMessageListener
            val rawMessage = message.data ?: return@addWebMessageListener
            handleMessage(rawMessage)
        }
        listenerRegistered = true
    }

    private fun handleMessage(rawMessage: String) {
        if (rawMessage.length !in 1..256) return
        val message = runCatching { JSONObject(rawMessage) }.getOrNull() ?: return
        val keys = message.keys().asSequence().toList()
        if (keys != listOf("action")) return
        when (message.optString("action")) {
            "getShellInfo" -> emitShellInfo()
            "checkForApkUpdate" -> updater.checkForUpdate()
            "installApkUpdate" -> updater.installUpdate()
        }
    }

    private fun emitShellInfo() {
        val detail = JSONObject()
            .put("available", true)
            .put("shellVersion", "0.2.0")
            .put("shellVersionCode", 200)
            .put("nativeApkUpdateSupported", true)
            .put("nativeApkUpdateChannel", "stable")
            .put("nativeApkUpdateStrategy", "same-origin-verified-user-approved")
        val script = "window.dispatchEvent(new CustomEvent('biomodstack-android-shell-info', { detail: $detail }));"
        nativeWebView.post { nativeWebView.evaluateJavascript(script, null) }
    }

    override fun onResume(multitasking: Boolean) {
        super.onResume(multitasking)
        if (::updater.isInitialized) {
            updater.reconcileInstallerReturn()
            updater.resumePendingInstallerIfApproved()
        }
    }

    override fun onDestroy() {
        if (listenerRegistered) {
            WebViewCompat.removeWebMessageListener(nativeWebView, "BmsAndroidUpdater")
            listenerRegistered = false
        }
        if (::updater.isInitialized) updater.close()
        super.onDestroy()
    }

    private fun requireConfiguredApiOrigin(value: String): String {
        val uri = try { URI(value) } catch (error: Exception) {
            throw IllegalStateException("BMS_API_ORIGIN must be an exact HTTPS origin", error)
        }
        if (!uri.scheme.equals("https", true) || uri.host.isNullOrBlank() ||
            uri.userInfo != null || uri.query != null || uri.fragment != null ||
            (uri.path.isNotEmpty() && uri.path != "/")
        ) throw IllegalStateException("BMS_API_ORIGIN must be an exact HTTPS origin")
        return URI("https", null, uri.host, if (uri.port == -1) 443 else uri.port, "", null, null).toString()
    }
}
