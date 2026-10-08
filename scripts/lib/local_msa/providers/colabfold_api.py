from __future__ import annotations

import fcntl
import json
import os
import re
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..cache import (
    acquire_msa_lock,
    build_cache_profile,
    check_cache,
    compute_sequence_hash,
    get_msa_lock_path,
    load_from_cache,
    release_msa_lock,
    save_to_cache,
)
from ..config import (
    DEFAULT_CACHE_DIR,
    DEFAULT_COLABFOLD_API_HOST,
    DEFAULT_COLABFOLD_API_USER_AGENT,
    DEFAULT_DB_PATH,
    MSA_PRESETS,
)
from ..gpuserver import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    _atomic_write_json,
)

def sanitize_a3m_for_boltz(a3m_content: str) -> Tuple[str, int]:
    """
    Strip unexpected characters from A3M sequence lines.

    Boltz's parser can fail on digits/control symbols embedded in sequence rows.
    Keep only letters and gap '-' in sequence lines; preserve headers verbatim.
    """
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-")
    removed = 0
    cleaned_lines: List[str] = []

    for line in a3m_content.splitlines():
        if line.startswith(">") or line == "":
            cleaned_lines.append(line)
            continue
        cleaned = "".join(ch for ch in line if ch in allowed)
        removed += len(line) - len(cleaned)
        cleaned_lines.append(cleaned)

    sanitized = "\n".join(cleaned_lines)
    if a3m_content.endswith("\n"):
        sanitized += "\n"
    return sanitized, removed

