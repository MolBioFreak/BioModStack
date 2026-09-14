# Private Hugging Face runtime-asset delivery

HF is the preferred **bulk byte-delivery source** for BMS remote preparation.
It does not replace model/source manifests, licensing, scientific admission,
SSH control, result retrieval, or the shared runtime-image lifecycle.

## Supported flow

The existing `services.remote_execution.cache._cache_artifacts` owner is shared
by independent provisioning, saved-Job preload and ordinary bundle staging.
It probes and verifies the worker cache first. A verified hit requires no HF
connection or credential. For a missing selected artifact of at least 8 MiB:

1. The controller consults its deployment-owned private HF bucket.
2. SIF mirrors use `sha256/<selected-sha256>/image.sif`; opaque weights, runtime
   assets and the workflow source archive use `sha256/<selected-sha256>/artifact`.
   The expected digest/size come from the existing authoritative selection, not
   HF filenames, object metadata, a browser request or a new manifest registry.
3. If the object is absent, publishing requires explicit deployment permission.
   The controller pins the selected local inode, hashes its complete bytes,
   uploads using the pinned HF SDK, and rechecks local identity and remote size.
   It does not build images, modify weights, choose another model revision, or
   make an extra full local SIF copy. Existing objects are not intentionally
   replaced.
4. An authenticated HEAD against the fixed HF origin obtains a short-lived,
   single-object CDN read capability. Only that URL and its expiry cross the
   already host-key-pinned SSH channel, in transient stdin—not command argv,
   a job environment, an execution envelope, persisted progress or a token file.
5. The worker downloads bounded ranges directly over TLS into the existing
   private incoming batch. It checks response range/length and the complete
   selected SHA-256/size. It cannot publish from this acquisition action.
6. The controller rechecks its existing ownership/cancellation fence, then calls
   the existing cache ingest owner. SIFs enter the one shared runtime-image
   store with existing immutable modes and durable lifecycle retention; weights
   and code enter the existing ordinary artifact CAS. Normal launch consumes
   these same stores. Acknowledged publication allows removal of that incoming
   batch. Uncertain transfers remain unadmitted, not silently declared complete.

Small support files retain the existing efficient batched SSH transport rather
than incur thousands of cloud requests. Biological inputs, prepared input data,
user results, credentials and relocated support-python are not HF mirrors.
This route does not make the worker join the controller Tailnet or require a
callback listener. It is provider-neutral; Vast remains the current provider.

## Controller configuration

Use the controller's normal protected service environment configuration. Do not
put these settings in model YAML, scientific parameters or worker templates.
The deployment operator supplies an **existing private bucket** and protected
credential file; BMS never creates buckets, changes visibility, upgrades a plan,
rents/starts a worker, or accepts licenses as a side effect.

```ini
BMS_HF_ASSET_BUCKET=YOUR_ACCOUNT/YOUR_PRIVATE_BUCKET
BMS_HF_TOKEN_FILE=/absolute/service-owned/private/hf-token
BMS_HF_ASSET_ALLOW_PUBLISH=1
BMS_HF_ASSET_MAX_BYTES=1000000000000
```

- `BMS_HF_TOKEN_FILE`: current controller UID, regular file, no symlink in any
  path component, one hard link, mode `0600` or `0400`, bounded nonempty HF token.
  The token is read only on the controller. Prefer a fine-grained token limited
  to the selected bucket; read access suffices for pre-existing mirrors, and
  write access is needed only if missing-object publication is enabled.
- `BMS_HF_ASSET_ALLOW_PUBLISH`: `0` (default) or `1`. Enabling publication
  authorizes private cloud storage of the already selected runtime assets;
  the operator must hold the rights to mirror each asset there. Local license
  acceptance or an accessible upstream URL is not by itself permission to
  redistribute weights. Existing acquisition license/admission checks remain
  unchanged. This flag grants neither public redistribution nor input/result
  upload.
- `BMS_HF_ASSET_MAX_BYTES`: positive byte ceiling for bucket usage and each
  selected object; default is a decimal 1 TB guard, not a statement of the HF
  account's quota. Current provider quota/access failures remain blockers.
- With **all** HF settings absent, existing SSH delivery remains compatible.
  Partially configured, invalid, unauthorized, over-limit, corrupt, or missing
  read-only mirrors **fail visibly**; BMS does not silently fall back to SSH.
  Removing all HF settings is an explicit operator rollback to SSH.

The HF SDK is pinned in the normal API `pyproject.toml`/`uv.lock`. Install through
`uv sync --frozen` and apply environment changes through the existing managed
controller service path. No SDK installation or HF login is needed on Vast.

## Connection checks and progress

The normal operator-authenticated API exposes a closed safe projection:

- `GET /api/execution-targets/providers/huggingface`: local configuration status;
  it performs no cloud request and never claims authenticated cloud access.
- `POST /api/execution-targets/providers/huggingface/check`: no body (or `{}`);
  explicitly verifies access to the exact private bucket without writing an
  object or starting a worker. It accepts no token, URL, path, bucket override or
  publication flag. Status returns no credential path, token or capability URL.

Existing preparation/job progress identifies **Preparing private Hugging Face
asset delivery** and **Downloading artifact from Hugging Face**. There is no
new log panel, validation dashboard or scientific-readiness claim. An access
check is neither write-permission qualification nor proof of a full transfer.

## Security and retry boundaries

- CDN access is limited to the explicitly qualified host `us.aws.cdn.hf.co` over
  HTTPS, with the exact signed query and a remaining lifetime at most one hour.
  The worker accepts no extra headers, proxies, userinfo, arbitrary host, or
  redirects. New HF regions/hosts require an explicit policy change and tests,
  not a wildcard or silent redirection.
- BMS cannot shorten an already issued provider signature or immediately revoke
  it by unlinking the controller. A bearer URL can be replayed for that same
  object until it expires. Never log or persist it. The long-lived personal
  credential is not placed on the rented host.
- Downloads use bounded eight-way 8 MiB ranges and finite request/transfer
  deadlines. Expired links permit one fresh-link retry within the same private
  batch, rehashing retained bytes and requiring the complete final digest.
  Other network/auth/integrity failures are not alternate-source approval.
- Acquisition never executes asset contents or publishes a partial. Cancellation
  and API-death handling retain existing operation ownership and admission
  fences. Killing a controller-owned upload process is distinct from proving
  no remote HF object committed; the next source check reconciles metadata.
- HF bucket object keys are mutable at the provider. Its pinned SDK does not
  offer a conditional-create transaction. BMS serializes its own publishers
  sharing the protected credential inode and checks presence again before
  upload, but cannot prevent an unrelated external HF writer racing that key.
  Mandatory complete SHA-256 verification on the worker prevents altered bytes
  from becoming an admitted runtime. Do not describe HF storage itself as WORM.

Real cloud transfer, worker-cache readback, deployed controller identity,
clean-machine installation and scientific execution are separate acceptance
layers. Tests using declared HTTP/transport fixtures attest only their exercised
software boundaries.
