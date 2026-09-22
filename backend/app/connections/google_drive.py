"""Drive API calls made by Athena itself.

Distinct from `hermes_files`, which hands Hermes its own copy of the
credentials so it can reach Drive independently. Both sides hold the same
refresh token and refresh on their own schedule; Google allows that, and the
alternative -- Athena asking Hermes to fetch files through a prompt -- would put
file contents into `sessions.payload` for every later turn to replay.

Only the read half of Drive is here: list and fetch. Nothing in Athena writes to
a user's Drive, and the granted scope (`drive.readonly`) could not do so anyway.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.connections import crypto
from app.connections import repository as repo
from app.db import connection

log = logging.getLogger(__name__)

FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"

# Fields worth asking for. Drive returns a trimmed object by default, and
# `size` is absent entirely for native Docs -- see `DriveFile.size` below.
LIST_FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,size,webViewLink,iconLink)"
)

# Refresh this far before the recorded expiry. A token that expires mid-ingest
# fails a download that has already cost a Drive round-trip, so spend the
# refresh early rather than racing the clock.
EXPIRY_SKEW = timedelta(minutes=5)


class DriveError(RuntimeError):
    """A Drive call failed. The message reaches the user, so it explains."""


class DriveNotConnected(RuntimeError):
    """Google is not connected, or Drive reading is switched off.

    Separate from DriveError because the fix is different: this one is the
    user's to make in Settings, and the router maps it to 409 rather than 502.
    """


def _expired(expires_at: str | None) -> bool:
    """Treat unknown as expired: refreshing needlessly costs one request,
    while using a dead token costs a failed import the user has to redo."""
    if not expires_at:
        return True
    try:
        deadline = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return True
    return datetime.now(timezone.utc) + EXPIRY_SKEW >= deadline


async def _refresh(client: dict[str, Any], refresh_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                client.get("token_uri") or "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
    except httpx.HTTPError as exc:
        raise DriveError(f"could not reach Google to refresh access: {exc}") from exc

    if resp.status_code != 200:
        # invalid_grant means the user revoked the grant or changed their
        # password. Reconnecting is the only fix, so say that rather than
        # leaving them staring at an OAuth error code.
        body = resp.text[:300]
        if "invalid_grant" in body:
            raise DriveNotConnected(
                "Google has revoked Αθηνα's access, so Drive cannot be read. "
                "Reconnect Google in Settings."
            )
        raise DriveError(f"Google refused to refresh access ({resp.status_code}): {body}")

    payload = resp.json()
    if not payload.get("access_token"):
        raise DriveError("Google's refresh response contained no access token")
    return payload


async def access_token() -> str:
    """A usable access token, refreshing and persisting it if the stored one
    has expired.

    Refreshing here does not disturb Hermes: Google keeps a refresh token valid
    across uses, and Hermes holds its own copy in `google_token.json`.
    """
    with connection() as conn:
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)
        if row is None or row["status"] == "disconnected":
            raise DriveNotConnected(
                "Google is not connected. Connect it in Settings to read from Drive."
            )
        if row["status"] == "authorizing":
            raise DriveNotConnected(
                "the Google connection was never finished -- paste the redirect "
                "URL in Settings to complete it."
            )
        if not row.get("capabilities", {}).get("drive.read"):
            raise DriveNotConnected(
                "reading Drive is switched off for this connection. Turn on "
                "Drive access in Settings."
            )
        try:
            stored = repo.get_credentials(conn, repo.GOOGLE_SLUG)
        except crypto.SecretUnreadable as exc:
            raise DriveNotConnected(
                "the stored Google credential cannot be decrypted -- "
                "CONNECTIONS_SECRET_KEY has changed. Reconnect Google in Settings."
            ) from exc
        expires_at = row.get("expires_at")

    if not stored:
        raise DriveNotConnected(
            "the Google credential is missing. Reconnect Google in Settings."
        )

    token = stored.get("token") or {}
    client = {k: v for k, v in stored.items() if k != "token"}

    if not _expired(expires_at) and token.get("access_token"):
        return token["access_token"]

    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise DriveNotConnected(
            "the Google connection has no refresh token, so access cannot be "
            "renewed. Reconnect Google in Settings."
        )

    fresh = await _refresh(client, refresh_token)
    # Google omits refresh_token from a refresh response; keeping the original
    # is what makes the next refresh possible.
    merged = {**token, **fresh, "refresh_token": fresh.get("refresh_token", refresh_token)}
    new_expiry = (
        datetime.now(timezone.utc) + timedelta(seconds=int(fresh.get("expires_in", 3600)))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with connection() as conn:
        repo.update_token(
            conn,
            slug=repo.GOOGLE_SLUG,
            secret={**client, "token": merged},
            expires_at=new_expiry,
        )

    return merged["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _drive_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return f"{resp.status_code} {resp.text[:300]}"
    message = (payload.get("error") or {}).get("message")
    return message or f"{resp.status_code} {resp.text[:300]}"


async def list_files(
    token: str, *, query: str, page_token: str | None = None, page_size: int = 50
) -> dict[str, Any]:
    """One page of Drive's file list.

    `includeItemsFromAllDrives` plus `supportsAllDrives` so files shared into a
    shared drive appear; university material is routinely shared that way, and
    without these they are silently missing rather than visibly denied.
    """
    params = {
        "q": query,
        "fields": LIST_FIELDS,
        "pageSize": page_size,
        "orderBy": "folder,modifiedTime desc",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    if page_token:
        params["pageToken"] = page_token

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(FILES_ENDPOINT, headers=_auth(token), params=params)
    except httpx.HTTPError as exc:
        raise DriveError(f"could not reach Google Drive: {exc}") from exc

    if resp.status_code == 403:
        raise DriveError(
            f"Google Drive refused the request: {_drive_error(resp)}. If this "
            "mentions the Drive API not being enabled, enable it in the Cloud "
            "Console project the client secret came from."
        )
    if resp.status_code != 200:
        raise DriveError(f"Google Drive returned an error: {_drive_error(resp)}")

    return resp.json()


async def get_file(token: str, file_id: str) -> dict[str, Any]:
    """Metadata for one file.

    Import takes ids and looks the rest up here rather than trusting what the
    browser posted: filename and mime type decide how the bytes are extracted,
    and a client-supplied mime could route a PDF through the text extractor.
    """
    params = {
        "fields": "id,name,mimeType,modifiedTime,size,webViewLink,iconLink",
        "supportsAllDrives": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(
                f"{FILES_ENDPOINT}/{file_id}", headers=_auth(token), params=params
            )
    except httpx.HTTPError as exc:
        raise DriveError(f"could not reach Google Drive: {exc}") from exc

    if resp.status_code == 404:
        raise DriveError(
            "that file is no longer in Drive, or this account cannot see it"
        )
    if resp.status_code != 200:
        raise DriveError(f"Google Drive returned an error: {_drive_error(resp)}")

    return resp.json()


async def download(token: str, file_id: str, *, export_mime: str | None, limit: int) -> bytes:
    """Fetch one file's bytes.

    Native Google formats (Docs, Sheets, Slides) have no bytes of their own and
    must go through /export with a target format; everything else is fetched
    with alt=media. `limit` is enforced against the streamed body rather than
    the reported size, because /export reports no size at all.
    """
    if export_mime:
        url = f"{FILES_ENDPOINT}/{file_id}/export"
        params = {"mimeType": export_mime, "supportsAllDrives": "true"}
    else:
        url = f"{FILES_ENDPOINT}/{file_id}"
        params = {"alt": "media", "supportsAllDrives": "true"}

    chunks: list[bytes] = []
    total = 0
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as http:
            async with http.stream("GET", url, headers=_auth(token), params=params) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    raise DriveError(
                        f"Google Drive would not return that file: {_drive_error(resp)}"
                    )
                async for part in resp.aiter_bytes():
                    total += len(part)
                    if total > limit:
                        # Stop paying for bytes we are about to reject.
                        raise DriveError(
                            f"this file is larger than the {limit // (1024 * 1024)}MB limit"
                        )
                    chunks.append(part)
    except httpx.HTTPError as exc:
        raise DriveError(f"could not download from Google Drive: {exc}") from exc

    data = b"".join(chunks)
    if not data:
        raise DriveError("Google Drive returned an empty file")
    return data
