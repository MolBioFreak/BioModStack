package org.biomodstack.mobile.apkupdate

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApkUpdatePolicyTest {
    private val signer = "A".repeat(64)

    private fun manifest(
        channel: String = "stable",
        versionCode: Long = 200,
        versionName: String = "0.2.0",
        minSdk: Int = 24,
        downloadUrl: String = "/api/mobile-apk/channels/stable/files/biomodstack.apk",
    ) = ApkUpdateManifest(
        channel = channel,
        versionCode = versionCode,
        versionName = versionName,
        minSdk = minSdk,
        sha256 = "b".repeat(64),
        sizeBytes = 1024,
        filename = "biomodstack.apk",
        signingCertificateSha256 = signer,
        downloadUrl = downloadUrl,
    )

    @Test
    fun manifestPolicyPinsStableChannelOriginPathAndStrictlyNewerVersion() {
        assertNull(
            ApkUpdatePolicy.validationError(
                manifest(),
                "https://compute-node.taileb3a90.ts.net",
                installedVersionCode = 100,
                deviceSdk = 35,
            )
        )
        assertNotNull(
            ApkUpdatePolicy.validationError(
                manifest(channel = "beta"),
                "https://compute-node.taileb3a90.ts.net",
                installedVersionCode = 100,
                deviceSdk = 35,
            )
        )
        assertNotNull(
            ApkUpdatePolicy.validationError(
                manifest(downloadUrl = "https://evil.example/api/mobile-apk/channels/stable/files/biomodstack.apk"),
                "https://compute-node.taileb3a90.ts.net",
                installedVersionCode = 100,
                deviceSdk = 35,
            )
        )
        assertNotNull(
            ApkUpdatePolicy.validationError(
                manifest(),
                "https://compute-node.taileb3a90.ts.net",
                installedVersionCode = 200,
                deviceSdk = 35,
            )
        )
    }

    @Test
    fun archivePolicyChecksEveryDeclaredIdentityField() {
        val expected = ApkArchiveIdentity(
            packageId = "org.biomodstack.mobile",
            versionCode = 200,
            versionName = "0.2.0",
            minSdk = 24,
            signingCertificateSha256 = setOf(signer),
        )
        assertNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected))
        assertNotNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected.copy(packageId = "evil.example")))
        assertNotNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected.copy(versionCode = 201)))
        assertNotNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected.copy(versionName = "0.2.1")))
        assertNotNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected.copy(minSdk = 26)))
        assertNotNull(ApkUpdatePolicy.archiveValidationError(manifest(), expected.copy(signingCertificateSha256 = setOf("C".repeat(64)))))
    }

    @Test
    fun installerReturnTreatsInstalledTargetOrNewerVersionAsSuccess() {
        assertEquals("available", InstallerReturnPolicy.status(installedVersionCode = 199, launchedVersionCode = 200))
        assertEquals("up_to_date", InstallerReturnPolicy.status(installedVersionCode = 200, launchedVersionCode = 200))
        assertEquals("up_to_date", InstallerReturnPolicy.status(installedVersionCode = 201, launchedVersionCode = 200))
    }

    @Test
    fun rangePolicyRequiresCoherentContinuationAndExactLength() {
        assertEquals(false, ApkDownloadPolicy.shouldRestartResume(206, "bytes 100-999/1000", 100, 1000, 900))
        assertTrue(ApkDownloadPolicy.shouldRestartResume(206, "bytes 99-999/1000", 100, 1000, 900))
        assertTrue(ApkDownloadPolicy.shouldRestartResume(206, "bytes 100-999/1001", 100, 1000, 900))
        assertTrue(ApkDownloadPolicy.shouldRestartResume(206, "bytes 100-999/1000", 100, 1000, 899))
        assertTrue(ApkDownloadPolicy.shouldRestartResume(206, "bytes 100-998/1000", 100, 1000, 900))
        assertTrue(ApkDownloadPolicy.shouldRestartResume(206, "bytes 0-999/1000", 0, 1000, 1000))
    }
}
