package org.biomodstack.mobile.apkupdate

import java.net.URI


data class ApkUpdateManifest(
    val channel: String = "stable",
    val versionCode: Long,
    val versionName: String,
    val minSdk: Int,
    val sha256: String,
    val sizeBytes: Long,
    val filename: String = "biomodstack.apk",
    val packageId: String = "org.biomodstack.mobile",
    val signingCertificateSha256: String,
    val publishedAt: String = "",
    val changelog: List<String> = emptyList(),
    val downloadUrl: String,
)


data class ApkArchiveIdentity(
    val packageId: String,
    val versionCode: Long,
    val versionName: String,
    val minSdk: Int,
    val signingCertificateSha256: Set<String>,
)


object ApkUpdatePolicy {
    private const val MAX_APK_BYTES = 250L * 1024L * 1024L
    private const val EXPECTED_PACKAGE_ID = "org.biomodstack.mobile"
    private val SHA256_PATTERN = Regex("^[a-fA-F0-9]{64}$")

    private val APK_FILENAME_PATTERN = Regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,159}\\.apk$")

    fun isUpdateAvailable(manifest: ApkUpdateManifest, installedVersionCode: Long): Boolean =
        manifest.versionCode > installedVersionCode

    fun validationError(
        manifest: ApkUpdateManifest,
        configuredServerUrl: String,
        installedVersionCode: Long,
        deviceSdk: Int,
    ): String? {
        if (manifest.channel != "stable") return "APK update manifest is not for the stable channel."
        if (manifest.packageId != EXPECTED_PACKAGE_ID) return "APK package ID does not match BioModStack."
        if (manifest.versionCode <= 0L) return "Invalid APK version code."
        if (manifest.versionName.isBlank() || manifest.versionName.length > 128) return "Invalid APK version name."
        if (manifest.minSdk !in 1..100) return "Invalid APK minimum SDK."
        if (manifest.minSdk > deviceSdk) return "APK requires a newer Android SDK than this device provides."
        if (!SHA256_PATTERN.matches(manifest.sha256)) return "APK SHA-256 metadata is invalid."
        if (!SHA256_PATTERN.matches(manifest.signingCertificateSha256)) {
            return "Invalid APK signing-certificate digest."
        }
        if (manifest.sizeBytes <= 0L || manifest.sizeBytes > MAX_APK_BYTES) return "Invalid APK size."
        if (!APK_FILENAME_PATTERN.matches(manifest.filename)) return "Invalid APK filename."
        if (manifest.publishedAt.length > 64 || manifest.changelog.size > 50 ||
            manifest.changelog.any { it.length > 1_000 }
        ) return "Invalid APK release metadata."
        val resolvedDownloadUrl = resolveDownloadUrl(configuredServerUrl, manifest.downloadUrl)
            ?: return "APK download must use the configured BioModStack HTTPS origin."
        val expectedPath = "/api/mobile-apk/channels/${manifest.channel}/files/${manifest.filename}"
        if (URI(resolvedDownloadUrl).path != expectedPath) {
            return "APK download path does not match the manifest channel and filename."
        }
        if (manifest.versionCode <= installedVersionCode) {
            return "APK versionCode must be newer than the installed version."
        }
        return null
    }

    fun archiveValidationError(manifest: ApkUpdateManifest, archive: ApkArchiveIdentity): String? {
        if (archive.packageId != EXPECTED_PACKAGE_ID) return "Downloaded APK package ID does not match BioModStack."
        if (archive.versionCode != manifest.versionCode) return "Downloaded APK version code does not match the update manifest."
        if (archive.versionName != manifest.versionName) return "Downloaded APK version name does not match the update manifest."
        if (archive.minSdk != manifest.minSdk) return "Downloaded APK minimum SDK does not match the update manifest."
        val expectedSigner = normalizeFingerprint(manifest.signingCertificateSha256)
            ?: return "APK signing-certificate digest is invalid."
        if (archive.signingCertificateSha256 != setOf(expectedSigner)) {
            return "Downloaded APK signing certificate does not match the update manifest."
        }
        return null
    }

    fun resolveDownloadUrl(configuredServerUrl: String, downloadUrl: String): String? {
        return try {
            val configured = URI(configuredServerUrl)
            if (!configured.scheme.equals("https", ignoreCase = true) || configured.host.isNullOrBlank()) {
                return null
            }
            val origin = URI("https", null, configured.host, effectivePort(configured), "/", null, null)
            val resolved = origin.resolve(downloadUrl).normalize()
            if (!resolved.scheme.equals("https", ignoreCase = true)) return null
            if (!resolved.host.equals(configured.host, ignoreCase = true)) return null
            if (effectivePort(resolved) != effectivePort(configured)) return null
            if (resolved.userInfo != null || resolved.fragment != null || resolved.query != null) return null
            if (!resolved.path.startsWith("/api/mobile-apk/channels/")) return null
            resolved.toString()
        } catch (_: Exception) {
            null
        }
    }

    fun normalizeFingerprint(value: String): String? {
        val compact = value.replace(":", "").replace(" ", "")
        if (compact.isEmpty() || compact.any { !it.isDigit() && it.lowercaseChar() !in 'a'..'f' }) return null
        return compact.uppercase()
    }

    private fun effectivePort(uri: URI): Int = if (uri.port == -1) 443 else uri.port
}

object ApkDownloadPolicy {
    private val contentRangePattern = Regex("^bytes (\\d+)-(\\d+)/(\\d+)$")

    fun partialIdentity(manifest: ApkUpdateManifest, channel: String = "stable"): String {
        val filename = manifest.downloadUrl.substringAfterLast('/')
        return "$channel:${manifest.versionCode}:${manifest.sha256.lowercase()}:${manifest.sizeBytes}:$filename"
    }

    fun shouldRestartResume(
        responseCode: Int,
        contentRange: String,
        requestedOffset: Long,
        expectedSize: Long,
        responseContentLength: Long,
    ): Boolean {
        if (requestedOffset <= 0L) return responseCode != 200
        if (responseCode == 200) {
            return responseContentLength != expectedSize
        }
        if (responseCode != 206) return true
        val match = contentRangePattern.matchEntire(contentRange.trim()) ?: return true
        val start = match.groupValues[1].toLongOrNull() ?: return true
        val end = match.groupValues[2].toLongOrNull() ?: return true
        val total = match.groupValues[3].toLongOrNull() ?: return true
        return start != requestedOffset ||
            end != expectedSize - 1L ||
            total != expectedSize ||
            end - start + 1L != responseContentLength ||
            responseContentLength != expectedSize - requestedOffset
    }
}

object InstallerReturnPolicy {
    fun status(installedVersionCode: Long, launchedVersionCode: Long): String =
        if (installedVersionCode >= launchedVersionCode) "up_to_date" else "available"
}
