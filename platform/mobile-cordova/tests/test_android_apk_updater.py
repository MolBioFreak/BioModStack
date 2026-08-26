from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
PLUGIN = ROOT / 'local-plugins' / 'cordova-plugin-bms-apk-updater'
UI_BUNDLE_PLUGIN = ROOT / 'local-plugins' / 'cordova-plugin-bms-ui-bundle'
XML = PLUGIN / 'plugin.xml'
BRIDGE = PLUGIN / 'src' / 'android' / 'BmsApkUpdatePlugin.kt'
POLICY = PLUGIN / 'src' / 'android' / 'ApkUpdatePolicy.kt'
UPDATER = PLUGIN / 'src' / 'android' / 'BmsApkUpdater.kt'
UPDATER_PROVIDER = PLUGIN / 'src' / 'android' / 'BmsApkFileProvider.kt'
CONFIG = ROOT / 'config.xml'
GRADLE = ROOT / 'build-extras.gradle'
BUILD_SCRIPT = ROOT / 'scripts' / 'build-apk.sh'


def test_plugin_is_native_only_onload_and_scoped_fileprovider() -> None:
    xml = XML.read_text()
    package = PLUGIN / 'package.json'
    assert package.is_file()
    assert 'onload' in xml and 'BmsApkUpdatePlugin' in xml
    assert '<js-module' not in xml
    assert 'REQUEST_INSTALL_PACKAGES' in xml
    assert 'org.biomodstack.mobile.apkupdate.BmsApkFileProvider' in xml
    assert '<resource-file src="res/xml/apk_update_paths.xml" target="res/xml/apk_update_paths.xml"' in xml
    assert 'androidx.webkit:webkit' in xml


def test_updater_file_provider_is_dedicated_and_uri_authority_matches_manifest() -> None:
    xml = XML.read_text()
    updater = UPDATER.read_text()
    assert UPDATER_PROVIDER.is_file()
    provider = UPDATER_PROVIDER.read_text()
    assert 'class BmsApkFileProvider : FileProvider()' in provider
    assert 'android:name="org.biomodstack.mobile.apkupdate.BmsApkFileProvider"' in xml
    assert 'android:authorities="${applicationId}.bms.apk.fileprovider"' in xml
    assert 'src="src/android/BmsApkFileProvider.kt"' in xml
    assert '"${activity.packageName}.bms.apk.fileprovider"' in updater
    assert '"${activity.packageName}.fileprovider"' not in updater
    assert '<provider android:name="androidx.core.content.FileProvider"' not in xml


def test_bridge_is_exact_local_origin_main_frame_bounded_and_not_cordova_exec() -> None:
    bridge = BRIDGE.read_text()
    assert 'WebViewCompat.addWebMessageListener' in bridge
    assert 'setOf("https://localhost")' in bridge
    assert 'isMainFrame' in bridge
    assert 'sourceOrigin' in bridge
    assert 'rawMessage.length !in 1..256' in bridge
    for command in ('getShellInfo', 'checkForApkUpdate', 'installApkUpdate'):
        assert command in bridge
    assert 'addJavascriptInterface' not in bridge
    assert 'cordova.exec' not in bridge
    assert 'execute(' not in bridge


def test_policy_is_same_origin_https_immutable_stable_and_strictly_newer() -> None:
    policy = POLICY.read_text()
    for token in ('org.biomodstack.mobile', 'MAX_APK_BYTES', 'userInfo', 'fragment', 'query',
                  '/api/mobile-apk/channels/', 'manifest.channel != "stable"',
                  'manifest.versionCode <= installedVersionCode', 'expectedPath',
                  'signingCertificateSha256', 'ApkArchiveIdentity',
                  'archive.versionName != manifest.versionName',
                  'archive.minSdk != manifest.minSdk'):
        assert token in policy


def test_updater_resumes_restarts_verifies_archive_and_reconciles_installed_target_or_newer() -> None:
    updater = UPDATER.read_text()
    for token in ('HttpsURLConnection', 'instanceFollowRedirects = false', 'Content-Range',
                  'partialIdentity', 'restartDownloadFromZero', 'bytesWritten = 0L',
                  'MessageDigest.getInstance("SHA-256")', 'getPackageArchiveInfo',
                  'archiveSigners != installedSigners', 'replaceBySameDirectoryRename',
                  'source.renameTo(destination)',
                  'FileProvider.getUriForFile', 'ACTION_MANAGE_UNKNOWN_APP_SOURCES',
                  'KEY_INSTALLER_VERSION_CODE',
                  'InstallerReturnPolicy.status(installedVersionCode(), launchedVersionCode)'):
        assert token in updater
    for api_26_only_token in ('java.nio.file.Files', 'StandardCopyOption', '.toPath()'):
        assert api_26_only_token not in updater


def test_pending_installer_handoff_is_persisted_revalidated_and_reports_denial() -> None:
    updater = UPDATER.read_text()
    for token in (
        'getSharedPreferences("biomodstack-apk-installer"',
        'restorePendingInstaller()',
        'KEY_PENDING_APK_PATH',
        'KEY_PENDING_MANIFEST',
        '.commit()',
        'verifyPendingInstaller(apkFile, manifest)',
        'Pending APK size verification failed.',
        'Pending APK SHA-256 verification failed.',
        'verifyArchiveIdentity(apkFile, manifest)',
        'install_permission_denied',
        'Android installation was not completed.',
    ):
        assert token in updater
    launch_helper = updater.split('private fun launchPendingInstaller', 1)[1].split('private fun ', 1)[0]
    assert launch_helper.index('openPackageInstaller(apkFile, manifest)') < launch_helper.index('clearPendingInstaller()')
    assert 'Android could not open the package installer' in launch_helper


