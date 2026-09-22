"""HTTP surface for external connections.

The Google flow is three requests rather than the usual one-shot OAuth
redirect, because Hermes needs a *Desktop-app* client and Google only lets those
redirect to loopback -- which is the user's own machine, not this server. So:

  1. POST /connections/google/client    upload the client secret, get a consent URL
  2. (the user grants in their browser and lands on a dead loopback page)
  3. POST /connections/google/exchange  paste that URL back; we finish the grant

Both steps write a file to the Hermes host. Neither sends anything through a
Hermes prompt -- see app/connections/hermes_files.py for why.

The write surface is exactly these plus set-capability and disconnect. There is
no generic PATCH taking arbitrary columns, deliberately
(plans/athena-connections-plan_v.0.2.md §5.3).
"""

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.config import get_settings
from app.connections import crypto, google_oauth, hermes_files
from app.connections import repository as repo
from app.connections.schemas import (
    AuthorizeStarted,
    CapabilityUpdate,
    ConnectionOut,
    ConnectionsPage,
    DisconnectResult,
    ExchangeRequest,
)
from app.db import connection

router = APIRouter(prefix="/connections", tags=["connections"])

# Client secret files are a couple of KB. A megabyte is already absurd, and the
# 10MB materials ceiling would let someone stream junk through the JSON parser.
MAX_CLIENT_JSON_BYTES = 64 * 1024

GOOGLE_DISPLAY_NAME = "Google"
# Drive read is the only thing Athena does with the grant today.
GOOGLE_CAPABILITY = "drive.read"


def _require_secret_key() -> None:
    if not crypto.available():
        # 409, not 500: the server is working correctly and refusing on purpose.
        raise HTTPException(
            409,
            "credential storage is not configured, so connecting is disabled. "
            "Set CONNECTIONS_SECRET_KEY and restart the backend.",
        )


def _require_destination() -> None:
    if not hermes_files.configured():
        raise HTTPException(
            409,
            "there is nowhere to send the credentials: neither HERMES_CONFIG_PATH "
            "nor HERMES_FILES_URL is set, so Hermes would never see them.",
        )


@router.get("", response_model=ConnectionsPage)
def list_connections() -> ConnectionsPage:
    """The single source for the Settings connector section."""
    with connection() as conn:
        items = repo.list_all(conn)
    return ConnectionsPage(
        items=[ConnectionOut(**c) for c in items],
        secrets_ready=crypto.available(),
        hermes_destination=hermes_files.describe(),
    )


@router.post("/google/client", response_model=AuthorizeStarted, status_code=202)
async def upload_google_client(
    file: UploadFile = File(...),
) -> AuthorizeStarted:
    """Step 1: store the client secret and hand back the consent URL.

    202 rather than 201: nothing is connected yet. The grant only exists once
    the user comes back with a code.
    """
    _require_secret_key()
    _require_destination()

    data = await file.read()
    if not data:
        raise HTTPException(400, "the uploaded file is empty")
    if len(data) > MAX_CLIENT_JSON_BYTES:
        raise HTTPException(
            413,
            f"a client secret file is only a few KB; this one is "
            f"{len(data) // 1024}KB. Check you uploaded the right file.",
        )

    try:
        client = google_oauth.parse_client_json(data)
    except google_oauth.ClientJsonInvalid as exc:
        raise HTTPException(400, str(exc)) from exc

    with connection() as conn:
        row = repo.upsert_authorizing(
            conn,
            slug=repo.GOOGLE_SLUG,
            provider="google",
            display_name=GOOGLE_DISPLAY_NAME,
            secret=client,
        )

    # Written now rather than at exchange time: Hermes needs the client file
    # regardless, and a failure here is worth surfacing before we send the user
    # off to a consent screen that would be wasted effort.
    try:
        await hermes_files.put_file(hermes_files.GOOGLE_CLIENT_FILE, {"installed": client})
    except hermes_files.HermesFileError as exc:
        with connection() as conn:
            repo.mark_error(conn, repo.GOOGLE_SLUG, str(exc))
            row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)
        raise HTTPException(502, f"could not reach the Hermes host: {exc}") from exc

    with connection() as conn:
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)

    settings = get_settings()
    return AuthorizeStarted(
        authorize_url=google_oauth.authorize_url(client),
        redirect_uri=settings.google_redirect_uri,
        connection=ConnectionOut(**row),  # type: ignore[arg-type]
    )


