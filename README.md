# Fichtelink

Self-service mailing-list and contact-list platform for school parent groups (Elternbeiräte, Klassenlisten), Fördervereine, and similar small associations. Originally motivated by the need for a single place where parents can register, share contact information with controlled per-field visibility, and receive forwarded mail addressed to a class list.

The product specification (in German) is in [`filink.md`](filink.md). All architectural decisions are documented in [`CLAUDE.md`](CLAUDE.md) — including the data model, mail-pipeline behaviour, authentication, encryption, and deployment topology.

## Status

**Feature-complete, deployed live.** The full stack is implemented and verified on `fichtelink.caos.cloud`: domain model, passkey auth, list/record UI with per-field visibility, the mail pipeline (outbound, IMAP IDLE consumer, inbound decision + release/approval, bounce correlation, retention), school-class lifecycle (rollover, transfer, admin handover), aggregate aliases, and the **forms module** (self-service event signups — see [Forms](#forms-event-signups) below). Tests are written phase-parallel and run on the deploy VM.

The commands below are the live deployment procedure.

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

### Backups

A ready-made backup script lives at [`scripts/backup.sh`](scripts/backup.sh). It runs `pg_dump` inside the `db` container, writes a timestamped gzip dump, and prunes dumps older than `KEEP_DAYS` (default 14):

```bash
COMPOSE_DIR=/opt/fi-link BACKUP_DIR=/var/backups/fichtelink ./scripts/backup.sh
```

Automate it from the **host** crontab (not inside a container) — daily at 03:30:

```cron
30 3 * * * COMPOSE_DIR=/opt/fi-link BACKUP_DIR=/var/backups/fichtelink /opt/fi-link/scripts/backup.sh >> /var/log/fichtelink-backup.log 2>&1
```

**A database dump is not a complete backup on its own.** `LIST_RECORD_VALUE` rows are encrypted with `FERNET_KEY` (in `.env`); without that key the dump is unrecoverable ciphertext. Back up `.env` / `FERNET_KEY` **off-host and separately** from the dumps. Sync the dump directory off-host too (rclone, restic, or similar).

Restore a dump onto a fresh stack:

```bash
gunzip -c fichtelink-2026-05-29T03-30-00.sql.gz | docker compose exec -T db psql -U fichtelink fichtelink
```

### Account recovery

**Primary path is self-service** (no admin needed): a locked-out user requests a fresh passkey-enrollment link to their on-file email at `/auth/recover/` (linked from the login page). It is additive (keeps existing passkeys), enumeration-resistant, rate-limited, and handles the shared-family-mailbox case with one labelled link per account. See *Account recovery* in CLAUDE.md.

**Operator fallback (super-admin)** — only when the mailbox itself is lost, or a shared-mailbox account needs disambiguation:

```bash
docker compose exec web python manage.py reset_passkeys --email <user-email>
```

The command **deletes** the user's existing passkeys and **prints a fresh enrollment link to stdout**. The super-admin relays the link to the user out-of-band (phone, in person, separate email). The user opens it and enrols a new passkey — their data, list memberships, and family relationships are retained. If the email matches multiple Users (shared family mailbox), the command refuses and lists candidates; re-run with `--user-id`.

## Forms (event signups)

The forms module lets people sign **themselves** up for activities — the recurring
school events these groups run (summer parties, cake sales, helper rosters). A form
is an ordered set of parts, each of one kind:

- **HTML** — an authored description block (with optional image assets).
- **Slots** — fixed positions with a capacity, e.g. *"Aufbau Freitag 14:00–15:00"*
  for two people. Participants claim a free slot; a full slot shows *"vergeben"*.
- **Contributions** — an open free-text signup, e.g. cake donations where everyone
  enters what they bring (*"Apfelkuchen"*, *"Brezeln"*).

Each signup carries two visibility switches — **name visible** (default on) and
**email visible** (default off) — chosen at signup and editable afterward; hidden
entries render as *"vergeben"* / *"anonym"*. Form admins and super-admins always see
real names/emails for moderation.

**Authoring is super-admin only, in the Django Admin** — there is no self-service
form builder. The HTML body of any part doubles as the description shown above its
signup list.

**Sharing**: every form has a single reusable broadcast link, shown read-only at the
top of the form page for its admin (the token is created lazily on first admin view —
no separate "generate" step). Mail that link to recipients. Viewing needs no login;
**signing up** does — a new participant is taken through passkey registration and then
straight back to the form. Signing up grants the form access so it stays findable
under `/forms/`.

**Re-using a form next year**: in the Django Admin form list, select a form and run
the **"Als Vorlage klonen (ohne Eintragungen)"** action. It deep-copies the parts,
slots, asset images, and texts but drops all signups, access grants, and the share
token, and reopens signups — last year's form stays intact for reference.

## Matrix messenger (class chats)

Optional Synapse-backed class chats run **alongside** email, for time-critical
notices ("Bus verspätet sich", "Ausflug abgesagt"). Each matrix-enabled class
list gets one invite-only Matrix room; parents join with Element. The whole
feature is gated by `MATRIX_ENABLED` — off, nothing in the app touches Matrix.

**Setup** is a one-time operator procedure (Synapse container, separate `synapse`
database, Apache routes, `.well-known` delegation, federation test): see
[`docs/matrix-phase0-operator.md`](docs/matrix-phase0-operator.md). Architecture
and rationale: [`docs/matrix-architecture.md`](docs/matrix-architecture.md) and
[`docs/matrix-implementation-plan.md`](docs/matrix-implementation-plan.md).

**How it works once enabled:**

- Accounts are provisioned lazily (Weg A — server-assigned password). A parent
  opens *Messenger-Zugang* in the menu to see their homeserver / Matrix-ID /
  password + a QR, and enters them in Element. Display names are pseudonymous
  (`Elternteil <id>`) — real contact data stays in the encrypted app DB, never
  in Matrix profiles.
- Membership is mirrored automatically: joining a class list → room invite,
  leaving → kick, becoming list-admin → room power level 50. No manual step.
- Admins send to a class room via *Nachricht an Klassenraum* (sent as the
  service account, so it also posts in broadcast-only rooms). *Klassenraum-
  Moderation* bans/unbans a member from the room in an emergency.

**Operator commands** (run in the `web` container):

```
python manage.py matrix_healthcheck                 # probe Synapse
python manage.py matrix_bootstrap_service_account   # one-time, after enabling
python manage.py matrix_create_rooms --verify        # create rooms for class lists
python manage.py matrix_reconcile                    # repair membership drift
```

A daily periodic task (`matrix.tasks.reconcile_all_rooms`) re-invites members
missing from their room. **Back up the `synapse` database and the `./synapse`
volume** (the signing key — losing it changes the server identity and breaks
federation + existing rooms); see the operator checklist.

## Development

Local dev requires Python 3.12+ and PostgreSQL. WebAuthn accepts `localhost` as a non-HTTPS origin, so dev does not need a TLS cert. The compose stack can be used locally too — point `MAIL_DOMAIN` at a throwaway domain or a test catch-all.

## Documentation

- [`docs/benutzerhandbuch.md`](docs/benutzerhandbuch.md) — **user manual** (German) for end users and list admins (Elternvertreter / list creators); also served in-app at **`/hilfe`**
- [`docs/superadmin-handbuch.md`](docs/superadmin-handbuch.md) — **super-admin manual** (German): templates, top-level lists, aggregate aliases, school-year rollover, recovery; served in-app at **`/hilfe/superadmin`** (super-admins only)
- [`filink.md`](filink.md) — product specification (German, original)
- [`CLAUDE.md`](CLAUDE.md) — architecture decisions and implementation guidance
