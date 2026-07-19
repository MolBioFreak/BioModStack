from __future__ import annotations

from typing import BinaryIO, Iterator, NoReturn
import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from mobile_apk_auth import require_mobile_apk_tailscale_identity
from paths import get_mobile_apk_updates_dir
from services.mobile_apk_updates import (
    MobileApkRelease,
    MobileApkReleaseIntegrityError,
    MobileApkReleaseNotPublishedError,
    MobileApkUpdateService,
)


router = APIRouter(
    prefix='/mobile-apk/channels',
    tags=['mobile-apk-updates'],
    dependencies=[Depends(require_mobile_apk_tailscale_identity)],
)
RANGE_PATTERN = re.compile(r'^bytes=(\d*)-(\d*)$')


def _service(request: Request) -> MobileApkUpdateService:
    return MobileApkUpdateService(get_mobile_apk_updates_dir())


def _translate_release_error(error: Exception) -> NoReturn:
    if isinstance(error, MobileApkReleaseNotPublishedError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='APK update channel is not published',
        ) from error
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail='APK update metadata failed integrity validation',
    ) from error


def _load_release(request: Request, channel: str) -> MobileApkRelease:
    try:
        return _service(request).load_release(channel)
    except (MobileApkReleaseNotPublishedError, MobileApkReleaseIntegrityError) as error:
        _translate_release_error(error)


def _open_release(request: Request, channel: str) -> tuple[MobileApkRelease, BinaryIO]:
    try:
        return _service(request).open_release(channel)
    except (MobileApkReleaseNotPublishedError, MobileApkReleaseIntegrityError) as error:
        _translate_release_error(error)


def _parse_range(range_header: str | None, size: int) -> tuple[int, int, bool]:
    if range_header is None:
        return 0, size - 1, False
    match = RANGE_PATTERN.fullmatch(range_header.strip())
    if match is None or (not match.group(1) and not match.group(2)):
        raise ValueError('invalid range')
    start_text, end_text = match.groups()
    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError('invalid suffix range')
        start = max(0, size - suffix_length)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise ValueError('unsatisfiable range')
        end = min(end, size - 1)
    return start, end, True


def _stream_open_file(apk_file: BinaryIO, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    try:
        apk_file.seek(start)
        while remaining > 0:
            chunk = apk_file.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        apk_file.close()


@router.get('/{channel}/manifest')
def get_mobile_apk_manifest(
    channel: str,
    request: Request,
) -> JSONResponse:
    release = _load_release(request, channel)
    return JSONResponse(
        release.response_payload(),
        headers={
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@router.get('/{channel}/files/{filename}')
def download_mobile_apk(
    channel: str,
    filename: str,
    request: Request,
) -> StreamingResponse:
    service = _service(request)
    try:
        release = service.release_metadata(channel)
    except (MobileApkReleaseNotPublishedError, MobileApkReleaseIntegrityError) as error:
        _translate_release_error(error)
    if filename != release.filename:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='APK release file not found')

    try:
        start, end, partial = _parse_range(request.headers.get('range'), release.size_bytes)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail='Requested APK byte range is not satisfiable',
            headers={'Content-Range': f'bytes */{release.size_bytes}'},
        ) from error

    try:
        release, apk_file = service.open_verified_release(release)
    except (MobileApkReleaseNotPublishedError, MobileApkReleaseIntegrityError) as error:
        _translate_release_error(error)

    content_length = end - start + 1
    headers = {
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'private, no-store',
        'Content-Length': str(content_length),
        'X-Content-Type-Options': 'nosniff',
    }
    if partial:
        headers['Content-Range'] = f'bytes {start}-{end}/{release.size_bytes}'

    return StreamingResponse(
        _stream_open_file(apk_file, start, end),
        status_code=status.HTTP_206_PARTIAL_CONTENT if partial else status.HTTP_200_OK,
        media_type='application/vnd.android.package-archive',
        headers=headers,
    )
