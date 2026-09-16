"""Controller-side, inference-dependency-free MSA API preparation (contract v1).

Sources (retrieved 2026-09-07):
https://github.com/sokrypton/ColabFold/blob/main/colabfold/colabfold.py
https://github.com/sokrypton/ColabFold#faq
https://neurosnap.ai/blog/post/66b00dacec3f2aa9b4be703a
https://neurosnap.ai/api/service/mmseqs2%20MSA%20Generation

Public entry point: prepare_msa(sequences=..., provider=..., settings=...,
cache_root=Path(...), credential_file=None, cache_only=False). Cache-only misses
raise MSAAPIError without credential access or HTTP. validate_settings(provider,
settings) performs no-IO prequeue validation and returns expanded adapter keys;
Neurosnap also accepts msa_neurosnap_coverage_percent, msa_neurosnap_identity_percent,
msa_neurosnap_max_sequences, msa_neurosnap_force_uppercase and
msa_neurosnap_pad_sequences. Supplying both an alias and its short key is rejected.
Settings are CLOSED; defaults are expanded by effective_settings and included in
the immutable request identity.
ColabFold: use_env=True, use_filter=True, use_templates=False (True unsupported),
pairing_mode='unpaired' ('paired', 'unpaired_paired'), pairing_strategy='greedy'
('complete'). Paired requests require >=2 distinct chains, without duplicates.
Neurosnap: coverage=35, identity_threshold=50, max_sequences=1000000,
force_uppercase=False, pad_sequences=False, pairing_mode='unpaired'. Only one
monomer with native insertion semantics is supported; archives/ambiguous output
layouts fail closed. No model applicability or consumer qualification is implied.

Production must run ONLY on the designated controller with one fixed egress IP;
all callers must share BMS_MSA_API_STATE_ROOT (default user cache directory).
The host-wide lock AND durable active marker prevent overlapping public requests,
including across different cache roots and after interrupted/ambiguous submission.
This does not replace deployment's controller/egress admission policy. Workers
receive artifacts, never this client's credentials or submission authority.

No POST is retried. A lost submit response blocks reconciliation, not resubmission.
Timeout/interruption leaves the remote ID durable for same-ID recovery. Cancel is
explicit (MSAClient.cancel); no automatic cancellation on timeouts, no claim of
refunds. ColabFold has no documented cancellation endpoint and is not sent one.
Database version is unknown, not fabricated; cache is pinned to first result and
must not be reused as proof of a currently identical remote database.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
import time
from urllib.parse import quote, urlsplit

SERVICES = {"colabfold_api": "https://api.colabfold.com",
            "neurosnap_api": "https://neurosnap.ai/api"}
REVISION = "bms-msa-api-v1"
MAX_BYTES = 256 * 1024 * 1024


class MSAAPIError(RuntimeError):
    """Sanitized failure; remote response bodies/credentials are never included."""


class MSATransportError(MSAAPIError):
    """Transient transport failure; safe reads may resume, POSTs may not."""


class MSACacheMiss(MSAAPIError):
    """No published identity-qualified entry; never corruption or pending work."""

    def __init__(self):
        super().__init__("no verified provider/settings/query-bound cached MSA")


class ReconciliationRequired(MSAAPIError):
    """Do not delete journal/active marker or repeat the submission."""


class PendingMSA(MSAAPIError):
    """Bounded polling ended; call again to resume the existing remote job."""

    def __init__(self, message, *, operation=None):
        super().__init__(message)
        # Project only known non-secret fields, never paths, sequences, headers,
        # response bodies or arbitrary provider metadata. This is not a store.
        self.operation = None
        if isinstance(operation, dict):
            safe = {}
            if isinstance(operation.get('provider'), str) and operation['provider'] in SERVICES:
                safe['provider'] = operation['provider']
            digest = operation.get('request_digest')
            if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest):
                safe['request_digest'] = digest
            tickets = operation.get('tickets')
            if isinstance(tickets, dict):
                safe['tickets'] = {}
                for role in ('unpaired', 'paired'):
                    ticket = tickets.get(role)
                    if not isinstance(ticket, dict):
                        continue
                    item = {}
                    if ticket.get('phase') in ('submitting', 'polling', 'complete', 'terminal'):
                        item['phase'] = ticket['phase']
                    remote_id = ticket.get('remote_id')
                    if isinstance(remote_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', remote_id):
                        item['remote_id'] = remote_id
                    safe['tickets'][role] = item
            delay = operation.get('retry_after_seconds')
            if isinstance(delay, (int, float)) and not isinstance(delay, bool) and math.isfinite(delay) and delay >= 0:
                safe['retry_after_seconds'] = delay
            self.operation = safe or None


class MSAPreparationInterrupted(PendingMSA):
    """Local polling stopped, not remote cancellation; durable tickets remain."""


_preparation_stop = ContextVar('bms_msa_preparation_stop', default=None)


@contextmanager
def preparation_stop_scope(event):
    token = _preparation_stop.set(event)
    try:
        yield
    finally:
        _preparation_stop.reset(token)


def _check_preparation_stop():
    event = _preparation_stop.get()
    if event is not None and event.is_set():
        raise MSAPreparationInterrupted('Local MSA polling stopped; retain/reconcile the provider ticket')


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def effective_settings(provider: str, settings: dict, sequences: list[str]) -> dict:
    """Validate the complete supported scientific request, without IO/submission."""
    if provider not in SERVICES or not isinstance(settings, dict):
        raise MSAAPIError("unsupported provider or settings object")
    if not isinstance(sequences, list) or not sequences or len(sequences) > 10:
        raise MSAAPIError("sequences must contain 1-10 protein chains")
    for seq in sequences:
        if not isinstance(seq, str) or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYBXZJUO]+", seq):
            raise MSAAPIError("protein sequences must be uppercase, ungapped residue strings")
        minimum = 20 if provider == 'neurosnap_api' else 1
        if not minimum <= len(seq) <= 25000:
            raise MSAAPIError(f"supported sequence length is {minimum}-25000 residues")
    aliases = {
        "msa_neurosnap_coverage_percent": "coverage",
        "msa_neurosnap_identity_percent": "identity_threshold",
        "msa_neurosnap_max_sequences": "max_sequences",
        "msa_neurosnap_force_uppercase": "force_uppercase",
        "msa_neurosnap_pad_sequences": "pad_sequences",
    } if provider == "neurosnap_api" else {}
    settings = dict(settings)
    for alias, key in aliases.items():
        if alias in settings:
            if key in settings:
                raise MSAAPIError("duplicate native/canonical setting aliases")
            settings[key] = settings.pop(alias)
    if provider == "colabfold_api":
        defaults = dict(use_env=True, use_filter=True, use_templates=False,
                        pairing_mode="unpaired", pairing_strategy="greedy")
    else:
        defaults = dict(coverage=35, identity_threshold=50, max_sequences=1000000,
                        force_uppercase=False, pad_sequences=False, pairing_mode="unpaired")
    if set(settings) - set(defaults):
        raise MSAAPIError("unsupported scientific setting keys")
    result = {**defaults, **settings}
    for key, default in defaults.items():
        if isinstance(default, bool) and type(result[key]) is not bool:
            raise MSAAPIError("boolean settings require boolean values")
    if provider == "colabfold_api":
        if result["use_templates"]:
            raise MSAAPIError("template retrieval is not supported by the MSA artifact contract")
        if result["pairing_mode"] not in ("unpaired", "paired", "unpaired_paired"):
            raise MSAAPIError("unsupported pairing_mode")
        if result["pairing_strategy"] not in ("greedy", "complete"):
            raise MSAAPIError("unsupported pairing_strategy")
        if result["pairing_mode"] != "unpaired":
            if len(sequences) < 2 or len(set(sequences)) != len(sequences):
                raise MSAAPIError("paired search requires at least two distinct, nonduplicate chains")
            if not result["use_filter"]:
                raise MSAAPIError("unfiltered paired search is not documented")
    else:
        if len(sequences) != 1 or result["pairing_mode"] != "unpaired":
            raise MSAAPIError("Neurosnap multi-chain/pairing output semantics are not qualified")
        if result["force_uppercase"] or result["pad_sequences"]:
            raise MSAAPIError("uppercase/padding transformations are not supported for native A3M")
        for key, low, high in (("coverage", 10, 90), ("identity_threshold", 25, 100),
                               ("max_sequences", 10, 1000000)):
            value = result[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise MSAAPIError("Neurosnap numeric setting outside documented bounds")
            if key == "max_sequences" and value != int(value):
                raise MSAAPIError("max_sequences must be an integer")
            result[key] = int(value) if value == int(value) else value
    return result


def validate_settings(provider: str, settings: dict) -> dict:
    """No-IO prequeue validation; returns expanded short/native adapter keys.

    Also accepts the five canonical msa_neurosnap_* keys from the model schema.
    Actual chain applicability still requires effective_settings(..., sequences).
    """
    sequences = ["A" * 20, "C" * 20] if provider == "colabfold_api" else ["A" * 20]
    return effective_settings(provider, settings, sequences)


def _directory(path, *, create=True):
    path = Path(path).absolute()
    # Trusted controller-owned roots only; reject symlink ancestors, even within root.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise MSAAPIError("symlink storage paths are forbidden")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        info = path.stat()
    except FileNotFoundError:
        raise MSACacheMiss() from None
    if not stat.S_ISDIR(info.st_mode):
        raise MSAAPIError("storage path must be a directory")
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise MSAAPIError("storage directory must be controller-owned and not publicly writable")
    return path


def _read(path, limit=MAX_BYTES):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise MSAAPIError("invalid or oversized stored artifact")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise MSAAPIError("stored artifact exceeds size limit")
            return data
    except OSError:
        raise MSAAPIError("stored artifact unavailable or unsafe") from None


def _atomic(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load(path):
    try:
        return json.loads(_read(path, 4 * 1024 * 1024))
    except (ValueError, UnicodeError):
        raise MSAAPIError("invalid stored manifest") from None


def _immutable(path, data):
    if path.exists() or path.is_symlink():
        if _read(path) != data:
            raise MSAAPIError("immutable cache artifact conflict/corruption")
    else:
        _atomic(path, data)


def _credential(path):
    if path is None or not Path(path).is_absolute():
        raise MSAAPIError("Neurosnap requires an absolute protected controller credential file")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or not 1 <= info.st_size <= 4096):
                raise MSAAPIError("credential file must be private and controller-owned")
            key = stream.read(4097).decode("ascii").strip()
        if not key or not re.fullmatch(r"[!-~]+", key):
            raise MSAAPIError("invalid credential file format")
        return key
    except (OSError, UnicodeError):
        raise MSAAPIError("credential file unavailable or unsafe") from None


def validate_a3m(data: bytes, sequence: str) -> int:
    """Validate query and every aligned width, preserving insertion case and bytes."""
    try:
        text = data.decode("ascii")
    except UnicodeError:
        raise MSAAPIError("A3M must be ASCII") from None
    rows = []
    current = None
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith(">"):
            # Native MMseqs headers contain tab-separated hit metadata.
            if len(line) == 1 or any(ord(c) < 32 and c != '\t' for c in line):
                raise MSAAPIError("invalid A3M header")
            if current is not None:
                rows.append(current)
            current = ""
        elif current is None or not re.fullmatch(r"[A-Za-z.-]+", line):
            raise MSAAPIError("invalid A3M sequence data")
        else:
            current += line
    if current is not None:
        rows.append(current)
    if not rows or rows[0] != sequence:
        raise MSAAPIError("A3M query identity mismatch")
    for row in rows:
        aligned = re.sub(r"[a-z.]", "", row)
        if len(aligned) != len(sequence) or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWYBXZJUO-]+", aligned):
            raise MSAAPIError("invalid A3M aligned row width/residues")
    return len(rows)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    body: bytes
    retry_after: str | None = None
    location: str | None = field(default=None, repr=False)


class RequestsTransport:
    """No redirects, environment proxies/netrc, automatic retries or body logging."""
    def request(self, method, url, *, headers, data=None, files=None, timeout=(6.1, 30),
                max_bytes=MAX_BYTES):
        import requests
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(method, url, headers=headers, data=data, files=files,
                                     timeout=timeout, allow_redirects=False, stream=True) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise MSAAPIError("remote response exceeds size limit")
                        chunks.append(chunk)
                    return HTTPResponse(response.status_code, b"".join(chunks),
                                        response.headers.get("Retry-After"), response.headers.get("Location"))
        except requests.RequestException:
            raise MSATransportError("provider transport failure") from None


@dataclass(frozen=True)
class ClientConfig:
    controller_root: Path | None = None
    max_polls: int = 120
    poll_seconds: float = 5
    safe_attempts: int = 3
    timeout: tuple[float, float] = (6.1, 30)
    max_bytes: int = MAX_BYTES


class MSAClient:
    """Small synchronous adapter. Injectable transport/sleep are for offline tests.

    Operational bounds are NOT scientific settings. A pending return is an error
    with durable state, never an empty/fabricated MSA. Cache corruption is blocked,
    not silently repaired under the same immutable result identity.
    """
    def __init__(self, *, config=None, transport=None, sleep=time.sleep):
        self.config = config or ClientConfig()
        if (not 1 <= self.config.max_polls <= 1000 or not 1 <= self.config.safe_attempts <= 5
                or not 0 <= self.config.poll_seconds <= 60
                or not 1 <= self.config.max_bytes <= MAX_BYTES
                or len(self.config.timeout) != 2
                or any(not 0 < x <= 120 for x in self.config.timeout)):
            raise MSAAPIError("invalid bounded client configuration")
        self.transport = transport or RequestsTransport()
        self.sleep = sleep

    def _pause(self, seconds):
        event = _preparation_stop.get()
        if event is None:
            self.sleep(seconds)
        else:
            event.wait(seconds)
            _check_preparation_stop()

    @contextmanager
    def _authority(self, provider):
        root = self.config.controller_root or Path(os.environ.get(
            "BMS_MSA_API_STATE_ROOT", str(Path.home() / ".cache/biomodstack/msa-api-controller")))
        root = _directory(root)
        fd = os.open(root / (provider + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PendingMSA("provider controller busy; requests are serialized") from None
            yield root / (provider + "-active.json")
        finally:
            os.close(fd)

    def _signed_output(self, response, path, key):
        # Neurosnap's native file endpoint returns a signed Cloudflare R2 URL.
        # Only this download boundary may cross origins, with NO API credential.
        location = response.location or ''
        try:
            target = urlsplit(location)
            valid = (target.scheme == 'https' and not target.username and not target.password
                     and target.port in (None, 443) and not target.fragment
                     and re.fullmatch(r'ns-job-files\.[0-9a-f]{32}\.r2\.cloudflarestorage\.com', target.hostname or '')
                     and target.path == '/' + path.removeprefix('/job/file/')
                     and bool(target.query) and not any(ord(c) < 33 for c in location)
                     and not (key and key in location))
        except ValueError:
            valid = False
        if not valid:
            raise MSAAPIError('unsafe provider file redirect; existing state retained')
        _check_preparation_stop()
        try:
            result = self.transport.request('GET', location, headers={},
                                            timeout=self.config.timeout, max_bytes=self.config.max_bytes)
        except MSATransportError:
            raise PendingMSA('signed output transport unavailable; resume the existing ticket') from None
        except Exception:
            raise MSAAPIError('signed output download failed; existing state retained') from None
        # One explicit cross-origin hop only; never chase arbitrary redirects.
        return result

    def _http(self, provider, path, headers, *, method="GET", data=None, files=None):
        if provider == "colabfold_api":
            from biomodstack_msa_controller import require_controller_submission
            require_controller_submission(SERVICES[provider])
        attempts = self.config.safe_attempts if method == "GET" else 1
        for attempt in range(attempts):
            # A durable POST intent is an in-flight transaction: finish recording
            # its response/ambiguity. Cancellation prevents subsequent GETs/roles.
            if method == 'GET':
                _check_preparation_stop()
            response = None
            try:
                response = self.transport.request(
                    method, SERVICES[provider] + path, headers=headers, data=data, files=files,
                    timeout=self.config.timeout, max_bytes=self.config.max_bytes)
            except MSATransportError:
                if attempt + 1 == attempts:
                    if method == 'GET':
                        raise PendingMSA('provider transport unavailable; resume the existing ticket') from None
                    raise MSAAPIError('provider transport failure; existing state retained') from None
            except MSAAPIError:
                # Unsafe/oversized content is not a transient availability error.
                raise
            except Exception:
                if attempt + 1 == attempts:
                    raise MSAAPIError("provider transport failure; existing state retained") from None
            if response is not None:
                if (provider == 'neurosnap_api' and method == 'GET'
                        and path.startswith('/job/file/') and response.status in (302, 303, 307, 308)):
                    response = self._signed_output(response, path, headers.get('X-API-KEY'))
                if len(response.body) > self.config.max_bytes:
                    raise MSAAPIError("remote response exceeds size limit")
                key = headers.get("X-API-KEY")
                if key and key.encode() in response.body:
                    raise MSAAPIError("provider response contained credential material")
                if response.status == 200:
                    return response.body
                if response.status not in (408, 429, 500, 502, 503, 504) or method != 'GET':
                    raise MSAAPIError(f"provider HTTP {response.status}; existing state retained")
            delay = min(60, self.config.poll_seconds * 2 ** attempt)
            if response and response.retry_after:
                try:
                    try:
                        retry = float(response.retry_after)
                    except ValueError:
                        from email.utils import parsedate_to_datetime
                        retry_at = parsedate_to_datetime(response.retry_after)
                        if retry_at.tzinfo is None:
                            raise ValueError('Retry-After date has no timezone')
                        retry = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    if not math.isfinite(retry):
                        raise ValueError('nonfinite Retry-After')
                    delay = max(delay, max(0, retry))
                except (ValueError, TypeError, OverflowError):
                    raise PendingMSA('provider Retry-After is invalid; reconcile before retry',
                                     operation={'retry_after_seconds': 60}) from None
            # Exhausted safe reads are a durable wait, not failed science. This
            # same exception reaches the existing local/remote launch waiter.
            if delay > 60 or attempt + 1 == attempts:
                message = ('provider Retry-After requires deferred retry; resume the existing ticket'
                           if response and response.retry_after else
                           'provider temporarily unavailable; resume the existing ticket')
                raise PendingMSA(message, operation={'retry_after_seconds': delay})
            self._pause(delay)
        raise MSAAPIError("safe request retry budget exhausted")

    def _get_json(self, *args, **kwargs):
        try:
            return json.loads(self._http(*args, **kwargs))
        except (ValueError, UnicodeError):
            raise MSAAPIError("provider returned invalid JSON") from None

    def _identity(self, sequences, provider, settings):
        effective = effective_settings(provider, settings, sequences)
        identity = dict(revision=REVISION, provider=provider, service=SERVICES[provider],
                        sequences=sequences, settings=effective)
        return identity, _hash(_json(identity))

    def _ticket(self, *, provider, role, sequences, settings, digest, entry, state, active, headers):
        tickets = state.setdefault("tickets", {})
        ticket = tickets.get(role)
        binding = dict(request_digest=digest, cache_entry=str(entry), role=role)
        current_active = _load(active) if active.exists() else None
        if current_active is not None and current_active != binding and not (ticket and ticket.get("phase") == "complete"):
            raise ReconciliationRequired("another provider operation is pending; reconcile it first")
        if ticket is None:
            _check_preparation_stop()
            # Both records precede POST. A crash in either window fails closed.
            ticket = {"phase": "submitting", "submitted_at": _now()}
            tickets[role] = ticket
            _atomic(entry / "state.json", _json(state))
            _atomic(active, _json(binding))
            if provider == "colabfold_api":
                unique = list(dict.fromkeys(sequences))
                query = "".join(f">{101+i}\n{seq}\n" for i, seq in enumerate(unique))
                if role == "paired":
                    mode = "pair" + settings["pairing_strategy"] + ("-env" if settings["use_env"] else "")
                else:
                    mode = ("env" if settings["use_env"] else "all") if settings["use_filter"] else (
                        "env-nofilter" if settings["use_env"] else "nofilter")
                path = "/ticket/pair" if role == "paired" else "/ticket/msa"
                kwargs = dict(data={"q": query, "mode": mode})
            else:
                fields = {"Query Sequence": json.dumps({"aa": {"query": sequences[0]}, "dna": {}, "rna": {}}),
                          "Coverage": str(settings["coverage"]),
                          "Identity threshold": str(settings["identity_threshold"]),
                          "Max Sequences": str(settings["max_sequences"]),
                          "Force Uppercase": "false", "Pad Sequences": "false"}
                kwargs = dict(files={key: (None, value) for key, value in fields.items()})
                path = "/job/submit/" + quote("mmseqs2 MSA Generation", safe="")
            try:
                result = self._get_json(provider, path, headers, method="POST", **kwargs)
                remote_id = result.get("id") if provider == "colabfold_api" and isinstance(result, dict) else result
                if not isinstance(remote_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", remote_id):
                    raise MSAAPIError("invalid remote ID")
            except Exception:
                raise ReconciliationRequired("submission outcome ambiguous/rejected; never automatically resubmit") from None
            ticket.update(remote_id=remote_id, phase="polling")
            # Commit ID BEFORE any status/download request. Failed write leaves submitting.
            _atomic(entry / "state.json", _json(state))
        if not ticket.get("remote_id"):
            raise ReconciliationRequired("submission has no durable remote ID; manual reconciliation required")
        remote_id = ticket["remote_id"]
        if not isinstance(remote_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", remote_id):
            raise ReconciliationRequired("invalid stored remote ID")
        if ticket["phase"] == "terminal":
            if current_active == binding:
                _atomic(active, _json(None))
            raise MSAAPIError("remote job terminated without usable output")
        if ticket["phase"] != "complete":
            _atomic(active, _json(binding))
            for poll in range(self.config.max_polls):
                path = "/ticket/" if provider == "colabfold_api" else "/job/status/"
                result = self._get_json(provider, path + remote_id, headers)
                status = result.get("status") if provider == "colabfold_api" and isinstance(result, dict) else result
                if status in ("COMPLETE", "completed"):
                    ticket.update(phase="complete", completed_at=_now())
                    _atomic(entry / "state.json", _json(state))
                    _atomic(active, _json(None))
                    break
                if status in ("ERROR", "failed", "deleted", "cancelled"):
                    ticket.update(phase="terminal", terminal_status=status)
                    _atomic(entry / "state.json", _json(state))
                    _atomic(active, _json(None))
                    raise MSAAPIError("remote job terminated without usable output")
                if status not in ("PENDING", "RUNNING", "pending", "running"):
                    raise PendingMSA("unknown provider status; existing ID retained")
                if poll + 1 < self.config.max_polls:
                    self._pause(self.config.poll_seconds)
            else:
                raise PendingMSA("poll budget exhausted; resume with the same request")
        elif current_active == binding:
            _atomic(active, _json(None))
        return remote_id

    def _outputs(self, provider, role, remote_id, sequences, settings, entry, headers, *, state=None):
        # Pin downloaded native bytes in the existing ticket journal before
        # publishing them. A paired-role retry must not re-download a completed
        # unpaired result (even gzip metadata can change between GETs).
        native = []
        native_name = 'native-' + role + ('.a3m' if provider == 'neurosnap_api' else '.tar.gz')
        ticket = state['tickets'][role] if state is not None else None
        checkpoint = ticket.get('output') if ticket is not None else None
        data = None
        present = (entry / native_name).exists() or (entry / native_name).is_symlink()
        if checkpoint is not None:
            if (not isinstance(checkpoint, dict) or set(checkpoint) != {'name', 'sha256'}
                    or checkpoint.get('name') != native_name
                    or not isinstance(checkpoint.get('sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', checkpoint['sha256'])):
                raise ReconciliationRequired('invalid native output checkpoint')
            if present:
                data = _read(entry / native_name)
                if _hash(data) != checkpoint['sha256']:
                    raise MSAAPIError('native output checkpoint corruption')
        elif ticket is not None and present:
            raise ReconciliationRequired('interrupted native output lacks a durable digest; reconcile it')

        def store_native(data):
            record = {'name': native_name, 'sha256': _hash(data)}
            if checkpoint is not None and checkpoint != record:
                raise MSAAPIError('provider output changed after durable download checkpoint')
            if ticket is not None and checkpoint is None:
                ticket['output'] = record
                _atomic(entry / 'state.json', _json(state))
            return self._store(entry, native_name, data)
        if provider == "neurosnap_api":
            if data is None:
                metadata = self._get_json(provider, "/job/data/" + remote_id, headers)
                outputs = metadata.get("out") if isinstance(metadata, dict) else None
                if not isinstance(outputs, list) or len(outputs) > 1000:
                    raise MSAAPIError("invalid Neurosnap output listing")
                names = []
                for item in outputs:
                    if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str):
                        raise MSAAPIError("invalid Neurosnap output descriptor")
                    name = item[0]
                    if (not name or PurePosixPath(name).name != name or "\\" in name
                            or name in (".", "..") or any(ord(c) < 32 for c in name)):
                        raise MSAAPIError("unsafe Neurosnap output filename")
                    if name.lower().endswith(".a3m"):
                        names.append(name)
                if len(names) != 1:
                    raise MSAAPIError("expected exactly one listed native monomer A3M; layout unqualified")
                data = self._http(provider, "/job/file/" + remote_id + "/out/" + quote(names[0], safe=""), headers)
            validate_a3m(data, sequences[0])
            native.append(store_native(data))
            return [data], native
        if data is None:
            data = self._http(provider, "/result/download/" + remote_id, headers)
        wanted = ["pair.a3m"] if role == "paired" else ["uniref.a3m"] + (
            ["bfd.mgnify30.metaeuk30.smag30.a3m"] if settings["use_env"] else [])
        members = {}
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                total, count = 0, 0
                for member in archive:
                    count += 1
                    total += member.size
                    if (count > 1000 or total > self.config.max_bytes or member.size < 0
                            or not member.isfile() or PurePosixPath(member.name).name != member.name
                            or "\\" in member.name or member.name in members):
                        raise MSAAPIError("unsafe/oversized native archive")
                    members[member.name] = None
                    if member.name in wanted:
                        stream = archive.extractfile(member)
                        if stream is None:
                            raise MSAAPIError("missing archive file content")
                        members[member.name] = stream.read(self.config.max_bytes + 1)
        except (tarfile.TarError, OSError, EOFError):
            raise MSAAPIError("invalid native ColabFold archive") from None
        unique = list(dict.fromkeys(sequences))
        combined = {101 + i: b"" for i in range(len(unique))}
        for name in wanted:
            if not isinstance(members.get(name), bytes):
                raise MSAAPIError("documented ColabFold A3M missing")
            blocks = {}
            for block in members[name].split(b"\x00"):
                block = block.lstrip(b"\r\n")
                if not block:
                    continue
                header = block.splitlines()[0]
                if not re.fullmatch(rb">[0-9]+", header):
                    raise MSAAPIError("invalid native ColabFold query identifier")
                identifier = int(header[1:])
                if identifier not in combined or identifier in blocks:
                    raise MSAAPIError("duplicate/unknown native ColabFold query identifier")
                validate_a3m(block, unique[identifier - 101])
                blocks[identifier] = block if block.endswith(b"\n") else block + b"\n"
            if set(blocks) != set(combined):
                raise MSAAPIError("missing ColabFold query/chain")
            for identifier, block in blocks.items():
                combined[identifier] += block
        result = [combined[101 + unique.index(seq)] for seq in sequences]
        if role == "paired" and len({validate_a3m(a, s) for a, s in zip(result, sequences)}) != 1:
            raise MSAAPIError("native paired chain row counts differ")
        native.append(store_native(data))
        return result, native

    @staticmethod
    def _store(entry, name, data):
        _immutable(entry / name, data)
        return {"name": name, "sha256": _hash(data)}

    def _replay(self, entry, identity, digest):
        manifest = _load(entry / "manifest.json")
        if not isinstance(manifest, dict) or manifest.get("identity") != identity or manifest.get("request_digest") != digest:
            raise MSAAPIError("cache request identity mismatch")
        provenance = manifest.get("provenance")
        expected_provenance = dict(provider=identity["provider"], service=identity["service"],
                                   adapter_revision=REVISION, effective_settings=identity["settings"],
                                   settings_digest=_hash(_json(identity["settings"])),
                                   query_digest=_hash(_json(identity["sequences"])))
        if not isinstance(provenance, dict) or any(provenance.get(k) != v for k, v in expected_provenance.items()):
            raise MSAAPIError("cache provenance identity mismatch")
        state = _load(entry / "state.json")
        if (not isinstance(state, dict) or state.get("identity") != identity
                or state.get("request_digest") != digest or state.get("tickets") != provenance.get("tickets")):
            raise MSAAPIError("cache remote ticket provenance mismatch")
        sequences = identity["sequences"]
        roles = self._roles(identity["provider"], identity["settings"])
        if (set(state.get("tickets", {})) != set(roles)
                or any(t.get("phase") != "complete" for t in state["tickets"].values())):
            raise MSAAPIError("cache remote ticket is not complete")
        expected = {(i, role) for role in roles for i in range(len(sequences))}
        seen, artifacts = set(), []
        for item in manifest.get("artifacts", []):
            pair = (item.get("chain_index"), item.get("role"))
            if pair not in expected or pair in seen:
                raise MSAAPIError("cache artifact role/chain mismatch")
            seen.add(pair)
            name = f"chain-{pair[0]}-{pair[1]}.a3m"
            data = _read(entry / name)
            if item.get("name") != name or _hash(data) != item.get("sha256"):
                raise MSAAPIError("cache artifact hash mismatch")
            validate_a3m(data, sequences[pair[0]])
            artifacts.append(dict(chain_index=pair[0], role=pair[1], path=str(entry / name), sha256=item["sha256"]))
        if seen != expected:
            raise MSAAPIError("cache artifacts incomplete")
        native = manifest.get("provenance", {}).get("native_artifacts", [])
        if len(native) != len(roles):
            raise MSAAPIError("native cache artifacts incomplete")
        for item, role in zip(native, roles):
            name = "native-" + role + (".a3m" if identity["provider"] == "neurosnap_api" else ".tar.gz")
            if item.get("name") != name or _hash(_read(entry / name)) != item.get("sha256"):
                raise MSAAPIError("native cache artifact hash mismatch")
        return dict(provider=identity["provider"], request_digest=digest, artifacts=artifacts,
                    provenance=manifest["provenance"], cache_hit=True)

    @staticmethod
    def _roles(provider: str, settings: dict) -> list[str]:
        mode = settings["pairing_mode"]
        return ["unpaired", "paired"] if mode == "unpaired_paired" else [mode]

    def prepare_msa(self, *, sequences: list[str], provider: str, settings: dict,
                    cache_root: Path, credential_file: Path | None = None,
                    cache_only: bool = False) -> dict:
        if provider == 'neurosnap_api' and isinstance(sequences, list) and len(sequences) > 1:
            # Independent unpaired chains use documented monomer jobs. This
            # preserves per-chain cache/recovery and avoids guessing a provider
            # multi-query pairing/output convention. Repeated chains reuse cache.
            if len(sequences) > 10:
                raise MSAAPIError('sequences must contain 1-10 protein chains')
            normalized = [effective_settings(provider, settings, [seq]) for seq in sequences]
            results = [self.prepare_msa(sequences=[seq], provider=provider, settings=settings,
                        cache_root=cache_root, credential_file=credential_file, cache_only=cache_only)
                       for seq in sequences]
            artifacts = [{**artifact, 'chain_index': index}
                         for index, result in enumerate(results) for artifact in result['artifacts']]
            identity = dict(provider=provider, sequences=sequences, settings=normalized[0],
                            chain_requests=[result['request_digest'] for result in results])
            return dict(provider=provider, request_digest=_hash(_json(identity)), artifacts=artifacts,
                        cache_hit=all(result['cache_hit'] for result in results),
                        provenance=dict(service=SERVICES[provider], effective_settings=normalized[0],
                            operation='independent-unpaired-chain-searches',
                            chain_receipts=[result['provenance'] for result in results],
                            database_version=None, live_qualification='not_asserted'))
        identity, digest = self._identity(sequences, provider, settings)
        settings = identity["settings"]
        # Preflight/cache-only lookup is read-only, including a cold miss.
        root = _directory(cache_root, create=not cache_only)
        entry = _directory(root / provider / digest, create=not cache_only)
        # Published artifacts are immutable and revalidated; a cache hit does
        # not need the submission lock or a provider credential.
        if (entry / "manifest.json").exists():
            return self._replay(entry, identity, digest)
        if cache_only:
            raise MSACacheMiss()
        if provider == "colabfold_api":
            from biomodstack_msa_controller import require_controller_submission
            require_controller_submission(SERVICES[provider])
        with self._authority(provider) as active:
            if (entry / "manifest.json").exists():
                return self._replay(entry, identity, digest)
            state_path = entry / "state.json"
            state = _load(state_path) if state_path.exists() else dict(identity=identity, request_digest=digest)
            if state.get("identity") != identity or state.get("request_digest") != digest:
                raise ReconciliationRequired("stored request identity mismatch")
            headers = {"User-Agent": "BioModStack-MSA/1 (https://github.com/BioModStack)"}
            if provider == "neurosnap_api":
                headers["X-API-KEY"] = _credential(credential_file)
            elif credential_file is not None:
                raise MSAAPIError("ColabFold must not receive credentials")
            artifacts, native = [], []
            for role in self._roles(provider, settings):
                try:
                    _check_preparation_stop()
                    remote_id = self._ticket(provider=provider, role=role, sequences=sequences,
                                             settings=settings, digest=digest, entry=entry,
                                             state=state, active=active, headers=headers)
                    output, originals = self._outputs(provider, role, remote_id, sequences, settings, entry, headers, state=state)
                except PendingMSA as exc:
                    raise type(exc)(str(exc), operation={**(exc.operation or {}),
                        "provider": provider, "request_digest": digest,
                        "tickets": state.get("tickets", {})}) from None
                native.extend(originals)
                for index, (data, sequence) in enumerate(zip(output, sequences)):
                    validate_a3m(data, sequence)
                    stored = self._store(entry, f"chain-{index}-{role}.a3m", data)
                    artifacts.append(dict(chain_index=index, role=role, **stored))
            provenance = dict(adapter_revision=REVISION, service=SERVICES[provider],
                              provider=provider, effective_settings=settings,
                              settings_digest=_hash(_json(settings)),
                              query_digest=_hash(_json(sequences)), database_version=None,
                              tickets=state["tickets"], retrieved_at=_now(), native_artifacts=native,
                              conversion="colabfold-null-block-split-and-db-concatenation-v1" if provider == "colabfold_api" else "none",
                              live_qualification="not_asserted")
            manifest = dict(identity=identity, request_digest=digest, artifacts=artifacts, provenance=provenance)
            _immutable(entry / "manifest.json", _json(manifest))
            result = self._replay(entry, identity, digest)
            result["cache_hit"] = False
            return result

    def cancel(self, *, sequences, provider, settings, cache_root, credential_file=None):
        """Request Neurosnap cancellation once, then reconcile via prepare_msa.

        This returns cancel_requested, NOT confirmed cancellation. Lost responses
        remain cancel_ambiguous and are never blindly retried. ColabFold callers
        may stop local polling but must keep/reconcile its active ticket.
        """
        identity, digest = self._identity(sequences, provider, settings)
        if provider != "neurosnap_api":
            raise MSAAPIError("ColabFold remote cancellation is not documented; retain ticket")
        with self._authority(provider):
            entry = _directory(Path(cache_root) / provider / digest)
            state = _load(entry / "state.json")
            if state.get("identity") != identity:
                raise ReconciliationRequired("cancel request identity mismatch")
            ticket = state.get("tickets", {}).get("unpaired", {})
            remote_id = ticket.get("remote_id")
            if not isinstance(remote_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", remote_id):
                raise ReconciliationRequired("no durable remote ID to cancel")
            if ticket.get("phase") in ("complete", "terminal"):
                return {"status": ticket["phase"], "remote_id": remote_id}
            if ticket.get("cancel_state"):
                return {"status": ticket["cancel_state"], "remote_id": remote_id}
            headers = {"X-API-KEY": _credential(credential_file)}
            ticket["cancel_state"] = "cancel_ambiguous"
            _atomic(entry / "state.json", _json(state))
            self._http(provider, "/job/cancel/" + remote_id, headers, method="POST")
            ticket["cancel_state"] = "cancel_requested"
            _atomic(entry / "state.json", _json(state))
            return {"status": "cancel_requested", "remote_id": remote_id}


def prepare_msa(*, sequences: list[str], provider: str, settings: dict,
                cache_root: Path, credential_file: Path | None = None,
                cache_only: bool = False) -> dict:
    """Prepare validated cached native MSAs; see module docstring for settings."""
    return MSAClient().prepare_msa(sequences=sequences, provider=provider, settings=settings,
                                 cache_root=cache_root, credential_file=credential_file,
                                 cache_only=cache_only)