def test_native_events_are_sequenced_and_lint_fails_on_api_compatibility_errors() -> None:
    updater = UPDATER.read_text()
    gradle = GRADLE.read_text()
    assert 'AtomicLong' in updater
    assert '.put("sequence", eventSequence.incrementAndGet())' in updater
    assert 'abortOnError true' in gradle
    assert 'checkReleaseBuilds true' in gradle
    assert 'bmsInternalUpdate' in gradle
    assert 'initWith debug' in gradle
    assert 'debuggable false' in gradle
    assert 'signingConfig signingConfigs.debug' in gradle
    assert 'testBuildType "bmsInternalUpdate"' in gradle
    assert 'BMS_ANDROID_BUILD_VARIANT' in BUILD_SCRIPT.read_text()
    assert 'assembleBmsInternalUpdate' in BUILD_SCRIPT.read_text()


def test_core_runtime_compose_passes_both_apk_authentication_policies() -> None:
    compose = (REPO_ROOT / 'compose.core-runtime.yml').read_text()
    assert 'BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS: ${BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS:-}' in compose
    assert 'BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS: ${BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS:-}' in compose


def test_shell_version_navigation_network_and_release_signing_are_constrained() -> None:
    config = CONFIG.read_text()
    gradle = GRADLE.read_text()
    assert 'version="0.4.6"' in config
    assert 'android-versionCode="406"' in config
    runtime = (ROOT / "cordova.runtime.json").read_text(encoding="utf-8")
    assert '"remoteUiUrl": "https://compute-node.taileb3a90.ts.net/"' in runtime
    assert '<allow-navigation href="https://localhost/*"' in config
    assert 'https://compute-node.taileb3a90.ts.net' in config
    assert 'http://10.0.2.2:8000' in config
    assert '<access origin="*"' not in config
    assert 'androidx.test.runner.AndroidJUnitRunner' in gradle
    for key in ('BMS_ANDROID_KEYSTORE_PATH', 'BMS_ANDROID_KEYSTORE_PASSWORD',
                'BMS_ANDROID_KEY_ALIAS', 'BMS_ANDROID_KEY_PASSWORD'):
        assert key in gradle
    assert 'gradle.taskGraph.whenReady' in gradle
    assert 'assemble|bundle|package|sign|validateSigning|install' in gradle


def test_clean_build_prepares_www_before_adding_android_platform() -> None:
    script = BUILD_SCRIPT.read_text()
    prepare = 'node ./scripts/prepare-bms-assets.mjs --config "$CONFIG_PATH"'
    platform_add = 'npx cordova platform add "android@$ANDROID_PLATFORM_VERSION"'
    assert script.count(prepare) == 1
    assert script.index(prepare) < script.index(platform_add)


def test_internal_update_build_ignores_inherited_home_and_xdg_signing_roots() -> None:
    script = BUILD_SCRIPT.read_text()
    assert "pwd.getpwuid(os.getuid()).pw_dir" in script
    assert 'HOME="$CANONICAL_HOME"' in script
    assert 'XDG_CONFIG_HOME="$HOME/.local/share/biomodstack/cordova-build-config"' in script
    assert "export HOME XDG_CONFIG_HOME" in script


def test_build_reinstalls_tracked_apk_updater_before_prepare() -> None:
    script = BUILD_SCRIPT.read_text()
    remove = 'npx cordova plugin remove "$LOCAL_APK_UPDATER_PLUGIN_ID" --nosave'
    add = 'npx cordova plugin add "$LOCAL_APK_UPDATER_PLUGIN_DIR" --nosave'
    prepare = 'npx cordova prepare android'
    assert 'LOCAL_APK_UPDATER_PLUGIN_DIR=' in script
    assert 'LOCAL_APK_UPDATER_PLUGIN_ID=' in script
    assert remove in script
    assert add in script
    assert script.index(remove) < script.index(add) < script.index(prepare)


def test_build_syncs_authoritative_package_manager_instrumentation_source() -> None:
    script = BUILD_SCRIPT.read_text()
    assert 'UPDATER_ANDROID_TEST_SOURCE' in script
    assert 'BmsPackageManagerIntegrationTest.kt' in script
    assert 'rm -rf "$UPDATER_ANDROID_TEST_PACKAGE_DIR"' in script
    assert 'install -D -m 0644' in script
    assert script.index('rm -rf "$UPDATER_ANDROID_TEST_PACKAGE_DIR"') < script.index('install -D -m 0644')
    assert script.index('npx cordova prepare android') < script.index('UPDATER_ANDROID_TEST_TARGET')


def test_existing_ui_bundle_plugin_declares_only_files_present_in_source() -> None:
    xml = (UI_BUNDLE_PLUGIN / 'plugin.xml').read_text()
    assert 'src="www/bmsUiBundle.js"' in xml
    assert (UI_BUNDLE_PLUGIN / 'www' / 'bmsUiBundle.js').is_file()
