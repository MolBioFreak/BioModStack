package org.biomodstack.mobile.apkupdate

import android.content.pm.PackageManager
import android.os.Build
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.security.MessageDigest

@RunWith(AndroidJUnit4::class)
class BmsPackageManagerIntegrationTest {
    @Test
    fun installedPackageIdentityAndSignerAreReadableForReconciliation() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION")
            PackageManager.GET_SIGNATURES
        }
        val info = context.packageManager.getPackageInfo(context.packageName, flags)
        val versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }
        val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.signingInfo?.apkContentsSigners?.toList().orEmpty()
        } else {
            @Suppress("DEPRECATION")
            info.signatures?.toList().orEmpty()
        }
        val digests = signatures.map { signature ->
            MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())
                .joinToString("") { "%02X".format(it) }
        }.toSet()

        assertEquals("org.biomodstack.mobile", info.packageName)
        assertEquals(403L, versionCode)
        assertEquals("0.4.3", info.versionName)
        assertEquals(24, info.applicationInfo?.minSdkVersion)
        assertTrue(digests.isNotEmpty())
        assertEquals("up_to_date", InstallerReturnPolicy.status(versionCode, 403L))
        assertEquals("available", InstallerReturnPolicy.status(versionCode, 404L))
    }

    @Test
    fun copiedApkArchiveIdentityAndSignerAreReadableBeforeInstallerHandoff() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val copiedApk = File(context.cacheDir, "downloaded-candidate.apk")
        File(context.applicationInfo.sourceDir).copyTo(copiedApk, overwrite = true)
        try {
            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                PackageManager.GET_SIGNING_CERTIFICATES
            } else {
                @Suppress("DEPRECATION")
                PackageManager.GET_SIGNATURES
            }
            @Suppress("DEPRECATION")
            val archive = context.packageManager.getPackageArchiveInfo(copiedApk.absolutePath, flags)
            assertTrue(archive != null)
            requireNotNull(archive)
            val versionCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) archive.longVersionCode else {
                @Suppress("DEPRECATION")
                archive.versionCode.toLong()
            }
            val signatures = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                archive.signingInfo?.apkContentsSigners?.toList().orEmpty()
            } else {
                @Suppress("DEPRECATION")
                archive.signatures?.toList().orEmpty()
            }

            assertEquals("org.biomodstack.mobile", archive.packageName)
            assertEquals(403L, versionCode)
            assertEquals("0.4.3", archive.versionName)
            assertEquals(24, archive.applicationInfo?.minSdkVersion)
            assertTrue(signatures.isNotEmpty())
        } finally {
            copiedApk.delete()
        }
    }
}