def _http_post_form_json(
    url: str,
    form_data: Dict[str, Any],
    timeout_seconds: float = 20.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> Dict[str, Any]:
    """POST form data and parse JSON response."""
    payload = urllib.parse.urlencode(form_data).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} from ColabFold API POST {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ColabFold API POST {url} failed: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON from ColabFold API POST {url}: {raw[:200]!r}") from exc

def _http_get_json(
    url: str,
    timeout_seconds: float = 20.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> Dict[str, Any]:
    """GET JSON from URL."""
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} from ColabFold API GET {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ColabFold API GET {url} failed: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON from ColabFold API GET {url}: {raw[:200]!r}") from exc

def _http_download_file(
    url: str,
    dest_path: Path,
    timeout_seconds: float = 60.0,
    user_agent: str = DEFAULT_COLABFOLD_API_USER_AGENT,
) -> None:
    """Download URL to local file."""
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={"User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as fh:
                fh.write(response.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            body = str(exc)
        raise RuntimeError(f"HTTP {exc.code} while downloading {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {url}: {exc}") from exc

def _normalize_colabfold_host(host_url: str) -> str:
    """Normalize ColabFold API host URL."""
    normalized = (host_url or DEFAULT_COLABFOLD_API_HOST).strip()
    if not normalized:
        normalized = DEFAULT_COLABFOLD_API_HOST
    return normalized.rstrip("/")

def _resolve_colabfold_api_mode(use_env: bool, use_filter: bool) -> str:
    """
    ColabFold API mode mapping:
      use_env + use_filter      -> env
      !use_env + use_filter     -> all
      use_env + !use_filter     -> env-nofilter
      !use_env + !use_filter    -> nofilter
    """
    if use_filter:
        return "env" if use_env else "all"
    return "env-nofilter" if use_env else "nofilter"

def _wait_for_colabfold_submit_slot(cache_dir: str, min_interval_seconds: float) -> None:
    """
    Global pacing gate for remote submissions.

    This serializes submissions and enforces a minimum inter-submit delay so
    single-job users still behave politely against shared ColabFold servers.
    """
    interval = max(0.0, float(min_interval_seconds))
    if interval <= 0:
        return

    lock_dir = Path(cache_dir or DEFAULT_CACHE_DIR) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "colabfold_api.rate.lock"
    state_path = lock_dir / "colabfold_api.rate.json"

    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)

        last_submit = 0.0
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                last_submit = float(state.get("last_submit_unix", 0.0) or 0.0)
            except Exception:
                last_submit = 0.0

        now = time.time()
        wait_seconds = max(0.0, (last_submit + interval) - now)
        if wait_seconds > 0:
            print(
                f"ColabFold API pacing: waiting {wait_seconds:.1f}s before submit...",
                flush=True,
            )
            time.sleep(wait_seconds)

        _atomic_write_json(
            state_path,
            {
                "last_submit_unix": float(time.time()),
                "updated_at": datetime.utcnow().isoformat() + "Z",
            },
        )

def _read_a3m_entries(a3m_text: str) -> List[Tuple[str, str]]:
    """Parse A3M text into (header, sequence) entries."""
    entries: List[Tuple[str, str]] = []
    current_header: Optional[str] = None
    seq_lines: List[str] = []

    for raw_line in a3m_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if current_header is not None:
                entries.append((current_header, "".join(seq_lines)))
            current_header = line
            seq_lines = []
            continue
        if current_header is None:
            # Ignore malformed leading sequence lines.
            continue
        seq_lines.append(line)

    if current_header is not None:
        entries.append((current_header, "".join(seq_lines)))

    return entries

def _entries_to_a3m(entries: List[Tuple[str, str]]) -> str:
    """Serialize parsed A3M entries back to text."""
    lines: List[str] = []
    for header, seq in entries:
        lines.append(header)
        lines.append(seq)
    return "\n".join(lines) + ("\n" if lines else "")

def _merge_colabfold_a3m_contents(primary_text: str, extra_texts: List[str]) -> str:
    """Merge ColabFold A3M blocks while keeping query first and deduplicating by sequence."""
    merged_entries: List[Tuple[str, str]] = []
    seen_sequences: set[str] = set()

    primary_entries = _read_a3m_entries(primary_text)
    if not primary_entries:
        return ""

    # Always keep first entry from primary (query).
    query_header, query_seq = primary_entries[0]
    merged_entries.append((query_header, query_seq))
    seen_sequences.add(query_seq)

    for _, seq in primary_entries[1:]:
        if not seq or seq in seen_sequences:
            continue
        seen_sequences.add(seq)
        merged_entries.append((f">hit_{len(merged_entries)}", seq))

    for text in extra_texts:
        for idx, (header, seq) in enumerate(_read_a3m_entries(text)):
            if idx == 0:
                # Skip query entry from additional files.
                continue
            if not seq or seq in seen_sequences:
                continue
            seen_sequences.add(seq)
            merged_entries.append((header, seq))

    return _entries_to_a3m(merged_entries)

def _postfilter_a3m_by_taxonomy(a3m_content: str, taxon_list: Optional[str]) -> str:
    """Best-effort taxonomy post-filter for A3M text (keeps query sequence)."""
    if not taxon_list:
        return a3m_content

    target_taxids = {tok.strip() for tok in str(taxon_list).split(",") if tok.strip()}
    if not target_taxids:
        return a3m_content

    domain_map = {
        "2": "Bacteria",
        "2157": "Archaea",
        "2759": "Eukaryota",
        "10239": "Viruses",
    }
    filter_domains = {domain_map.get(tid) for tid in target_taxids if tid in domain_map}
    if not filter_domains:
        return a3m_content

    def _should_keep_entry(entry_lines: List[str]) -> bool:
        if not entry_lines:
            return False
        header = entry_lines[0]
        if header == ">query" or "query" in header.lower()[:20]:
            return True
        tax_match = re.search(r"Tax=([^T]+?)(?:TaxID=|$)", header)
        if not tax_match:
            return True
        tax_name = tax_match.group(1).strip().lower()
        is_bacteria = any(
            kw in tax_name
            for kw in (
                "bacteri",
                "escherichia",
                "salmonella",
                "streptococcus",
                "staphylococcus",
                "pseudomonas",
                "clostridium",
                "bacillus",
            )
        )
        if "Bacteria" in filter_domains:
            return is_bacteria
        return True

    filtered_entries: List[str] = []
    current_entry: List[str] = []

    for line in a3m_content.split("\n"):
        if line.startswith(">"):
            if current_entry and _should_keep_entry(current_entry):
                filtered_entries.append("\n".join(current_entry))
            current_entry = [line]
        else:
            current_entry.append(line)

    if current_entry and _should_keep_entry(current_entry):
        filtered_entries.append("\n".join(current_entry))

    return "\n".join(filtered_entries)

def _extract_tar_archive_safely(archive_path: Path, work_dir: Path) -> None:
    """Extract a tar.gz archive without allowing path traversal or special-file escapes."""
    root = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, mode="r:gz") as tar:
        safe_members = []
        for member in tar.getmembers():
            if not (member.isdir() or member.isreg()):
                raise RuntimeError(f"Refusing to extract special entry from ColabFold archive: {member.name}")
            if member.islnk() or member.issym():
                raise RuntimeError(f"Refusing to extract link entry from ColabFold archive: {member.name}")
            member_path = (work_dir / member.name).resolve()
            if member_path != root and not member_path.is_relative_to(root):
                raise RuntimeError(f"Refusing to extract path outside work dir from ColabFold archive: {member.name}")
            safe_members.append(member)
        tar.extractall(path=work_dir, members=safe_members)


def _run_colabfold_api_search(
    sequence: str,
    work_dir: Path,
    host_url: str,
    cache_dir: str,
    use_env: bool,
    use_filter: bool,
    min_submit_interval_seconds: float,
    poll_interval_seconds: float,
    submit_timeout_seconds: float = 20.0,
    status_timeout_seconds: float = 20.0,
    download_timeout_seconds: float = 90.0,
    max_submit_attempts: int = 12,
) -> Dict[str, Any]:
    """
    Query ColabFold public API for a single sequence MSA.

    Returns:
      {
        "ticket_id": str,
        "api_mode": str,
        "status": str,
        "a3m_content": str,
      }
    """
    host = _normalize_colabfold_host(host_url)
    api_mode = _resolve_colabfold_api_mode(use_env=use_env, use_filter=use_filter)
    query = f">101\n{sequence}\n"

    ticket_id: Optional[str] = None
    submit_status = "UNKNOWN"
    for attempt in range(max(1, int(max_submit_attempts))):
        _wait_for_colabfold_submit_slot(
            cache_dir=cache_dir,
            min_interval_seconds=min_submit_interval_seconds,
        )
        submit_payload = _http_post_form_json(
            url=f"{host}/ticket/msa",
            form_data={"q": query, "mode": api_mode},
            timeout_seconds=submit_timeout_seconds,
        )
        submit_status = str(submit_payload.get("status", "")).strip().upper()
        candidate_id = submit_payload.get("id")
        if isinstance(candidate_id, str) and candidate_id.strip():
            ticket_id = candidate_id.strip()

        if submit_status in {"RATELIMIT", "UNKNOWN"}:
            backoff = min(60.0, 5.0 + (attempt * 2.0))
            print(
                f"ColabFold API submit status={submit_status}; retrying in {backoff:.1f}s...",
                flush=True,
            )
            time.sleep(backoff)
            continue
        if submit_status == "MAINTENANCE":
            raise RuntimeError("ColabFold API is in MAINTENANCE mode")
        if submit_status in {"ERROR", "FAILED"}:
            raise RuntimeError(f"ColabFold API submit failed: {submit_payload}")
        if ticket_id:
            break

    if not ticket_id:
        raise RuntimeError(f"ColabFold API submit did not return a ticket id (last status={submit_status})")

    print(f"ColabFold API ticket: {ticket_id} (mode={api_mode})", flush=True)

    final_status = submit_status if submit_status else "UNKNOWN"
    while True:
        status_payload = _http_get_json(
            url=f"{host}/ticket/{ticket_id}",
            timeout_seconds=status_timeout_seconds,
        )
        final_status = str(status_payload.get("status", "")).strip().upper()

        if final_status == "COMPLETE":
            break
        if final_status in {"PENDING", "RUNNING", "UNKNOWN"}:
            sleep_for = max(1.0, float(poll_interval_seconds))
            print(
                f"ColabFold API ticket {ticket_id} status={final_status}; polling again in {sleep_for:.1f}s...",
                flush=True,
            )
            time.sleep(sleep_for)
            continue
        if final_status == "RATELIMIT":
            sleep_for = max(5.0, float(poll_interval_seconds) * 2.0)
            print(
                f"ColabFold API ticket {ticket_id} status=RATELIMIT; waiting {sleep_for:.1f}s...",
                flush=True,
            )
            time.sleep(sleep_for)
            continue
        if final_status == "MAINTENANCE":
            raise RuntimeError("ColabFold API entered MAINTENANCE mode during polling")
        raise RuntimeError(f"ColabFold API ticket {ticket_id} failed with status={final_status}")

    archive_path = work_dir / f"{ticket_id}.tar.gz"
    _http_download_file(
        url=f"{host}/result/download/{ticket_id}",
        dest_path=archive_path,
        timeout_seconds=download_timeout_seconds,
    )

    _extract_tar_archive_safely(archive_path=archive_path, work_dir=work_dir)

    a3m_paths = sorted(work_dir.rglob("*.a3m"))
    if not a3m_paths:
        raise RuntimeError(f"ColabFold API ticket {ticket_id} produced no .a3m files")

    primary_candidates = [p for p in a3m_paths if "uniref" in p.name.lower()]
    primary_path = primary_candidates[0] if primary_candidates else a3m_paths[0]
    primary_text = primary_path.read_text(encoding="utf-8", errors="ignore")

    extra_texts: List[str] = []
    if use_env:
        env_candidates = [
            p for p in a3m_paths
            if p != primary_path and any(
                token in p.name.lower() for token in ("bfd", "mgnify", "metaeuk", "env")
            )
        ]
        if not env_candidates:
            env_candidates = [p for p in a3m_paths if p != primary_path]
        for env_path in env_candidates:
            extra_texts.append(env_path.read_text(encoding="utf-8", errors="ignore"))

    merged_content = _merge_colabfold_a3m_contents(primary_text, extra_texts)
    if not merged_content.strip():
        merged_content = primary_text

    return {
        "ticket_id": ticket_id,
        "api_mode": api_mode,
        "status": final_status,
        "a3m_content": merged_content,
    }

def run_colabfold_api_msa_workflow(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = DEFAULT_DB_PATH,
    cache_dir: str = None,
    max_age_days: int = 0,
    force_refresh: bool = False,
    cache_only: bool = False,
    num_threads: int = 32,
    use_gpu: bool = None,
    gpu_id: int = None,
    cpu_only: bool = False,
    gpu_mode: str = "auto",
    gpu_threshold: int = 80,
    preferred_gpus: Optional[List[int]] = None,
    excluded_gpus: Optional[List[int]] = None,
    gpu_server_mode: str = "persistent",
    gpu_server_wait_timeout: int = DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    gpu_server_db_load_mode: int = DEFAULT_GPUSERVER_DB_LOAD_MODE,
    gpu_server_startup_wait: float = DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    reference_sequence: str = None,
    preset: str = "balanced",
    num_iterations: int = None,
    use_env: bool = None,
    use_expand: bool = None,
    use_filter: bool = None,
    evalue: float = None,
    sensitivity: float = None,
    max_seqs: int = None,
    min_seq_id: float = None,
    min_coverage: float = None,
    taxon_list: str = None,
    min_depth_warning: int = 100,
    min_depth_fail: int = 0,
    fast_env_fallback_min_depth: int = 25,
    colabfold_api_host: str = DEFAULT_COLABFOLD_API_HOST,
    colabfold_api_min_interval: float = 6.0,
    colabfold_api_poll_interval: float = 6.0,
):
    """
    Generate MSA via remote ColabFold API (single-query mode).

    This intentionally keeps the same cache + report behavior as local mode so
    downstream workflow logic remains unchanged.
    """
    _ = db_path
    _ = num_threads
    _ = use_gpu
    _ = gpu_id
    _ = cpu_only
    _ = gpu_mode
    _ = gpu_threshold
    _ = preferred_gpus
    _ = excluded_gpus
    _ = gpu_server_mode
    _ = gpu_server_wait_timeout
    _ = gpu_server_db_load_mode
    _ = gpu_server_startup_wait
    _ = fast_env_fallback_min_depth

    if force_refresh and cache_only:
        raise ValueError("Invalid flags: --force_refresh cannot be combined with --cache-only")

    if preset not in MSA_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Options: {list(MSA_PRESETS.keys())}")

    config = MSA_PRESETS[preset].copy()
    print(f"MSA Preset: {preset} - {config['description']}", flush=True)
    print(f"MSA Provider: colabfold_api ({_normalize_colabfold_host(colabfold_api_host)})", flush=True)

    if num_iterations is not None:
        config["num_iterations"] = num_iterations
    if use_env is not None:
        config["use_env"] = bool(use_env)
    if use_expand is not None:
        config["use_expand"] = bool(use_expand)
    if use_filter is not None:
        config["use_filter"] = bool(use_filter)
    if evalue is not None:
        config["evalue"] = evalue
    if sensitivity is not None:
        config["sensitivity"] = sensitivity
    if max_seqs is not None:
        config["max_seqs"] = max(1, int(max_seqs))

    if config.get("use_expand"):
        print(
            "WARNING: ColabFold API mode ignores local alignment expansion controls.",
            flush=True,
        )

    cache_profile = build_cache_profile(
        preset=preset,
        config=config,
        min_seq_id=min_seq_id,
        min_coverage=min_coverage,
        taxon_list=taxon_list,
    )

    cache_key_seq = reference_sequence or sequence
    seq_hash = compute_sequence_hash(cache_key_seq)
    cache_dir = cache_dir or DEFAULT_CACHE_DIR

    lock_fd = None
    lock_path = get_msa_lock_path(cache_dir, seq_hash)
    print(f"Acquiring MSA lock for {seq_hash[:16]}...", flush=True)
    lock_fd = acquire_msa_lock(lock_path)
    print("MSA lock acquired", flush=True)

    try:
        cache_found = False
        if not force_refresh:
            cached = check_cache(
                cache_dir=cache_dir,
                seq_hash=seq_hash,
                max_age_days=max_age_days,
                preset=preset,
                cache_profile=cache_profile,
            )
            if cached:
                cache_found = True
                cached_path, cached_profile = cached
                print(f"CACHE HIT: {seq_hash[:16]}... ({cached_profile})", flush=True)
                final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
                os.makedirs(out_dir, exist_ok=True)
                load_from_cache(cached_path, final_a3m)

                with open(final_a3m, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                content, removed_invalid_chars = sanitize_a3m_for_boltz(content)
                if removed_invalid_chars > 0:
                    with open(final_a3m, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(
                        f"Sanitized cached A3M: removed {removed_invalid_chars} invalid character(s).",
                        flush=True,
                    )
                msa_depth = content.count("\n>") + (1 if content.startswith(">") else 0)

                report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "msa_depth": msa_depth,
                            "query_length": len(sequence),
                            "preset": preset,
                            "cache_profile": cache_profile,
                            "cached_profile": cached_profile,
                            "cached_preset": cached_profile.split("_", 1)[0],
                            "provider": "colabfold_api",
                            "api_host": _normalize_colabfold_host(colabfold_api_host),
                            "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                            "selected_gpu_id": None,
                            "used_gpu_mmseqs": False,
                            "from_cache": True,
                        },
                        f,
                        indent=2,
                    )
                print(f"MSA quality report: {report_path}", flush=True)
                print(f"MSA generated: {final_a3m}", flush=True)
                return

        if cache_only and not cache_found:
            raise RuntimeError(
                "CACHE ONLY MODE: No cached MSA found for sequence hash "
                f"{seq_hash[:16]}...; disable --cache-only or run once without it."
            )

        print(f"CACHE MISS: {seq_hash[:16]}... ({cache_profile}; running ColabFold API)", flush=True)
        with tempfile.TemporaryDirectory() as tmp_dir:
            query_result = _run_colabfold_api_search(
                sequence=sequence,
                work_dir=Path(tmp_dir),
                host_url=colabfold_api_host,
                cache_dir=cache_dir,
                use_env=bool(config["use_env"]),
                use_filter=bool(config["use_filter"]),
                min_submit_interval_seconds=colabfold_api_min_interval,
                poll_interval_seconds=colabfold_api_poll_interval,
            )

            a3m_content = str(query_result["a3m_content"])
            a3m_content, removed_invalid_chars = sanitize_a3m_for_boltz(a3m_content)
            if removed_invalid_chars > 0:
                print(
                    f"Sanitized A3M: removed {removed_invalid_chars} invalid character(s).",
                    flush=True,
                )

            if taxon_list:
                before_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
                a3m_content = _postfilter_a3m_by_taxonomy(a3m_content, taxon_list)
                after_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
                if before_depth != after_depth:
                    print(
                        f"Taxonomy filter: {before_depth} -> {after_depth} sequences",
                        flush=True,
                    )

            msa_depth = a3m_content.count("\n>") + (1 if a3m_content.startswith(">") else 0)
            print(f"Final MSA depth: {msa_depth} sequences", flush=True)

            if min_depth_fail > 0 and msa_depth < min_depth_fail:
                error_msg = (
                    f"MSA FAILED: Only {msa_depth} sequences found (minimum: {min_depth_fail}). "
                    "Consider: 1) Different preset, 2) Relaxing filters, 3) Checking sequence."
                )
                print(f"ERROR: {error_msg}", flush=True)
                raise RuntimeError(error_msg)

            if msa_depth < min_depth_warning:
                print(
                    f"WARNING: MSA has only {msa_depth} sequences (recommended >{min_depth_warning}). "
                    f"Structure prediction confidence may be low.",
                    flush=True,
                )

            quality_report = {
                "msa_depth": msa_depth,
                "query_length": len(sequence),
                "preset": preset,
                "cache_profile": cache_profile,
                "provider": "colabfold_api",
                "api_host": _normalize_colabfold_host(colabfold_api_host),
                "api_ticket_id": query_result.get("ticket_id"),
                "api_mode": query_result.get("api_mode"),
                "api_status": query_result.get("status"),
                "num_iterations": config["num_iterations"],
                "use_env_requested": bool(config["use_env"]),
                "use_env_effective": bool(config["use_env"]),
                "auto_env_fallback_triggered": False,
                "fast_env_fallback_min_depth": 0,
                "uniref_only_depth": None,
                "use_expand": config["use_expand"],
                "use_filter": config["use_filter"],
                "evalue": config["evalue"],
                "sensitivity": config["sensitivity"],
                "taxon_filter": taxon_list,
                "sanitized_invalid_chars_removed": int(removed_invalid_chars),
                "selected_gpu_id": None,
                "used_gpu_mmseqs": False,
                "from_cache": False,
            }

            final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
            os.makedirs(out_dir, exist_ok=True)
            with open(final_a3m, "w", encoding="utf-8") as f:
                f.write(a3m_content)

            report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(quality_report, f, indent=2)
            print(f"MSA quality report: {report_path}", flush=True)
            print(f"MSA generated: {final_a3m}", flush=True)

            if cache_dir:
                save_to_cache(cache_dir, seq_hash, a3m_content, cache_profile)

    finally:
        if lock_fd is not None:
            release_msa_lock(lock_fd)
            print("MSA lock released", flush=True)


def register_legacy_run_colabfold_api_msa_workflow(fn):
    global run_colabfold_api_msa_workflow
    run_colabfold_api_msa_workflow = fn
    return fn


__all__ = [
    "register_legacy_run_colabfold_api_msa_workflow",
    "run_colabfold_api_msa_workflow",
    "sanitize_a3m_for_boltz",
]
