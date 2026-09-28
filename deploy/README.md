# Axiora Pulse — Dev Environment Runbook

Everything about the shared dev environment: what exists, how deploys work, how to operate it,
and what to fix when it breaks.

**URL:** https://qa.axiorapulse.com
**Owner:** platform / devops
**Last verified:** 2026-07-23

---

## 1. What this is

A deliberately simple single-box dev environment. One EC2 instance runs two containers behind
Caddy; GitHub Actions deploys to it on every push to `develop`.

```
Developer pushes to develop
        │
        ├─ axiora-pulse-be  ──▶ GH Actions ──rsync source──▶ EC2 ──docker build──▶ api container
        └─ axiora-pulse-fe  ──▶ GH Actions ──npm run build──▶ rsync dist/ ──▶ EC2

Browser ──HTTP:80──▶ Caddy ─┬─ /api/*  ──▶ api:8000 (uvicorn/FastAPI)
                            └─ /*      ──▶ /srv (static SPA)
                                              │
                     api ──▶ RDS: axiora_dev_db (on the shared axiora-prod-db instance)
```

This is **not** a staging or production topology. No autoscaling, no redundancy, no backups. It
exists so the team and client can look at `develop` without running it locally.

**Cost:** ~$18/month (t3.small + 30 GB gp3). The dev database adds nothing — it shares the
existing RDS instance.

---

## 2. Inventory

| Resource | Value |
|---|---|
| AWS account | `399894608507` |
| Region | `ap-south-1` (Mumbai) |
| EC2 instance | `i-0705af92110312967` — "Axiora-dev-server" |
| Instance type | t3.small, Ubuntu 24.04 LTS, 30 GB gp3 |
| Elastic IP | `13.126.92.39` (static — do not release) |
| Private IP | `172.31.6.47` |
| VPC / Subnet | `vpc-0f2b8b53d10d121ee` / `subnet-088581f5dde13b32c` |
| Security group | `sg-0cba7a4942e53eba7` ("launch-wizard-1") — inbound 22, 80, 443 |
| DNS | `qa.axiorapulse.com` → A → `13.126.92.39`, client-owned Route 53 zone (we have write access) |
| TLS | Let's Encrypt via Caddy, auto-renewing; certs in the `caddy_data` volume |
| RDS instance | `axiora-prod-db.clqkm2moazs2.ap-south-1.rds.amazonaws.com` (shared with prod) |
| Dev database | `axiora_dev_db`, owned by role `axiora_dev` |
| Docker | 29.6.2 + compose v2 |
| SSH key | `axiora-dev-key.pem` — also stored as the `EC2_SSH_KEY` GitHub secret |

**Repos:** `Axiora-products/axiora-pulse-be`, `Axiora-products/axiora-pulse-fe` — branch `develop`.

---

## 3. Server layout

Everything lives in `/opt/axiora`, owned by `ubuntu`:

```
/opt/axiora/
├── docker-compose.yml      # 2 services: api, caddy
├── Caddyfile               # :80 — SPA + /api reverse proxy
├── .env                    # secrets. chmod 600. NOT in git. Hand-maintained.
├── dist/                   # built SPA, written by the frontend workflow
└── axiora-pulse-be/        # legacy backend source (no longer used for deployment)
```

The backend workflow uploads `deploy/docker-compose.yml` on every deployment.
`Caddyfile` is provisioned separately and is not overwritten by CI.
The API uses `${ECR_REGISTRY}/axiora-pulse-be:${IMAGE_TAG}`; both values are
persisted in `/opt/axiora/.env` by the workflow, including on first deployment.

---

## 4. How deploys work

Both repos have `.github/workflows/deploy-dev.yml`, triggered on push to `develop` (or manually via
**Actions → Deploy dev → Run workflow**).

**Backend**: build once in GitHub Actions, push the immutable `sha-<12-character SHA>`
image to ECR, upload Compose configuration, persist the registry and image tag,
log in to ECR on EC2, pull and start the API, then poll `/health`.
Production promotes an existing QA image and deploys it after the Production approval gate.

Required backend repository variables: `AWS_REGION`, `ECR_REGISTRY` (registry hostname),
and `AWS_ROLE_ARN` (GitHub OIDC role with ECR push permissions).
EC2 also needs AWS CLI and an instance role with ECR pull permissions.
Production additionally requires `PROD_EC2_HOST` and its Production environment configuration.

**Frontend** (~1 min): checkout → `npm ci` → `npm run build` → `rsync dist/` →
`docker compose up -d caddy` → curl the site.

**Required GitHub secrets — set in *both* repos** (secrets are per-repository; forgetting the
second repo is the single most common failure here):

| Secret | Value |
|---|---|
| `EC2_HOST` | `13.126.92.39` |
| `EC2_SSH_KEY` | full contents of `axiora-dev-key.pem`, including BEGIN/END lines |

### Things worth knowing

- **Migrations run automatically.** `run_migrations()` is called from the FastAPI lifespan hook and
  executes `alembic upgrade head` on every container start. No manual migration step — but it also
  means a bad migration takes the API down on boot.
- **The API runs a single uvicorn worker.** Deliberate: multiple workers would race on
  `alembic upgrade head` at startup.
- **`VITE_*` values are compiled into the bundle**, not read at runtime. `VITE_API_URL=/api` is
  relative, so the bundle contains no hostname and survives an IP change.
- **`.env` changes need `--force-recreate`.** `env_file` is read when a container is *created*, not
  started, so `docker compose restart` will silently keep the old values.

---

## 5. Common operations

All from your machine, Git Bash. `SSH="ssh -i axiora-dev-key.pem ubuntu@13.126.92.39"`.

**Deploy** — push to `develop`, or re-run from the Actions tab. Don't deploy by hand; CI is the
only path that keeps the box and the repo in sync.

