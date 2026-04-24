# BioModStack Android APK artifacts

This directory tracks manually exported Android wrapper artifacts that are built outside the main BioModStack git repo.

Current staged artifact:
- `BioModStack-debug-phone-2026.04.21-phone-startup-crash-fix-beta-006.apk`

Source wrapper project:
- `/home/dalab/Desktop/BioModStack Cordova Android Project`

Source artifact path:
- `/home/dalab/Desktop/BioModStack Cordova Android Project/BioModStack Android APK/BioModStack-debug-phone-2026.04.21-phone-startup-crash-fix-beta-006.apk`

Verification metadata:
- SHA256: `3b2f94d81a8053df60516963a5eef81519f86f66a4b0aee5fb1cd79d5ede06ba`
- Size: `13990120` bytes

Reason for this artifact:
- Includes the startup crash fix for the Cordova Android shell (`init()` before touching `appView` / guarded `WebView` access).
- Preserves the phone-specific zoom and wide-viewport settings used in the working beta-006 debug APK.
