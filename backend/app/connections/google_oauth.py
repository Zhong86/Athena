"""The Google OAuth dance, run by Athena on the user's behalf.

Normally a Hermes user does this in Hermes' own console: it prints a consent
URL, they grant, they paste the redirect URL back. Athena does the same three
steps over HTTP so the user never touches the VPS, then writes the resulting
files where Hermes expects them.

Plain httpx rather than google-auth-oauthlib -- it is two requests. google-auth
is used for exactly one thing: `Credentials.to_json()`, so `google_token.json`
carries the field names, `universe_domain` and naive-UTC `expiry` format that
Hermes' own loader parses. That shape is easy to get subtly wrong by hand and
the failure mode is a connection that looks fine and does not work.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from google.oauth2.credentials import Credentials

from app.config import get_settings

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"


class GoogleOAuthError(RuntimeError):
    pass


class ClientJsonInvalid(ValueError):
    """The uploaded file is not a usable Desktop-app client secret."""


def parse_client_json(raw: bytes) -> dict[str, str]:
    """Validate the downloaded client-secret file and pull out what we need.

    A Desktop-app client serialises under `installed`; a Web-application client
    under `web`. Both are valid JSON from Google, so a generic "malformed file"
    message would send the user round in circles -- name the actual problem.
    """
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ClientJsonInvalid(
            "that file is not valid JSON. Upload the client secret file exactly "
            "as Google Cloud Console downloaded it, without editing it."
        ) from exc

    if not isinstance(payload, dict):
        raise ClientJsonInvalid("that JSON file is not a Google client secret.")

    if "installed" not in payload and "web" in payload:
        raise ClientJsonInvalid(
            "that is a Web application client. Google only allows the "
            "paste-the-code flow for Desktop app clients -- create a new OAuth "
            "client ID with application type Desktop app and upload that one."
        )

    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise ClientJsonInvalid(
            "that JSON file is not a Google client secret -- it has no "
            '"installed" section. Download it from APIs & Services > '
            "Credentials, not from the project settings."
        )

    missing = [
        key
        for key in ("client_id", "client_secret", "auth_uri", "token_uri")
        if not installed.get(key)
    ]
    if missing:
        raise ClientJsonInvalid(
            f"that client secret file is missing {', '.join(missing)}. "
            "Re-download it from Google Cloud Console."
        )

    return {
        "client_id": installed["client_id"],
        "client_secret": installed["client_secret"],
        "auth_uri": installed["auth_uri"],
        "token_uri": installed["token_uri"],
    }


def authorize_url(client: dict[str, str]) -> str:
    """The consent URL the user opens.

    access_type=offline and prompt=consent are both load-bearing: without them
    Google withholds the refresh_token on a re-consent, and Hermes needs it to
    keep working past the first hour.
    """
    settings = get_settings()
    params = {
        "client_id": client["client_id"],
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(settings.google_scope_list),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{client.get('auth_uri') or AUTH_ENDPOINT}?{urlencode(params)}"


def extract_code(pasted: str) -> str:
    """Take whatever the user pasted and find the authorization code.

    The Desktop-app flow redirects to a loopback port on the user's own machine
    where nothing is listening, so the browser shows a connection error and the
    code sits in the address bar. People paste the whole URL, and sometimes just
    the code, so accept both rather than making them read instructions.
    """
    pasted = pasted.strip()
    if not pasted:
        raise ClientJsonInvalid("paste the URL you were redirected to, or the code from it.")

    if "://" in pasted or pasted.startswith("localhost") or "?" in pasted:
        query = parse_qs(urlparse(pasted if "://" in pasted else f"http://{pasted}").query)
        error = (query.get("error") or [""])[0]
        if error:
            if error == "access_denied":
                raise ClientJsonInvalid(
                    "you declined the consent screen, so Google issued no code. "
                    "Open the link again and choose Allow."
                )
            raise ClientJsonInvalid(f"Google reported an error instead of a code: {error}.")
        code = (query.get("code") or [""])[0]
        if not code:
            raise ClientJsonInvalid(
                "that URL has no code in it. Copy the full address bar contents "
                "from the page you landed on after choosing Allow."
            )
        return code

    # A bare code. Google's look like "4/0A...", but that prefix is not a
    # documented guarantee, so only reject what is obviously not a code.
    if " " in pasted:
        raise ClientJsonInvalid(
            "that does not look like a code or a redirect URL. Paste the whole "
            "address bar contents from the page you landed on."
        )
    return pasted


async def exchange_code(client: dict[str, str], code: str) -> dict[str, Any]:
    """Trade the authorization code for tokens.

    The redirect_uri must match the consent request byte for byte even though
    nothing ever listened on it -- Google validates it as part of the grant.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                client.get("token_uri") or TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError as exc:
        raise GoogleOAuthError(f"could not reach Google to exchange the code: {exc}") from exc

    if resp.status_code != 200:
        detail = _google_error(resp)
        raise GoogleOAuthError(f"Google rejected the code: {detail}")

    payload = resp.json()
    if not payload.get("access_token"):
        raise GoogleOAuthError(f"unexpected token response shape: {payload!r}")
    if not payload.get("refresh_token"):
        # Without this Hermes cannot refresh and access dies in an hour. The
        # usual cause is re-consenting to an app that already has a grant.
        raise GoogleOAuthError(
            "Google returned no refresh token, so Hermes would lose access "
            "within the hour. Remove Αθηνα at "
            "https://myaccount.google.com/permissions and connect again."
        )
    return payload