**Check health**
```bash
$SSH 'cd /opt/axiora && docker compose ps && docker compose exec -T api curl -s localhost:8000/health'
```
Healthy looks like `"status":"healthy"`, `"skills_count":7`, `"provider_configured":true`.

**Tail logs**
```bash
$SSH 'cd /opt/axiora && docker compose logs -f --tail 100 api'
```

**Change a secret / env var**
```bash
$SSH 'nano /opt/axiora/.env'
$SSH 'cd /opt/axiora && docker compose up -d --force-recreate api'
```

**Restart everything**
```bash
$SSH 'cd /opt/axiora && docker compose up -d --force-recreate'
```

**Roll back**: production's manual workflow accepts an existing immutable `image_tag`.
For dev, set `IMAGE_TAG` in `/opt/axiora/.env` to a previous ECR SHA tag, authenticate
to ECR, then run `docker compose pull api && docker compose up -d --no-build api`.

**Query the dev database**
```bash
psql -h axiora-prod-db.clqkm2moazs2.ap-south-1.rds.amazonaws.com -U axiora_dev -d axiora_dev_db -W
```

**Reset the dev database** (destroys all dev data; Alembic rebuilds the schema on next boot)
```sql
-- as postgres master
DROP DATABASE axiora_dev_db;
CREATE DATABASE axiora_dev_db OWNER axiora_dev;
```
```bash
$SSH 'cd /opt/axiora && docker compose up -d --force-recreate api'
```

**Disk filling up** — Docker layers accumulate. `$SSH 'docker system prune -af --volumes'`.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workflow fails at **Set up SSH**, exit 1, no output | `EC2_HOST` or `EC2_SSH_KEY` missing in *that* repo | Add both secrets, re-run |
| Workflow fails at **Sync source** with host key error | EIP reassigned or instance rebuilt | `ssh-keyscan` runs per-job, so this means the IP genuinely changed — update `EC2_HOST` |
| Health check times out, logs show connection refused to RDS | RDS security group doesn't allow `13.126.92.39/32` on 5432 | Add the rule |
| API boots then exits, logs mention alembic | Bad or conflicting migration | Read `docker compose logs api`; fix the migration, don't patch the DB by hand |
| Site loads but every API call 502s | api container down | `docker compose ps`, then check its logs |
| Site 404s on refresh of a deep link | Caddy `try_files` fallback broken | Check `Caddyfile` is the version in this folder |
| `.env` edited but nothing changed | Used `restart` instead of `--force-recreate` | See above |
| Config change deployed but not reflected | `Caddyfile` is provisioned separately | `scp` it manually |

---

## 7. Safety rules

**The dev app must never point at the prod database.** `alembic upgrade head` runs on every boot,
so a misconfigured `DATABASE_URL` would migrate prod. Two guardrails are in place:

1. `DATABASE_URL` in `/opt/axiora/.env` targets `axiora_dev_db`.
2. `CONNECT` on the prod database has been revoked from `PUBLIC`, so the `axiora_dev` role is
   refused even if the URL is wrong. Verify with:
   ```bash
   PGPASSWORD='...' psql -h <rds> -U axiora_dev -d axiora-db -c "select 1;"   # must fail
   ```

**Never commit `.env`.** It's gitignored in the backend repo; `.env.example` documents the keys.

**Don't push a local working tree you haven't reconciled with `origin/develop`.** An earlier local
checkout carried a hardcoded prod RDS password in `app/core/config.py`; it was never committed, and
the branch has since moved to `os.getenv()` everywhere. Always `git pull` before you branch.

---

## 8. Known gaps

Accepted for a dev box; fix before this pattern goes anywhere near prod.

- ~~No TLS~~ — resolved 2026-07-23. Caddy serves `qa.axiorapulse.com` with a Let's Encrypt
  certificate, auto-renewing. Certificates persist in the `caddy_data` Docker volume; **never
  `docker volume rm` it**, or the next start re-requests a cert and burns Let's Encrypt rate limit
  (5 failures/hour, 5 duplicate certs/week).
- **SSH open to `0.0.0.0/0`** on `sg-0cba7a4942e53eba7`. This is *required* as long as deploys run
  over SSH from GitHub-hosted runners, which use a large rotating IP pool — narrowing the rule to
  team IPs will break CI. Mitigation in place: key-only auth (`passwordauthentication no`). The only
  real fix is to stop deploying over SSH — move the workflows to GitHub OIDC → IAM role →
  `aws ssm send-command`, then close port 22 entirely.
- **Prod RDS is publicly accessible** and shares an instance with dev. Acceptable now; separate
  instances before real client data exists.
- **Secrets live in a plaintext `.env`** on the box. Move to SSM Parameter Store with an instance
  role when convenient.
- **A long-lived SSH private key sits in GitHub secrets.** The better pattern is GitHub OIDC → an
  IAM role → `ssm send-command`, which removes the standing credential entirely.
- **Single instance**, so any deploy is a brief outage and there is no redundancy.

---

## 9. Rebuilding from scratch

If the box is lost:

1. Launch Ubuntu 24.04 t3.small in ap-south-1, 30 GB, SG with 22 (your IP) + 80 (anywhere).
2. Associate the Elastic IP `13.126.92.39` — this keeps the GitHub secrets valid.
3. `ssh -i axiora-dev-key.pem ubuntu@13.126.92.39 'bash -s' < setup.sh`
4. `scp docker-compose.yml Caddyfile ubuntu@13.126.92.39:/opt/axiora/`
5. Recreate `/opt/axiora/.env` from `axiora-pulse-be/.env.example`, `chmod 600`.
6. Re-run both workflows from the Actions tab, **backend first**.

The database is untouched by any of this — it lives on RDS.
