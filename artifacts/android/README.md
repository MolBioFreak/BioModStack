# BioModStack Android APK artifacts

This directory contains approved Android wrapper artifacts tracked on `test`.
Native APK promotion to `main` occurs only through the normal reviewed
`test → main` promotion process.

## Current internal-update artifact

- File: `BioModStack-0.4.15-internal-update.apk`
- SHA-256: `d74495bad7a62052acbdbbb4be8fb4d423f310ec58457683dc5b9daed961cc0e`
- Source revision: `7b3b3245d6b0054c4fb04295dc1fb90dd4d5690c`
- Package: `org.biomodstack.mobile`
- Version: `0.4.15` (`versionCode 415`)
- Build variant: non-debuggable internal update
- Signing certificate SHA-256: `43cce218275179b99aad810bfc246732226a9a408e616d9d5615d5b0709b595a`
- SDK: min 24, target 35

The stable in-app APK update channel publishes the exact hash-bound bytes from
this artifact. The package uses the durable internal-updater signer required by
previous BioModStack APK installations.

## Historical phone artifacts

The retained beta APKs predate the governed internal-update channel. Their
metadata and adjacent SHA-256 files remain available for provenance.

Native-wrapper build inputs and mobile UI-update distribution are operated
through the supported governed release process. Do not place machine-local
paths, credentials, deployment topology, or mutable staging locations here.
