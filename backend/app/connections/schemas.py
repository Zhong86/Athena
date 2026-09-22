from typing import Literal

from pydantic import BaseModel, Field

Provider = Literal["google", "notion", "web"]
AuthType = Literal["oauth2", "token", "none"]
# 'authorizing' is the gap between uploading the client secret and pasting the
# code back -- the UI resumes there after a reload.
ConnectionStatus = Literal[
    "disconnected", "authorizing", "connected", "expired", "error"
]
Capability = Literal["drive.read", "notion.read", "web.read"]


class ConnectionOut(BaseModel):
    """What Settings renders.

    There is deliberately no `secret` field, and adding one would break the
    invariant in plans/athena-connections-plan_v.0.2.md §5.2 -- the credential
    must have no route out of the repository layer. `scopes` is safe: it is what
    Google granted, not the grant itself.
    """

    id: int
    provider: Provider
    slug: str
    display_name: str
    account_label: str | None = None
    base_url: str | None = None
    auth_type: AuthType
    status: ConnectionStatus
    scopes: list[str] = []
    capabilities: dict[str, bool] = {}
    expires_at: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None
    connected_at: str | None = None
    created_at: str


class ConnectionsPage(BaseModel):
    """The list plus the two facts the UI needs to explain itself before the
    user picks a file, rather than after."""

    items: list[ConnectionOut] = []
    # False when CONNECTIONS_SECRET_KEY is unset: connecting is refused, and
    # saying so up front beats failing mid-upload.
    secrets_ready: bool = True
    # Where credential files would be written, e.g. "local (/hermes-config)".
    hermes_destination: str


class AuthorizeStarted(BaseModel):
    """202 body: the client secret is stored, the consent step is the user's."""

    authorize_url: str
    redirect_uri: str
    connection: ConnectionOut


class ExchangeRequest(BaseModel):
    # The whole redirect URL or the bare code -- google_oauth.extract_code
    # accepts either, because people paste both.
    pasted: str = Field(min_length=1, max_length=4096)


class CapabilityUpdate(BaseModel):
    enabled: bool


class DisconnectResult(BaseModel):
    connection: ConnectionOut
    # Set when the local clear succeeded but revoking upstream or removing the
    # Hermes files did not. The disconnect still happened; the user should know
    # it was untidy.
    warning: str | None = None
