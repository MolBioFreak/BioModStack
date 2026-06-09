# BioModStack Android APK artifacts

This directory tracks manually exported Android wrapper artifacts that are built outside the main BioModStack git repo.

Current staged artifact:
- `BioModStack-debug-phone-2026.06.09-molbio-drag-header-beta-007.apk`

Source wrapper project:
- `/home/dalab/Desktop/BioModStack Android APK/BioModStack Cordova Android Project`

Source artifact path:
- `/home/dalab/Desktop/BioModStack Android APK/BioModStack Cordova Android Project/platforms/android/app/build/outputs/apk/debug/app-debug.apk`

Verification metadata:
- SHA256: `021472cae04f2c55a774c15021ab5f479d94bb2f98751f78627aad8332c91343`
- Size: `9408488` bytes
- Package: `org.biomodstack.mobile`
- Version: `0.1.0` / versionCode `100`
- Signing: Android debug certificate
- SDK: min `24`, target `35`

Mobile UI update published for the existing APK channel:
- Channel: `phone`
- Version: `2026.06.09-155101`
- Manifest: `/mnt/BioModStack/mobile-ui-updates/channels/phone/manifest.json`
- Bundle: `/mnt/BioModStack/mobile-ui-updates/bundles/phone/2026.06.09-155101.zip`
- Bundle SHA256: `a4b8a1290a70f82b96483d76489f4836710f1e9af40277d75b7c31c65fc372be`
- Bundle size: `4723194` bytes
- Asset count: `30`

Reason for this artifact:
- Includes the Mol Bio sequence-header drag-to-scroll fix and SeqViz/overview readability CSS updates.
- Rebuilds the phone Cordova debug APK from the current BioModStack frontend checkout.
- Preserves the phone-specific zoom, wide-viewport, and mobile UI-update settings used by the existing Cordova shell.
- Existing installed APKs on the `phone` channel can receive the same UI through the mobile UI update manifest without reinstalling the native shell.