async def fetch_account_email(access_token: str) -> str | None:
    """Label the connection with the account it belongs to.

    Best-effort: a connection that works but shows no email is a cosmetic
    problem, and failing the whole connect over it would not be.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
        if resp.status_code != 200:
            return None
        return resp.json().get("email")
    except (httpx.HTTPError, ValueError):
        return None


async def revoke(refresh_token: str) -> str | None:
    """Best-effort revoke. Returns a message if it failed, None on success --
    a disconnect still clears locally either way (plan §5.4)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(REVOKE_ENDPOINT, data={"token": refresh_token})
    except httpx.HTTPError as exc:
        return f"could not reach Google to revoke the token: {exc}"
    if resp.status_code != 200:
        return f"Google would not revoke the token ({resp.status_code})"
    return None


def token_file(client: dict[str, str], token: dict[str, Any]) -> dict[str, Any]:
    """Build `google_token.json` in the shape Hermes' loader reads.

    Round-tripped through google-auth rather than hand-written so the field
    names, `universe_domain` and the naive-UTC `expiry` format come from the
    library that will parse them.
    """
    scopes = (token.get("scope") or "").split() or get_settings().google_scope_list
    credentials = Credentials(
        token=token["access_token"],
        refresh_token=token["refresh_token"],
        token_uri=client.get("token_uri") or TOKEN_ENDPOINT,
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        scopes=scopes,
    )
    # google-auth stores expiry as naive UTC and serialises it with a trailing
    # Z; handing it an aware datetime makes it compare against utcnow() wrongly.
    credentials.expiry = _expires_at(token).replace(tzinfo=None)
    return json.loads(credentials.to_json())


def _expires_at(token: dict[str, Any]) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600)))


def expires_at_iso(token: dict[str, Any]) -> str:
    return _expires_at(token).strftime("%Y-%m-%dT%H:%M:%SZ")


def granted_scopes(token: dict[str, Any]) -> list[str]:
    return (token.get("scope") or "").split() or get_settings().google_scope_list


def _google_error(resp: httpx.Response) -> str:
    """Google's OAuth errors are JSON; fall back to the body if not."""
    try:
        payload = resp.json()
    except ValueError:
        return f"{resp.status_code} {resp.text[:500]}"
    error = payload.get("error", resp.status_code)
    description = payload.get("error_description")
    return f"{error}: {description}" if description else str(error)
