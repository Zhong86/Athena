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

### 4. Point DNS and open the firewall

```bash
sudo ufw allow 80,443/tcp
```

### 5. Create a deploy key

On your laptop:

```bash
ssh-keygen -t ed25519 -f athena_deploy -N ""
ssh-copy-id -i athena_deploy.pub <user>@<vps-host>
```

The **private** key (`athena_deploy`) goes into the `VPS_SSH_KEY` secret.

### 6. Create a GHCR read token

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

---

## Local development is unchanged

The Dockerfiles are only used for deploys. Locally, keep running the backend and
`next dev` directly; `backend/.env` and `frontend/.env.local` still apply, and
`INTERNAL_API_BASE_URL` being unset makes the frontend fall back to
`NEXT_PUBLIC_API_BASE_URL` exactly as before.
