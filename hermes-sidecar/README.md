# Hermes credential sidecar

A drop-box that writes `google_credentials.json` and `google_token.json` into
Hermes' config directory on request.

**You probably do not need this.** In production Hermes runs on the same VPS as
Athena, so `docker-compose.yml` bind-mounts its config directory into the
backend container and the backend writes the files directly — no network, no
token, nothing to run. This service is for the other case: a Hermes box on a
different machine, which is mainly a debugging setup.

## Why it exists

Hermes' `api_server` speaks only `/health`, `/v1/chat/completions` and
`/v1/runs*`, and its docs are explicit that file uploads are unsupported. There
is no endpoint that puts a file in `~/.hermes/`. Sending the credentials through
a Hermes prompt instead was considered and rejected — see
`plans/athena-connections-plan_v.0.2.md` §4b: anything sent as a chat turn is
persisted to `sessions.payload` in plaintext and replayed into model context on
every later turn.

## Run it on the Hermes box

```sh
export HERMES_FILES_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export HERMES_CONFIG_PATH="$HOME/.hermes"

docker build -t hermes-sidecar .
docker run -d --name hermes-sidecar \
  -p 127.0.0.1:8700:8700 \
  -e HERMES_FILES_TOKEN \
  -e HERMES_CONFIG_PATH=/hermes-config \
  -v "$HERMES_CONFIG_PATH:/hermes-config" \
  hermes-sidecar
```

Then point Athena's backend at it:

```
HERMES_FILES_URL=http://127.0.0.1:8700
HERMES_FILES_TOKEN=<the same token>
```

Set `HERMES_CONFIG_PATH` in Athena's env **or** these two, never both — the
backend picks the local path first and would silently ignore the sidecar.

If Athena is on a different host again, reach this over an SSH tunnel rather
than publishing the port:

```sh
ssh -N -L 8700:127.0.0.1:8700 user@hermes-box
```

## Endpoints

| method | path | notes |
|---|---|---|
| `GET` | `/health` | unauthenticated; reports the config path, writability, and which files exist |
| `PUT` | `/credentials/{name}` | bearer token; JSON body; atomic `0600` write |
| `DELETE` | `/credentials/{name}` | bearer token; used by Athena's disconnect |

`{name}` is checked against a two-item allowlist (`google_credentials.json`,
`google_token.json`), so a traversal payload is a 404 rather than something to
sanitise. With no `HERMES_FILES_TOKEN` set the write endpoints return 503 — it
refuses to run open rather than expose an unauthenticated write over a
credential directory.

## Security

The bearer token is the only thing between a caller and Hermes' Google
credentials. Bind to loopback, use a tunnel or TLS, and treat the token like the
refresh token it protects.
