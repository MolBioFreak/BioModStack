# BioModStack Android APK artifacts

This directory contains the approved Android wrapper artifact tracked on `test`.
Native APK promotion to `main` occurs only through the normal reviewed
`test → main` promotion process.

## Current artifact

- File: `BioModStack-debug-phone-2026.06.09-molbio-drag-header-beta-007.apk`
- SHA-256: `021472cae04f2c55a774c15021ab5f479d94bb2f98751f78627aad8332c91343`
- Package: `org.biomodstack.mobile`
- Version: `0.1.0` (`versionCode 100`)
- Signing: Android debug certificate
- SDK: min 24, target 35

Includes the MolBio sequence-header usability update and mobile readability
improvements.

## Superseded artifact

Beta-006 is preserved in the `android-beta-006-archive` GitHub Release for
release provenance. It is not retained in the active source tree.

Native-wrapper build inputs and mobile UI-update distribution are operated
through the supported governed release process. Do not place machine-local
paths, credentials, deployment topology, or mutable staging locations here.
