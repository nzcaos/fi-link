# Fichtelink

Self-service mailing-list and contact-list platform for school parent groups (Elternbeiräte, Klassenlisten), Fördervereine, and similar small associations. Originally motivated by the need for a single place where parents can register, share contact information with controlled per-field visibility, and receive forwarded mail addressed to a class list.

The product specification (in German) is in [`filink.md`](filink.md). All architectural decisions are documented in [`CLAUDE.md`](CLAUDE.md) — including the data model, mail-pipeline behaviour, authentication, encryption, and deployment topology.

## Status

**Pre-implementation.** The repository currently contains the specification, the architecture decisions, this README, and a `.gitignore` for the planned stack. No application code yet — the next step is the Django project scaffold and the domain model from CLAUDE.md.

The commands below describe the intended deployment procedure once the code exists.

## Tech stack

- **Django + PostgreSQL + HTMX** — server-rendered, no SPA
- **`procrastinate`** — Postgres-native task queue (no Redis)
- **Passkeys only**, hand-rolled on `py_webauthn` (server) + `@simplewebauthn/browser` v9 vendored as a UMD bundle in `static/vendor/` — no passwords, no email-magic-link recovery
- **Provider-agnostic mail** — IMAP IDLE on a catch-all mailbox, SMTP submission for outbound

## Deployment

Fichtelink is shipped as four Docker containers. TLS is terminated by a separate reverse-proxy on the same private network — the application containers speak plain HTTP only.

### Prerequisites on the target VM

- Docker and Docker Compose
- A DNS name pointing at the reverse-proxy (example: `fichtelink.caos.cloud`)
- A reverse-proxy (Apache, nginx, Caddy, …) with a valid TLS certificate, reachable on the same private network as the VM
- A mail account at any provider with:
  - **Catch-all** on the configured mail domain
  - **IMAP** with **IDLE** support
  - **SMTP submission**

  Gmail and Google Workspace do **not** fit (no catch-all on entry tiers). Mailbox.org Standard and Migadu Micro are known fits.

### Reverse-proxy configuration

Example Apache vhost (the cert path is whatever your Let's Encrypt setup uses):

```apache
<VirtualHost *:443>
    ServerName fichtelink.caos.cloud
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/fichtelink.caos.cloud/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/fichtelink.caos.cloud/privkey.pem

    ProxyPreserveHost On
    ProxyPass        / http://<fichtelink-vm-ip>:8000/
    ProxyPassReverse / http://<fichtelink-vm-ip>:8000/
    RequestHeader set X-Forwarded-Proto "https"
</VirtualHost>
```

The VM's container port (8000 by default) **must not be reachable from the public internet** — restrict it via firewall to the reverse-proxy host only.

### First-time setup

```bash
git clone https://github.com/nzcaos/fi-link.git
cd fi-link
cp .env.example .env
# Edit .env — see "Required environment variables" below
docker compose build
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py collectstatic --noinput
docker compose run --rm web python manage.py bootstrap_super_admin \
    --email you@example.org --given-name Anna --family-name Müller
docker compose up -d
```

`bootstrap_super_admin` creates a Super-Admin Person+User and **prints a passkey-enrollment link to stdout**. Open that link in a browser on any passkey-capable device (iOS 16+, Android 9+, modern macOS / Windows / Linux with a current browser) and enrol a passkey — there is no password. The link is single-use and expires after 7 days.

### Required environment variables

| Variable | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | Generate: `python -c 'import secrets; print(secrets.token_urlsafe(64))'` |
| `FERNET_KEY` | Installation-wide key for at-rest encryption. Generate: `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. **Losing this key makes all stored list-record values unrecoverable.** Back it up offline. |
| `POSTGRES_PASSWORD` | Any strong random string |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASS` | Catch-all mailbox credentials |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` | Submission credentials at the same provider |
| `MAIL_DOMAIN` | Domain on which list addresses are received (e.g. `caos.cloud` → `5a@caos.cloud`). MX of this domain must point at the mail provider. |
| `ALLOWED_HOSTS` | e.g. `fichtelink.caos.cloud` |
| `CSRF_TRUSTED_ORIGINS` | e.g. `https://fichtelink.caos.cloud` |

### Updating

```bash
git pull origin main
docker compose build
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py collectstatic --noinput
docker compose up -d
```

### Day-2 operations

- `docker compose logs -f web` — request log
- `docker compose logs -f worker` — task-queue activity (procrastinate)
- `docker compose logs -f imap_idle` — incoming-mail consumer
- `docker compose exec db pg_dump -U fichtelink fichtelink > backup-$(date +%F).sql` — manual DB backup. Automate via host cron and off-host sync (rclone or similar).

### Account recovery (super-admin)

If a user has lost all their passkeys and the list-admin recovery chain (see *Account recovery* in CLAUDE.md) has escalated to the super-admin:

```bash
docker compose exec web python manage.py reset_passkeys --email <user-email>
```

The command deletes the user's existing passkeys and **prints a fresh enrollment link to stdout**. The super-admin relays the link to the user out-of-band (phone, in person, separate email). The user opens it and enrols a new passkey — their data, list memberships, and family relationships are retained. If the email matches multiple Users (shared family mailbox), the command refuses and lists candidates; re-run with `--user-id`.

## Development

Local dev requires Python 3.12+ and PostgreSQL. WebAuthn accepts `localhost` as a non-HTTPS origin, so dev does not need a TLS cert. The compose stack can be used locally too — point `MAIL_DOMAIN` at a throwaway domain or a test catch-all.

## Documentation

- [`filink.md`](filink.md) — product specification (German, original)
- [`CLAUDE.md`](CLAUDE.md) — architecture decisions and implementation guidance
