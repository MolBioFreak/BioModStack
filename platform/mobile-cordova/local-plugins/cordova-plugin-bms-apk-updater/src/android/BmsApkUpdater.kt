package org.biomodstack.mobile.apkupdate

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.webkit.CookieManager
import android.webkit.WebView
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI

import java.security.MessageDigest
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.HttpsURLConnection


class BmsApkUpdater(
    private val activity: Activity,
    private val webView: WebView,
    private val configuredServerUrl: () -> String,
) {
    private val executor = Executors.newSingleThreadExecutor()
    private val busy = AtomicBoolean(false)
    private val eventSequence = AtomicLong(0L)
    private val installerPreferences = activity.getSharedPreferences("biomodstack-apk-installer", Activity.MODE_PRIVATE)
    @Volatile private var availableManifest: ApkUpdateManifest? = null
    @Volatile private var pendingInstallerFile: File? = null
    @Volatile private var pendingInstallerManifest: ApkUpdateManifest? = null
    @Volatile private var installerLaunchVersionCode: Long =
        installerPreferences.getLong(KEY_INSTALLER_VERSION_CODE, 0L)

    init {
        restorePendingInstaller()
    }

    fun checkForUpdate() {
        if (!busy.compareAndSet(false, true)) return
        emit("checking", "Checking the authenticated BioModStack server for an APK update…")
        executor.execute {
            try {
                val manifest = fetchAndValidateManifest()
                if (ApkUpdatePolicy.isUpdateAvailable(manifest, installedVersionCode())) {
                    availableManifest = manifest
                    emit(
                        "available",
                        "BioModStack APK ${manifest.versionName} is available.",
                        manifest,
                    )
                } else {
                    availableManifest = null
                    emit("up_to_date", "This Android shell is already up to date.", manifest)
                }
            } catch (error: Exception) {
                availableManifest = null
                emit("error", userFacingError(error))
            } finally {
                busy.set(false)
            }
        }
    }

    fun installUpdate() {
        if (resumePendingInstallerIfApproved()) return
        if (!busy.compareAndSet(false, true)) return
        executor.execute {
            try {
                val manifest = availableManifest ?: fetchAndValidateManifest()
                if (!ApkUpdatePolicy.isUpdateAvailable(manifest, installedVersionCode())) {
                    availableManifest = null
                    emit("up_to_date", "This Android shell is already up to date.", manifest)
                    return@execute
                }
                val apkFile = downloadAndVerify(manifest)
                persistPendingInstaller(apkFile, manifest)
                requestInstaller(apkFile, manifest)
            } catch (error: Exception) {
                emit("error", userFacingError(error), availableManifest)
            } finally {
                busy.set(false)
            }
        }
    }

    fun resumePendingInstallerIfApproved(): Boolean {
        val apkFile = pendingInstallerFile ?: return false
        val manifest = pendingInstallerManifest ?: return false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !activity.packageManager.canRequestPackageInstalls()) {
            emit("install_permission_denied", "Android install permission is still disabled. Enable it and retry the verified update.", manifest)
            return true
        }
        if (!busy.compareAndSet(false, true)) return true
        executor.execute {
            try {
                verifyPendingInstaller(apkFile, manifest)
                activity.runOnUiThread { launchPendingInstaller(apkFile, manifest) }
            } catch (error: Exception) {
                emit("error", "Android could not open the package installer: ${error.message ?: "unknown error"}", manifest)
            } finally {
                busy.set(false)
            }
        }
        return true
    }

    fun reconcileInstallerReturn() {
        if (webView.url.isNullOrBlank()) return
        installerLaunchVersionCode = installerPreferences.getLong(KEY_INSTALLER_VERSION_CODE, 0L)
        val launchedVersionCode = installerLaunchVersionCode
        if (launchedVersionCode <= 0L) return
        val manifest = installerPreferences.getString(KEY_INSTALLER_MANIFEST, null)
            ?.let { runCatching { parseStoredManifest(JSONObject(it)) }.getOrNull() }
        val status = InstallerReturnPolicy.status(installedVersionCode(), launchedVersionCode)
        clearInstallerLaunch()
        if (status == "up_to_date") {
            availableManifest = null
            emit("up_to_date", "BioModStack APK $launchedVersionCode was installed successfully.")
        } else if (manifest != null) {
            availableManifest = manifest
            emit("available", "Android installation was not completed. You can retry the verified APK update.", manifest)
        } else {
            availableManifest = null
            emit("error", "Android installation was not completed. Check for the APK update again.")
        }
    }

    fun close() {
        executor.shutdownNow()
    }

    private fun fetchAndValidateManifest(): ApkUpdateManifest {
        val server = URI(configuredServerUrl())
        if (!server.scheme.equals("https", ignoreCase = true) || server.host.isNullOrBlank()) {
            throw IOException("The configured BioModStack server is not a valid HTTPS origin.")
        }
        val manifestUri = URI(
            "https",
            null,
            server.host,
            if (server.port == -1) 443 else server.port,
            "/api/mobile-apk/channels/stable/manifest",
            null,
            null,
        )
        val payload = readAuthenticatedResponse(manifestUri, 128 * 1024)
        val manifest = parseManifest(JSONObject(payload))
        val validationError = ApkUpdatePolicy.validationError(
            manifest,
            configuredServerUrl(),
            installedVersionCode(),
            Build.VERSION.SDK_INT,
        )
        if (validationError != null) throw IOException(validationError)

        val installedSigners = installedSignerDigests()
        val expectedSigner = ApkUpdatePolicy.normalizeFingerprint(manifest.signingCertificateSha256)
        if (installedSigners != setOf(expectedSigner)) {
            throw IOException("Published APK signing certificate does not match this installed BioModStack app.")
        }
        return manifest
    }

    private fun downloadAndVerify(manifest: ApkUpdateManifest, restartDownloadFromZero: Boolean = false): File {
        val downloadUrl = ApkUpdatePolicy.resolveDownloadUrl(configuredServerUrl(), manifest.downloadUrl)
            ?: throw IOException("APK download URL failed same-origin validation.")
        val downloadUri = URI(downloadUrl)
        emit("downloading", "Downloading BioModStack APK ${manifest.versionName}…", manifest)

        val updatesDirectory = File(activity.cacheDir, "apk-updates").apply { mkdirs() }
        val temporaryFile = File(updatesDirectory, ".${manifest.filename}.download")
        val identityFile = File(updatesDirectory, ".${manifest.filename}.identity")
        val finalFile = File(updatesDirectory, manifest.filename)
        val partialIdentity = ApkDownloadPolicy.partialIdentity(manifest)
        if (restartDownloadFromZero ||
            temporaryFile.length() > manifest.sizeBytes ||
            (temporaryFile.exists() && identityFile.readTextOrNull() != partialIdentity)
        ) {
            temporaryFile.delete()
            identityFile.delete()
        }
        if (!temporaryFile.exists()) writeIdentityAtomically(identityFile, partialIdentity)

        var digest = MessageDigest.getInstance("SHA-256")
        var bytesWritten = temporaryFile.length()
        if (bytesWritten > 0L) {
            FileInputStream(temporaryFile).use { input ->
                val buffer = ByteArray(1024 * 1024)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    digest.update(buffer, 0, read)
                }
            }
        }

        if (bytesWritten < manifest.sizeBytes) {
            val requestedOffset = bytesWritten
            val connection = openAuthenticatedConnection(
                downloadUri,
                rangeStart = requestedOffset.takeIf { it > 0L },
            )
            try {
                val responseCode = connection.responseCode
                val advertisedLength = connection.contentLengthLong
                val contentRange = connection.getHeaderField("Content-Range").orEmpty()
                if (requestedOffset > 0L && ApkDownloadPolicy.shouldRestartResume(
                        responseCode,
                        contentRange,
                        requestedOffset,
                        manifest.sizeBytes,
                        advertisedLength,
                    )
                ) {
                    if (restartDownloadFromZero) throw IOException("APK resume could not be restarted safely.")
                    temporaryFile.delete()
                    identityFile.delete()
                    return downloadAndVerify(manifest, restartDownloadFromZero = true)
                }
                val append = requestedOffset > 0L && responseCode == HttpURLConnection.HTTP_PARTIAL
                if (requestedOffset > 0L && responseCode == HttpURLConnection.HTTP_OK) {
                    bytesWritten = 0L
                    digest = MessageDigest.getInstance("SHA-256")
                }
                if (responseCode != HttpURLConnection.HTTP_OK && responseCode != HttpURLConnection.HTTP_PARTIAL) {
                    throw responseException(responseCode)
                }
                if (requestedOffset == 0L && responseCode == HttpURLConnection.HTTP_PARTIAL) {
                    throw IOException("BioModStack returned a partial APK without a resume request.")
                }

                val expectedResponseBytes = manifest.sizeBytes - bytesWritten
                if (advertisedLength < 0L || advertisedLength != expectedResponseBytes) {
                    throw IOException("APK server content length does not match the manifest metadata.")
                }
                connection.inputStream.use { input ->
                    FileOutputStream(temporaryFile, append).use { output ->
                        val buffer = ByteArray(1024 * 1024)
                        while (true) {
                            val read = input.read(buffer)
                            if (read < 0) break
                            bytesWritten += read
                            if (bytesWritten > manifest.sizeBytes) {
                                throw IOException("APK exceeded its declared size.")
                            }
                            digest.update(buffer, 0, read)
                            output.write(buffer, 0, read)
                        }
                        output.fd.sync()
                    }
                }
            } finally {
                connection.disconnect()
            }
        }

        emit("verifying", "Verifying BioModStack APK ${manifest.versionName}…", manifest)
        val actualDigest = digest.digest().joinToString("") { "%02x".format(it) }
        if (bytesWritten != manifest.sizeBytes) throw IOException("APK size verification failed.")
        if (!actualDigest.equals(manifest.sha256, ignoreCase = true)) {
            temporaryFile.delete()
            identityFile.delete()
            throw IOException("APK SHA-256 verification failed.")
        }
        try {
            verifyArchiveIdentity(temporaryFile, manifest)
        } catch (error: Exception) {
            temporaryFile.delete()
            identityFile.delete()
            throw error
        }

        replaceBySameDirectoryRename(temporaryFile, finalFile, "verified APK download")
        identityFile.delete()
        return finalFile
    }

    private fun File.readTextOrNull(): String? =
        try {
            if (isFile) readText(Charsets.UTF_8) else null
        } catch (_: IOException) {
            null
        }

    private fun writeIdentityAtomically(identityFile: File, identity: String) {
        val temporary = File(identityFile.parentFile, "${identityFile.name}.tmp")
        FileOutputStream(temporary, false).use { output ->
            output.write(identity.toByteArray(Charsets.UTF_8))
            output.fd.sync()
        }
        try {
            replaceBySameDirectoryRename(temporary, identityFile, "download identity")
        } finally {
            temporary.delete()
        }
    }

    private fun replaceBySameDirectoryRename(source: File, destination: File, label: String) {
        if (source.parentFile?.canonicalFile != destination.parentFile?.canonicalFile) {
            throw IOException("Could not finalize $label outside its private directory.")
        }
        if (destination.exists() && !destination.delete()) {
            throw IOException("Could not replace the existing $label.")
        }
        if (!source.renameTo(destination)) {
            throw IOException("Could not atomically finalize the $label.")
        }
    }

    private fun verifyArchiveIdentity(apkFile: File, manifest: ApkUpdateManifest) {
        val archiveInfo = packageArchiveInfo(apkFile)
            ?: throw IOException("Downloaded file is not a readable Android package.")
        val archiveSigners = signerDigests(archiveInfo)
        val archive = ApkArchiveIdentity(
            packageId = archiveInfo.packageName,
            versionCode = packageVersionCode(archiveInfo),
            versionName = archiveInfo.versionName.orEmpty(),
            minSdk = archiveInfo.applicationInfo?.minSdkVersion ?: 0,
            signingCertificateSha256 = archiveSigners,
        )
        ApkUpdatePolicy.archiveValidationError(manifest, archive)?.let { throw IOException(it) }
        val installedSigners = installedSignerDigests()
        if (archiveSigners.isEmpty() || archiveSigners != installedSigners) {
            throw IOException("Downloaded APK signing certificate does not match the installed app.")
        }
    }

    private fun requestInstaller(apkFile: File, manifest: ApkUpdateManifest) {
        activity.runOnUiThread {
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && !activity.packageManager.canRequestPackageInstalls()) {
                    emit(
                        "awaiting_install_permission",
                        "Allow BioModStack to install app updates, then return to continue.",
                        manifest,
                    )
                    val approvalIntent = Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:${activity.packageName}"),
                    )
                    activity.startActivity(approvalIntent)
                } else {
                    launchPendingInstaller(apkFile, manifest)
                }
            } catch (error: Exception) {
                pendingInstallerFile = apkFile
                pendingInstallerManifest = manifest
                clearInstallerLaunch()
                emit("error", "Android could not open the update approval flow: ${error.message ?: "unknown error"}", manifest)
            }
        }
    }

    private fun launchPendingInstaller(apkFile: File, manifest: ApkUpdateManifest) {
        try {
            openPackageInstaller(apkFile, manifest)
            clearPendingInstaller()
        } catch (error: Exception) {
            emit("error", "Android could not open the package installer: ${error.message ?: "unknown error"}", manifest)
        }
    }

    private fun openPackageInstaller(apkFile: File, manifest: ApkUpdateManifest) {
        val uri = FileProvider.getUriForFile(
            activity,
            "${activity.packageName}.fileprovider",
            apkFile,
        )
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        persistInstallerLaunch(manifest)
        try {
            activity.startActivity(intent)
        } catch (error: Exception) {
            clearInstallerLaunch()
            throw error
        }
        emit("installer_opened", "Android's verified package installer is open. Complete or cancel it in Android.", manifest)
    }

    private fun readAuthenticatedResponse(uri: URI, maximumBytes: Int): String {
        val connection = openAuthenticatedConnection(uri)
        try {
            val responseCode = connection.responseCode
            if (responseCode != HttpURLConnection.HTTP_OK) throw responseException(responseCode)
            val bytes = connection.inputStream.use { input ->
                val output = ByteArrayOutputStream()
                val buffer = ByteArray(8192)
                var total = 0
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    total += read
                    if (total > maximumBytes) throw IOException("APK update manifest is too large.")
                    output.write(buffer, 0, read)
                }
                output.toByteArray()
            }
            return bytes.toString(Charsets.UTF_8)
        } finally {
            connection.disconnect()
        }
    }

    private fun openAuthenticatedConnection(uri: URI, rangeStart: Long? = null): HttpsURLConnection {
        val connection = uri.toURL().openConnection() as? HttpsURLConnection
            ?: throw IOException("APK updates require HTTPS.")
        connection.instanceFollowRedirects = false
        connection.connectTimeout = 15_000
        connection.readTimeout = 60_000
        connection.setRequestProperty("Accept", "application/json, application/vnd.android.package-archive")
        rangeStart?.let { connection.setRequestProperty("Range", "bytes=$it-") }
        CookieManager.getInstance().getCookie(uri.toString())?.takeIf { it.isNotBlank() }?.let {
            connection.setRequestProperty("Cookie", it)
        }
        return connection
    }

    private fun responseException(responseCode: Int): IOException = when (responseCode) {
        HttpURLConnection.HTTP_UNAUTHORIZED -> IOException("Sign in to BioModStack before checking for APK updates.")
        HttpURLConnection.HTTP_NOT_FOUND -> IOException("No APK update is published on this BioModStack server.")
        else -> IOException("BioModStack APK update request failed with HTTP $responseCode.")
    }

    private fun parseManifest(payload: JSONObject): ApkUpdateManifest {
        val changelogJson = payload.optJSONArray("changelog") ?: JSONArray()
        if (changelogJson.length() > 50) throw JSONException("Too many changelog items")
        val changelog = buildList {
            for (index in 0 until changelogJson.length()) {
                val rawItem = changelogJson.get(index)
                if (rawItem !is String || rawItem.length > 1_000) {
                    throw JSONException("Invalid changelog item")
                }
                val item = rawItem.trim()
                if (item.isNotEmpty()) add(item)
            }
        }
        return ApkUpdateManifest(
            channel = payload.getString("channel"),
            versionCode = payload.requireLong("version_code"),
            versionName = payload.getString("version_name"),
            minSdk = payload.requireInt("min_sdk"),
            sha256 = payload.getString("sha256"),
            sizeBytes = payload.requireLong("size_bytes"),
            filename = payload.getString("filename"),
            packageId = payload.getString("package_id"),
            signingCertificateSha256 = payload.getString("signing_certificate_sha256"),
            publishedAt = payload.getString("published_at"),
            changelog = changelog,
            downloadUrl = payload.getString("download_url"),
        )
    }

    private fun JSONObject.requireLong(key: String): Long {
        val value = get(key)
        if (value !is Int && value !is Long) throw JSONException("$key must be an integer")
        return (value as Number).toLong()
    }

    private fun JSONObject.requireInt(key: String): Int {
        val value = requireLong(key)
        if (value !in Int.MIN_VALUE..Int.MAX_VALUE) throw JSONException("$key is outside integer range")
        return value.toInt()
    }

    private fun persistPendingInstaller(apkFile: File, manifest: ApkUpdateManifest) {
        if (!installerPreferences.edit()
                .putString(KEY_PENDING_APK_PATH, apkFile.absolutePath)
                .putString(KEY_PENDING_MANIFEST, manifest.toStoredJson().toString())
                .commit()
        ) throw IOException("Could not persist pending installer state.")
        pendingInstallerFile = apkFile
        pendingInstallerManifest = manifest
    }

    private fun restorePendingInstaller() {
        val rawPath = installerPreferences.getString(KEY_PENDING_APK_PATH, null) ?: return
        val rawManifest = installerPreferences.getString(KEY_PENDING_MANIFEST, null) ?: return
        val restored = runCatching { File(rawPath).canonicalFile }.getOrNull()
        val allowedRoot = File(activity.cacheDir, "apk-updates").canonicalFile
        val manifest = runCatching { parseStoredManifest(JSONObject(rawManifest)) }.getOrNull()
        if (restored == null || restored.parentFile != allowedRoot || !restored.isFile || manifest == null) {
            clearPendingInstaller()
            return
        }
        pendingInstallerFile = restored
        pendingInstallerManifest = manifest
    }

    private fun clearPendingInstaller() {
        pendingInstallerFile = null
        pendingInstallerManifest = null
        installerPreferences.edit().remove(KEY_PENDING_APK_PATH).remove(KEY_PENDING_MANIFEST).apply()
    }

    private fun verifyPendingInstaller(apkFile: File, manifest: ApkUpdateManifest) {
        if (!apkFile.isFile || apkFile.length() != manifest.sizeBytes) throw IOException("Pending APK size verification failed.")
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(apkFile).use { input ->
            val buffer = ByteArray(1024 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (!actual.equals(manifest.sha256, ignoreCase = true)) throw IOException("Pending APK SHA-256 verification failed.")
        ApkUpdatePolicy.validationError(manifest, configuredServerUrl(), installedVersionCode(), Build.VERSION.SDK_INT)
            ?.let { throw IOException(it) }
        verifyArchiveIdentity(apkFile, manifest)
    }

    private fun persistInstallerLaunch(manifest: ApkUpdateManifest) {
        val committed = installerPreferences.edit()
            .putLong(KEY_INSTALLER_VERSION_CODE, manifest.versionCode)
            .putString(KEY_INSTALLER_MANIFEST, manifest.toStoredJson().toString())
            .commit()
        if (!committed) throw IOException("Could not persist installer handoff state.")
        installerLaunchVersionCode = manifest.versionCode
    }

    private fun clearInstallerLaunch() {
        installerLaunchVersionCode = 0L
        installerPreferences.edit()
            .remove(KEY_INSTALLER_VERSION_CODE)
            .remove(KEY_INSTALLER_MANIFEST)
            .apply()
    }

    private fun parseStoredManifest(payload: JSONObject): ApkUpdateManifest = parseManifest(payload)

    private fun ApkUpdateManifest.toStoredJson(): JSONObject = JSONObject()
        .put("channel", channel)
        .put("version_code", versionCode)
        .put("version_name", versionName)
        .put("min_sdk", minSdk)
        .put("sha256", sha256)
        .put("size_bytes", sizeBytes)
        .put("filename", filename)
        .put("package_id", packageId)
        .put("signing_certificate_sha256", signingCertificateSha256)
        .put("published_at", publishedAt)
        .put("changelog", JSONArray(changelog))
        .put("download_url", downloadUrl)

    private fun emit(status: String, message: String, manifest: ApkUpdateManifest? = null) {
        val detail = JSONObject()
            .put("sequence", eventSequence.incrementAndGet())
            .put("status", status)
            .put("message", message)
        manifest?.let { detail.put("manifest", it.toEventJson()) }
        val script = "window.dispatchEvent(new CustomEvent('biomodstack-apk-update-state', { detail: ${detail} }));"
        webView.post { webView.evaluateJavascript(script, null) }
    }

    private fun ApkUpdateManifest.toEventJson(): JSONObject = JSONObject()
        .put("channel", channel)
        .put("versionCode", versionCode)
        .put("versionName", versionName)
        .put("minSdk", minSdk)
        .put("sizeBytes", sizeBytes)
        .put("publishedAt", publishedAt)
        .put("changelog", JSONArray(changelog))

    private fun installedVersionCode(): Long {
        val packageInfo = installedPackageInfo()
        return packageVersionCode(packageInfo)
    }

    private fun installedSignerDigests(): Set<String> = signerDigests(installedPackageInfo())

    @Suppress("DEPRECATION")
    private fun installedPackageInfo(): PackageInfo {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            PackageManager.GET_SIGNATURES
        }
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            activity.packageManager.getPackageInfo(
                activity.packageName,
                PackageManager.PackageInfoFlags.of(flags.toLong()),
            )
        } else {
            activity.packageManager.getPackageInfo(activity.packageName, flags)
        }
    }

    @Suppress("DEPRECATION")
    private fun packageArchiveInfo(apkFile: File): PackageInfo? {
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            PackageManager.GET_SIGNATURES
        }
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            activity.packageManager.getPackageArchiveInfo(
                apkFile.absolutePath,
                PackageManager.PackageInfoFlags.of(flags.toLong()),
            )
        } else {
            activity.packageManager.getPackageArchiveInfo(apkFile.absolutePath, flags)
        }
    }

    @Suppress("DEPRECATION")
    private fun signerDigests(packageInfo: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            packageInfo.signingInfo?.apkContentsSigners.orEmpty()
        } else {
            packageInfo.signatures.orEmpty()
        }
        return signatures.map { signature ->
            MessageDigest.getInstance("SHA-256")
                .digest(signature.toByteArray())
                .joinToString("") { "%02X".format(it) }
        }.toSet()
    }

    @Suppress("DEPRECATION")
    private fun packageVersionCode(packageInfo: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) packageInfo.longVersionCode else packageInfo.versionCode.toLong()

    private fun userFacingError(error: Exception): String =
        error.message?.takeIf { it.isNotBlank() } ?: "BioModStack could not complete the APK update request."

    companion object {
        private const val KEY_INSTALLER_VERSION_CODE = "installer_version_code"
        private const val KEY_INSTALLER_MANIFEST = "installer_manifest"
        private const val KEY_PENDING_APK_PATH = "pending_apk_path"
        private const val KEY_PENDING_MANIFEST = "pending_manifest"
    }
}