@router.post("/google/exchange", response_model=ConnectionOut)
async def exchange_google_code(body: ExchangeRequest) -> ConnectionOut:
    """Step 3: turn the pasted redirect URL into a token file on the Hermes host."""
    _require_secret_key()
    _require_destination()

    with connection() as conn:
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)
        if row is None or row["status"] == "disconnected":
            raise HTTPException(
                409, "upload the Google client secret file first -- there is no flow to finish."
            )
        client = repo.get_credentials(conn, repo.GOOGLE_SLUG)

    if client is None:
        raise HTTPException(
            409, "the stored client secret is gone. Upload the client secret file again."
        )

    try:
        code = google_oauth.extract_code(body.pasted)
    except google_oauth.ClientJsonInvalid as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        token = await google_oauth.exchange_code(client, code)
    except google_oauth.GoogleOAuthError as exc:
        # Left in 'authorizing', not 'error': a mistyped paste is the common
        # case and the user should just be able to try again.
        raise HTTPException(400, str(exc)) from exc

    # Both files, so Hermes can refresh on its own from here.
    try:
        await hermes_files.put_file(
            hermes_files.GOOGLE_CLIENT_FILE, {"installed": client}
        )
        await hermes_files.put_file(
            hermes_files.GOOGLE_TOKEN_FILE, google_oauth.token_file(client, token)
        )
    except hermes_files.HermesFileError as exc:
        with connection() as conn:
            repo.mark_error(conn, repo.GOOGLE_SLUG, str(exc))
        raise HTTPException(502, f"could not reach the Hermes host: {exc}") from exc

    email = await google_oauth.fetch_account_email(token["access_token"])
    scopes = google_oauth.granted_scopes(token)

    with connection() as conn:
        row = repo.mark_connected(
            conn,
            slug=repo.GOOGLE_SLUG,
            # The client secret is kept alongside the tokens: a re-push to
            # Hermes, or a later refresh, needs both.
            secret={**client, "token": token},
            account_label=email,
            scopes=scopes,
            expires_at=google_oauth.expires_at_iso(token),
        )
        # Only granted if Google actually gave us a Drive scope. Granted is not
        # permitted, and a capability whose scope was never granted must not
        # exist at all.
        if any("drive" in scope for scope in scopes):
            repo.grant_capability(conn, row["id"], GOOGLE_CAPABILITY)
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)

    return ConnectionOut(**row)  # type: ignore[arg-type]


@router.put("/{slug}/capabilities/{capability}", response_model=ConnectionOut)
def set_capability(slug: str, capability: str, body: CapabilityUpdate) -> ConnectionOut:
    if capability not in repo.CAPABILITIES:
        raise HTTPException(404, f"unknown capability {capability!r}")

    with connection() as conn:
        row = repo.get(conn, slug)
        if row is None:
            raise HTTPException(404, f"connection {slug!r} not found")
        if not repo.set_capability(conn, row["id"], capability, body.enabled):
            raise HTTPException(
                409,
                f"{slug!r} was never granted {capability} -- reconnect and "
                "accept that permission on the consent screen.",
            )
        row = repo.with_capabilities(conn, slug)

    return ConnectionOut(**row)  # type: ignore[arg-type]


@router.delete("/google", response_model=DisconnectResult)
async def disconnect_google() -> DisconnectResult:
    """Revoke upstream, remove the files from the Hermes host, clear locally.

    The local clear happens even when the first two fail -- a user who clicks
    Disconnect must end up disconnected here regardless of whether Google or
    the Hermes box is reachable. The result says so when it was untidy.
    """
    with connection() as conn:
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)
        if row is None:
            raise HTTPException(404, "Google is not connected")
        try:
            stored = repo.get_credentials(conn, repo.GOOGLE_SLUG)
        except crypto.SecretUnreadable:
            # Can't revoke what we can't decrypt, but clearing must still work.
            stored = None

    warnings: list[str] = []

    refresh_token = ((stored or {}).get("token") or {}).get("refresh_token")
    if refresh_token:
        failure = await google_oauth.revoke(refresh_token)
        if failure:
            warnings.append(failure)

    for name in (hermes_files.GOOGLE_TOKEN_FILE, hermes_files.GOOGLE_CLIENT_FILE):
        try:
            await hermes_files.delete_file(name)
        except hermes_files.HermesFileError as exc:
            warnings.append(f"{name} may still be on the Hermes host: {exc}")

    note = "; ".join(warnings) if warnings else None
    with connection() as conn:
        repo.clear(conn, repo.GOOGLE_SLUG, note=note)
        row = repo.with_capabilities(conn, repo.GOOGLE_SLUG)

    return DisconnectResult(
        connection=ConnectionOut(**row),  # type: ignore[arg-type]
        warning=note,
    )
