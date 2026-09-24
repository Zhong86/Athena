# Deploying Athena

Push to `main` → GitHub Actions builds both images, pushes them to GHCR, then
SSHes into the VPS and restarts the stack. The VPS never builds anything.

```
push to main
  └─ build (matrix: backend, frontend) ──> ghcr.io/<owner>/athena-{backend,frontend}
       └─ deploy: scp compose files ─> ssh: docker compose pull && up -d
            └─ verify: poll /health until status == "ok"
```

Caddy terminates TLS and serves everything from one origin: `/api/*` is proxied
to the backend with the prefix stripped, everything else goes to Next.js.

---

## One-time VPS setup

### 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in
```

### 2. Add swap

On a 1–2GB instance this is not optional. The backend loads a ~130MB ONNX
embedding model into memory and the OOM killer will otherwise take out the
container mid-request.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 3. Create the app directory and config

```bash
mkdir -p ~/athena && cd ~/athena
# paste .env.example from the repo, then fill it in
nano .env
```

`ATHENA_DOMAIN`'s A record must already point at this VPS — Caddy requests a
certificate on first boot and the handshake fails if DNS has not propagated.
No domain yet? See the comment above `ATHENA_DOMAIN` in `.env.example` for a
free nip.io alternative that works immediately.

Generate `CONNECTIONS_SECRET_KEY` (encrypts stored OAuth credentials at rest)
and paste it in:

```bash
docker run --rm python:3.13-slim sh -c \
  "pip install -q cryptography && python -c \
  'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

Leave `ATHENA_ADMIN_PASSWORD_HASH` blank for now — it needs the `caddy` image,
which step 4 pulls.

### 4. Point DNS, open the firewall, and set the admin password

```bash
sudo ufw allow 80,443/tcp
docker run --rm caddy:2-alpine caddy hash-password
```

Run this with plain `docker run`, not `docker compose run` — Compose refuses
to parse `docker-compose.yml` at all while `ATHENA_ADMIN_PASSWORD_HASH` is
still unset in `.env`, since every service's `environment:` block is
interpolated up front regardless of which service you're targeting.

Paste the resulting hash into `.env` as `ATHENA_ADMIN_PASSWORD_HASH`,
**doubling every `$`** (`$2a$14$abc` → `$$2a$$14$$abc`) — Compose treats a
single `$` in `.env` as variable interpolation and silently truncates the hash
otherwise. Set `ATHENA_ADMIN_USER` to whatever username you want; this pair
gates `/api/connections*`, the one route that touches your Google OAuth
secret.

### 5. Point the backend at Hermes

Hermes runs on this same VPS as its own process, outside Docker. The backend
container reaches it at `http://host.docker.internal:8642` (already the
default in `.env.example`) and writes its credential files straight into
`~/.hermes` via the bind mount `HERMES_CONFIG_DIR` points at — set that to
wherever Hermes' config directory actually is on this box. Nothing else here
is needed unless Hermes lives on a *different* machine, in which case see
`hermes-sidecar/README.md` instead.

### 6. Create a deploy key

On your laptop:

```bash
ssh-keygen -t ed25519 -f athena_deploy -N ""
ssh-copy-id -i athena_deploy.pub -p <ssh-port> <user>@<vps-host>
```

The **private** key (`athena_deploy`) goes into the `VPS_SSH_KEY` secret. Pipe
the file in rather than pasting it — the workflow fails with
`ssh: no key found` if the trailing newline after `-----END OPENSSH PRIVATE
KEY-----` is lost:

```bash
gh secret set VPS_SSH_KEY < athena_deploy
```

### 7. Create a GHCR read token

The VPS needs to pull from GHCR. Create a classic PAT with **`read:packages`**
only, and store it as the `GHCR_TOKEN` secret.

> Alternatively, set both packages to **Public** under the repo's Packages
> settings and delete the `docker login` line from the workflow — no token
> needed. Fine for a hackathon project, since the images contain no secrets.

---

## GitHub repo configuration

**Settings → Secrets and variables → Actions → Secrets:**

