# BioModStack Android APK artifacts

This directory contains approved Android wrapper artifacts tracked on `test`.
Native APK promotion to `main` occurs only through the normal reviewed
`test → main` promotion process.

## Current internal-update artifact

- File: `BioModStack-0.4.20-internal-update.apk`
- SHA-256: `e0c7c4cde82f6ca5ae8d22ee21d5c2398935859fb874b784d08b21e9215010c6`
- Source revision: `1d4173ffe768af6e69afc1db09cd62421c6560dd`
- Package: `org.biomodstack.mobile`
- Version: `0.4.20` (`versionCode 420`)
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
