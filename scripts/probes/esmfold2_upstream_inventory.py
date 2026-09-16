#!/usr/bin/env python3
"""Bounded, anonymous upstream identity probe; NOT acquisition/approval/admission.

Print candidate file identities for review. Never downloads weights, reads host
caches, installs assets, follows redirects, or generates registry approval_ref.
The revisions were discovered from existing local HF refs and then independently
resolved at upstream; that discovery is NOT evidence of BMS qualification.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request

PINS = {
    "biohub/ESMFold2-Fast": "0438ea0d932a314950665e0b4d0af4322ae88250",
    "biohub/ESMFold2": "e1e189d0f5fb70c2693da2332eca4443c0ccccd6",
    "biohub/ESMC-6B": "89c554c46a44d825fbfbe3ce2a6bdc539770bdaa",
}
LIMIT = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_small(url):
    # All callers construct fixed official HTTPS authorities, not user URLs.
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(url, timeout=20) as response:
        body = response.read(LIMIT + 1)
    if len(body) > LIMIT:
        raise ValueError("metadata exceeds probe budget")
    return body


def inventory(repo, revision, metadata, fetch=read_small):
    if metadata.get("sha") != revision or metadata.get("id") != repo:
        raise ValueError("upstream repository/revision mismatch")
    files = []
    bodies = {}
    seen = set()
    for entry in metadata["siblings"]:
        name = entry["rfilename"]
        # Only inference members; documentation and images are not downloaded.
        if not (name.endswith(".json") or name.endswith(".safetensors")):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in seen:
            raise ValueError("unsafe or duplicate member")
        seen.add(name)
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{name}"
        if "lfs" in entry:
            digest = entry["lfs"]["sha256"]
            size = entry["lfs"]["size"]
            basis = "upstream_lfs_metadata_only_not_download_verified"
        else:
            if name.endswith(".safetensors") or entry["size"] > LIMIT:
                raise ValueError("refuse large/non-LFS body")
            body = fetch(f"https://huggingface.co/{repo}/raw/{revision}/{name}")
            git_blob = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
            if git_blob != entry["blobId"] or len(body) != entry["size"]:
                raise ValueError("metadata bytes disagree with upstream Git blob")
            digest, size = hashlib.sha256(body).hexdigest(), len(body)
            bodies[name] = json.loads(body)
            basis = "downloaded_small_file_sha256_and_git_blob_verified"
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or type(size) is not int or size <= 0 or size != entry["size"]:
            raise ValueError("invalid upstream byte identity")
        files.append(dict(path=name, sha256=digest, size_bytes=size,
                          candidate_resolve_url=url, verification=basis))
    if "config.json" not in seen:
        raise ValueError("missing model configuration")
    if repo.endswith("ESMC-6B"):
        index = bodies.get("model.safetensors.index.json", {})
        shards = set(index.get("weight_map", {}).values())
        actual = {name for name in seen if name.endswith(".safetensors")}
        if not shards or shards != actual:
            raise ValueError("index/shard closure mismatch")
    elif bodies["config.json"].get("esmc_id") != "biohub/ESMC-6B":
        raise ValueError("unreviewed language-model dependency")
    return dict(repository=repo, revision=revision, license_card=metadata.get("cardData", {}),
                files=sorted(files, key=lambda item: item["path"]),
                size_bytes=sum(item["size_bytes"] for item in files))


def main():
    results = []
    for repo, revision in PINS.items():
        metadata = json.loads(read_small(
            f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true"))
        results.append(inventory(repo, revision, metadata))
    print(json.dumps(dict(status="upstream_identity_only", qualification="not_checked",
                          acquisition_approved=False, repositories=results,
                          file_count=sum(len(item["files"]) for item in results),
                          size_bytes=sum(item["size_bytes"] for item in results)), indent=2))


if __name__ == "__main__":
    main()