| Secret | Value |
| --- | --- |
| `VPS_HOST` | VPS IP or hostname |
| `VPS_PORT` | SSH port — omit if sshd is on 22 |
| `VPS_USER` | SSH user |
| `VPS_SSH_KEY` | Contents of the private `athena_deploy` key |
| `VPS_APP_DIR` | e.g. `/home/<user>/athena` |
| `GHCR_TOKEN` | PAT with `read:packages` |

**→ Variables:**

| Variable | Value |
| --- | --- |
| `PUBLIC_API_BASE_URL` | `https://<your-domain>/api` |

`PUBLIC_API_BASE_URL` is a *variable*, not a secret, because it is compiled into
the client JS bundle at build time and is therefore public by definition.
Changing it requires a rebuild — setting it on the running container does
nothing.

---

## First deploy

```bash
git push origin main
```

Then watch the run in the Actions tab. First boot is slow: the backend
downloads the embedding model before it starts serving, which is why the
healthcheck allows a 180s grace period.

---

## Operations

**Logs**

```bash
cd ~/athena && docker compose logs -f backend
```

**Roll back** — every build is also tagged with its commit SHA:

```bash
# in ~/athena/.env
IMAGE_TAG=sha-<full-commit-sha>
docker compose up -d
```

**Back up** — `athena-data` holds the SQLite DB, the LanceDB index, and every
uploaded file. Nothing in it is recoverable from git:

```bash
docker run --rm -v athena_athena-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/athena-$(date +%F).tar.gz -C /data .
```

Worth putting on a cron job before the stack holds anything you care about.

**Materials gather cron** — this is the "sync" Settings' CRON routine picker
controls, not Hermes; Hermes just answers the relevance/tagging calls the
run makes along the way. The trigger itself is a plain HTTP call the VPS has
to make on its own — nothing inside the container schedules it.

The actual frequency (daily/weekly/biweekly) is set from the Settings page
and stored server-side (`GET`/`PUT /api/materials/gather/interval`), but a
crontab entry can't be reprogrammed at runtime by the app when that dropdown
changes. So the crontab itself should fire *often* — hourly is fine — with
`scheduled=true`, and let the backend decide whether the configured interval
has actually elapsed since the last completed run. Off-schedule calls come
back a cheap `{"skipped": true}` without touching Drive, the inbox, or
Hermes; a manual "Sync now" click on the Knowledge-Sync page never sends
`scheduled=true`, so it always runs regardless of this.

```bash
crontab -e
```

```cron
0 * * * * curl -fsS -X POST "https://<your-domain>/api/materials/gather/run?scheduled=true" \
  -H "X-Gather-Token: <MATERIALS_GATHER_TOKEN from ~/athena/.env>" >/dev/null
```

Changing the schedule afterward is just the Settings page — no VPS access or
crontab edit needed; only the *polling* frequency above (how often the cron
checks in) is fixed at setup time, and hourly is frequent enough for any of
the three options the UI offers.

**Connect Google Drive** — done from the Settings page, not the shell. The
browser will ask for the `ATHENA_ADMIN_USER` / password pair from step 4 only if
you hit `/api/connections*` directly; Αθηνα's own UI calls those endpoints
server-side, so the page itself just works.

The flow is three steps and is explained inline: upload the Desktop-app client
secret JSON from Google Cloud Console, open the consent link, then paste the
address you land on back into the form. That page will fail to load — nothing
listens on the loopback port, and the authorization code is in its address bar.
Athena exchanges the code server-side and writes both files Hermes reads:

```bash
ls -l ~/.hermes/google_credentials.json ~/.hermes/google_token.json  # both 0600
```

Hermes refreshes the token itself from there. If it caches its config at
startup, restart it once after connecting:

```bash
systemctl restart hermes   # or however Hermes is supervised on this box
```

Backing up `athena-data` also backs up an encrypted copy of these credentials.
Restoring it onto a box with a different `CONNECTIONS_SECRET_KEY` leaves them
undecryptable — Settings will say so, and reconnecting is the fix.

---

## Local development is unchanged

The Dockerfiles are only used for deploys. Locally, keep running the backend and
`next dev` directly; `backend/.env` and `frontend/.env.local` still apply, and
`INTERNAL_API_BASE_URL` being unset makes the frontend fall back to
`NEXT_PUBLIC_API_BASE_URL` exactly as before.
