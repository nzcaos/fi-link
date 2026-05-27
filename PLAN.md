# Coding-Plan

Implementierungsplan für Fichtelink, geschnitten in 10 Phasen. Jede Phase ist so dimensioniert, dass sie in einem 5-Stunden-Usage-Slot abgeschlossen werden kann und mit einem Commit endet. Jede Phase startet kalt aus `CLAUDE.md`, `filink.md` und dem bisherigen Code — die Konversation davor wird nicht gebraucht.

**Source of Truth für Architektur:** `CLAUDE.md`. Dieser Plan ist nur die Abarbeitungs-Reihenfolge.

## Phase 0 — Skeleton & Compose

- [x] **Ziel:** `docker compose up` startet alle vier Container; Django-Default-Seite ist via Apache-Proxy auf `fichtelink.caos.cloud` erreichbar.

Outputs:
- `pyproject.toml` mit Pinned Deps (Django 5.x, psycopg, gunicorn, procrastinate, aioimaplib, django-allauth, django-cryptography, whitenoise)
- `Dockerfile` (single image, `python:3.12-slim`)
- `docker-compose.yml` (db, web, worker, imap_idle — letzte drei als Stubs)
- `.env.example` mit allen Keys aus README
- `settings.py`: `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_PROXY_SSL_HEADER`, `USE_X_FORWARDED_HOST`, Fernet-Key-Bindung

**Verify:** Browser-Hit auf die Domain liefert die Django-Welcome-Seite.

## Phase 1 — Domänenmodell

- [x] **Ziel:** Alle Kern-Entitäten als Django-Models, Migrations grün, Django-Admin zeigt sie an.

Outputs:
- Apps `accounts`, `lists`, `forms`
- Models: PERSON, USER (`AbstractBaseUser`), LISTTEMPLATE, LIST_ATTRIBUT, LIST (mit `parent_list_id`, Cohort-Feldern, Alias-Spalte), LIST_RECORD (`role`), LIST_RECORD_VALUE (encrypted via django-cryptography), LIST_RECORD_ACCESS, LIST_ADMIN, LIST_ACCESS, PERSON_RELATIONSHIP, RECORD_MANAGER, LIST_SEND_PERMISSION
- LISTTEMPLATE-`member_subject_mode` + konfigurierbare Rollen-Taxonomie
- Django-Admin-Registrierung (Super-Admin-MVP)

**Verify:** Entitäten manuell im Admin anlegen, FK-Beziehungen funktionieren.

## Phase 2 — Authentifizierung (Passkeys)

- [x] **Ziel:** End-to-end Registrierung + Login + Recovery-CLI.

Outputs:
- `py_webauthn` (Server) + `@simplewebauthn/browser` v9 als vendored UMD-Bundle in `static/vendor/`; kein `django-allauth`, kein CDN (Begründung in CLAUDE.md)
- Models: `Passkey` (credential_id, public_key, sign_count, user, label, last_used_at), `WebAuthnChallenge` (challenge, purpose, expected_user_id, expires_at)
- Endpunkte: `/auth/register-begin`, `/auth/register-finish`, `/auth/login-begin`, `/auth/login-finish`, `/auth/logout`
- Registrierungs-Flow (E-Mail → Aktivierungs-Link → Passkey-Enrollment)
- Login per discoverable credentials (`user.displayName` gesetzt), Email-Feld mit `autocomplete="email webauthn"` für Conditional-UI
- Constant-time `login-begin` (≥ 250 ms Floor, Fake-`allowCredentials` für unbekannte Emails); Resolution gegen USER-Set bei geteilter `PERSON.email`
- Passkey-Verwaltung (Add/Delete) pro Account, HTMX-driven
- RP-Config aus Env: `RP_ID`, `RP_ORIGIN`, `RP_NAME` (in `.env.example` ergänzen)
- Management-Commands: `bootstrap_super_admin`, `reset_passkeys`
- Periodischer `procrastinate`-Task: WebAuthnChallenge-Sweeper (TTL 5 min)

**Verify:** Echte Registrierung + Login in zwei verschiedenen Browsern über die Live-Domain. Test auf Mobile (iOS Safari + Android Chrome), weil das die Geräte sind, die bei Fi-Planer die CDN-Probleme zeigten — der vendored Bundle muss dort sauber laden.

**Restpunkt (Nachtrag 2026-05-26, in Phase 3b mitgenommen):** Im Registrierungs-Template (`templates/auth/register.html`) ist die E-Mail bereits `required`, aber der Inline-Privacy-Hinweis fehlt noch. Gem. *Architecture decisions (authentication) / Registration and login flow* in CLAUDE.md: kurzer Satz direkt unter dem E-Mail-Feld — *"Ihre E-Mail wird für die Kommunikation mit Ihnen verwendet und nur sichtbar, wenn Sie sie pro Liste explizit freigeben."*

## Phase 3a — Listen-Modelle & Admin-Views

- [ ] **Ziel:** LISTTEMPLATE-Pflege (Super-Admin), Listen-CRUD, Sichtbarkeits-Service als zentrale Funktion.

Outputs:
- LISTTEMPLATE-CRUD im Django-Admin (bereits in Phase 1 vorbereitet)
- Super-Admin legt Top-Level-Listen an; normale User legen Sub-Listen unter eigenen Parents an (HTMX-Form `/lists/new/`)
- Listen-Index `/lists/` + Detail-Stub `/lists/<pk>/` als Landeplatz nach Anlage (Detail-UI in Phase 3b)
- Modul `lists/permissions.py` mit reinen Helpern (`can_user_admin_list`, `can_user_create_top_level_list`, `can_user_create_sublist_under`, `eligible_parents_for`, `can_user_edit_record`, …) — siehe CLAUDE.md *Permission and visibility layer*
- Modul `lists/visibility.py` mit `can_user_see_field(user, record, attribute)` und `visible_attributes_for(user, record)` (Audience-Resolution gegen `LIST_RECORD_ACCESS`)
- Kein `django-guardian` (Begründung in CLAUDE.md / *Permission and visibility layer*)

**Verify:** Unit-Tests decken die Audience-Kombinationen ab (öffentlich, Liste-X-Mitglieder, kein Audience).

## Phase 3b — Member-UI & Onboarding

- [x] **Ziel (Teil 1):** Eintrags-Anzeige, Record-Edit + Sichtbarkeits-Matrix, Einladungs-Flow (beide Branches), `self`-Onboarding.
- [x] **Ziel (Teil 2 / Restpunkte):** QR-Code-Onboarding + `via_associate`-Wizard.

Outputs (erledigt):
- Listen-Detail listet Einträge gefiltert durch `visible_attributes_for`
- `RecordEditForm` baut Felder dynamisch aus `ListAttribute`, schreibt verschlüsselte `ListRecordValue`-Reihen
- Sichtbarkeits-Matrix als `MultipleChoiceField` pro Attribut (Audiences: öffentlich, eigene Liste, Parent-Liste, Sub-Listen) — synchronisiert `ListRecordAccess`-Reihen beim Save (replace-semantics)
- `ListInviteToken`-Modell + Migration 0002; Klick-Handler `/invite/<token>/` mit beiden Branches:
  - `target_person_id IS NULL`: nicht-eingeloggt → in den Register-Flow mit E-Mail-Prefill; eingeloggt (Round-Trip nach Passkey-Enrollment ODER bestehender USER mit Blank-Target-Einladung) → Token an aktuelle Person binden und konsumieren
  - `target_person_id IS NOT NULL`: Auth-Check als gebundener USER; bei Falschanmeldung 403; sonst One-Click-Join
- `accounts.views._next_url_after_auth`: nach `register_passkey_finish` und `login_finish` wird ein `pending_invite_token` aus der Session zurück an `lists:invite_accept` umgeleitet
- Admin-Einladungs-View `/lists/<pk>/invite/` mit Form (E-Mail + optionale Person-Auswahl + Modus), Token-Erzeugung und Mail-Versand
- Privacy-Hinweis am E-Mail-Feld in `templates/auth/register.html` (Phase-2-Restpunkt)
- `RECORD_MANAGER`-Anlage je nach Pfad (`self_registered` für `record_create_self`, `invited` für Einladungs-Konsum)
- Tests: `RecordEditFormTests` (Werte + Visibility-Replace) und `InviteFlowTests` (alle 6 Klick-Pfade: expired, consumed, unauth-A, existing-user-B, wrong-user-B, unauth-B, round-trip-A)

Restpunkte (Phase 3b-2):
- ~~**QR-Code-Onboarding:**~~ **Erledigt 2026-05-27.** Eigenes Model `ListJoinToken` (Multi-Use, kein `target_email`/`target_person`, Default-Expiry 48 h, manuell widerrufbar). Admin-UI `/lists/<pk>/join-tokens/` mit Liste/Anlage/Revoke, QR-Render client-side via vendored `qrcodejs-1.0.0.umd.min.js` (~20 KB, IIFE, kein CDN). Klick-Handler `/join/<token>/` folgt dem M10-Pattern (GET = Bestätigungsseite, POST = consume). Self-Mode angeschlossen (idempotente Record-Anlage + `RecordManager(basis=self_registered)`); `via_associate`-Mode redirected auf `record_create_associate` (Stub bis zur Wizard-Sub-Phase). `pending_join_token` in `accounts._next_url_after_auth` für Register-Roundtrip. Tests `QRJoinTokenAdminTests` + `QRJoinClickFlowTests`.
- ~~**`via_associate`-Wizard:**~~ **Erledigt 2026-05-27.** Single-Page-`AssociateWizardForm` (Kind-Name + optionale Kind-E-Mail + Rollen-Select aus `template.relationship_roles` + dynamische Record-Felder); ein POST legt atomar Person, ListRecord (role=member, subject=Kind), PersonRelationship (subject=Kind, related=user.person, role), RecordManager (basis=guardian), ListAccess (User in Benutzergruppe) und alle ListRecordValue-Reihen an. View `record_create_associate` mit Zugang via `can_user_see_list` ODER per `wizard_grant_list_id`-Session-Marker (gesetzt vom QR-Klick-Handler für frische Visitors auf private Listen). Wizard ist von der Detail-Page erreichbar (Button „Mitglied (z. B. Kind) hinzufügen") und vom QR-Click-Handler für via_associate-Listen. Tests `AssociateWizardTests` decken Permission-Gates, Form-Behaviour, atomare Anlage, Idempotenz mehrerer Kinder und Validierungs-Rollback ab.
- ~~**Onboarding-Wizard-Tests:**~~ **Erledigt 2026-05-27.** `Phase3b2E2ETests` deckt drei E2E-Pfade ab: (1) Self-QR-Flow unauth → register-roundtrip → auth POST → Record + RecordManager, (2) Associate-QR-Flow auth → join-POST → Wizard-Redirect (mit `wizard_grant_list_id`) → Wizard-GET (private Liste sichtbar dank Session-Marker) → Wizard-POST → komplette Triade (Person/Record/Relationship/Manager/Access) + Token-Multi-Use, (3) revoked-Token blockiert beide Pfade. Zusätzlich Unit-Coverage für den `_next_url_after_auth`-Handshake (4 Fälle: nur join, nur invite, leer, beide).

**Verify (Teil 1, durchgespielt):** bestehender USER wird per `LIST_INVITE_TOKEN` in „Elternvertreter" eingeladen, klickt den Mail-Link, loggt sich per Passkey ein, landet im Record-Edit-Formular mit Namen aus seiner PERSON prefilled, speichert — keine zweite Passkey-Ceremony, keine erneute Stammdaten-Abfrage. Plus: Sichtbarkeits-Matrix einstellbar pro Feld auf {öffentlich, eigene Liste, Parent-Liste}.

## Bekannte Findings (Stand 2026-05-26)

Aus dem ersten Review der Phase 3a/3b. Kritische B1/B2/B4/B5 wurden direkt gefixt; die folgenden bleiben offen und werden in passender Phase oder als Restpunkt vor Live-Deployment angegangen.

**Architektur-Entscheidungen umgesetzt:**

- ~~B3 — Listen-Admin kann Sichtbarkeits-Matrix fremder Records umstellen.~~ **Gefixt 2026-05-26.** Sichtbarkeits-Reihen werden nur geschrieben, wenn speichernder USER `RecordManager`, subject's-own-USER oder Super-Admin ist. Listen-Admin darf weiterhin Werte korrigieren, aber Matrix-UI ist für ihn read-only (Form-Felder `disabled=True` + Server-Gate in `RecordEditForm.save()`). Architektur in CLAUDE.md / *Who may edit the visibility matrix*.

**Mittel (Hardening, vor erstem Live-Deployment fixen):**

- ~~M6 — `target_person`-Dropdown enumeriert alle USER-PERSONs system-weit.~~ **Gefixt 2026-05-26 (commit folgt).** `candidate_invite_persons(inviting_user, target_list)` in `lists/permissions.py` schränkt die Auswahl ein auf Personen, die Subject eines aktiven Records in einer Liste sind, die der Inviter sehen kann (`eligible_parents_for` ∪ target_list selbst ∪ parent ∪ direkte children). Super-Admin sieht weiterhin alle.
- ~~M7 — Email-Parameter ohne URL-Encoding in `invite_accept`-Redirect.~~ **Gefixt 2026-05-26 (commit folgt).** `urlencode()` für beide Redirects (Branch-A→register mit `email`, Branch-B→login mit `next`). Test deckt `+`-Suffix-Fall ab.
- ~~M8 — `record.subject` (Person-Name) wird für jeden Listen-Sichter direkt aus Person-Modell gerendert, unabhängig von Sichtbarkeits-Matrix.~~ **Gefixt 2026-05-26.** Subject-Name kommt als zusätzliche Achse in `LIST_RECORD_ACCESS` (Sentinel `attribute_id=NULL`), Default sichtbar, Admin+Super sehen immer Klarname, Anonymisierung zu `?N` mit ad-hoc-Numerierung pro Render. Architektur in CLAUDE.md / *Subject-name visibility*.
- ~~M9 — `lst.title` in `send_mail`-Subject ohne Newline-Sanitization.~~ **Gefixt 2026-05-26 (commit folgt).** Defense-in-depth: `ListCreateForm.clean_title` kollabiert Whitespace und cappt auf 200 Zeichen; `list_invite`-View ruft zusätzlich `_sanitize_header_value(lst.title)` vor `send_mail` auf, deckt den Fall ab dass ein Titel via Django-Admin/Direktem DB-Write am Form vorbeigeht.
- ~~M10 — `invite_accept` ist GET-Endpoint mit Side-Effects.~~ **Gefixt 2026-05-27.** GET rendert nur noch `invite_confirm.html` ohne DB- oder Session-Writes; Konsum (target_person-Bindung, RecordManager-Anlage, `consumed_at`, `pending_invite_token`-Seed) passiert ausschließlich im POST-Pfad (CSRF-geschützt). Wrong-User bleibt sofortiges 403 auf beiden Methoden. Regressions-Tests `test_m10_*` decken GET-Side-Effect-Freiheit für alle vier Pfade ab.
- ~~M11 — `record_create_self` ohne LISTTEMPLATE-Mode-Check.~~ **Gefixt 2026-05-27.** View prüft `lst.template.member_subject_mode`; bei `via_associate` → 403 mit Hinweis auf späteren Wizard (Phase 3b-2). Defense-in-depth: `detail.html` zeigt den „Mich eintragen"-Button nur noch bei `self`-Templates, sonst Info-Text. Tests `test_self_mode_*` / `test_via_associate_*` decken View + Template ab.

**Niedrig (Performance + Edge-Cases, Phase 8):**

- **N12 — `list_detail` ist N+1.** 30 Mitglieder × 8 Felder × ~5 Queries/Sicht-Check = ~1000 Queries pro Page-Load. Optimierung mit prefetch + In-Memory-Audience-Resolution.
- ~~N13 — Sichtbarkeits-Matrix-Race bei parallelen POSTs.~~ **Gefixt 2026-05-27.** `RecordEditForm.save()` ruft zu Beginn der `@transaction.atomic`-Phase `ListRecord.objects.select_for_update().get(pk=self.record.pk)` auf. Der zweite paralleler POST blockt bis zum Commit des ersten, dann läuft sein delete+insert gegen den committed Zustand — last-writer-wins statt verlorener Reihen. Test `test_n13_save_acquires_row_lock_on_record` verifiziert die `SELECT … FOR UPDATE`-Anweisung via `CaptureQueriesContext`.
- **N14 — `send_mail` ohne Error-Handling.** SMTPException → 500, Token in DB, Admin weiß nichts. Fix: try/except + Status-Message + ggf. Resend-Knopf.
- ~~N15 — `register_force` ohne Rate-Limit.~~ **Gefixt 2026-05-27.** Per-IP-Throttle auf `_create_stub_and_send` via Django-Default-Cache (`cache.incr`-Bucket, Fenster 1 h, Limit 10). Beide Pfade — `register_start` (POST) und `register_force` — sind eingehakt; XFF wird respektiert (linker-Hop = throttle key). 429-Response statt 200 bei Überschreitung, kein Person/User/Token wird geschrieben. Caveat: LocMemCache = pro-Worker-Counter (eff. Limit ≈ Workers × 10); Upgrade auf shared Cache wenn Redis später dazukommt. Tests `N15RegisterRateLimitTests` decken Limit, force-Pfad, Per-IP-Trennung und XFF-Key ab.
- **N16 — Default-Sichtbarkeit „leer" bei neuen Records.** Non-public Felder sind ohne explizite Audience-Wahl für niemanden außer Owner/Admin/Super sichtbar. UX-Frage: sollte „eigene Liste" Default sein? Architektur-Klärung offen.

## Phase 4 — Outbound Mail

- [x] **Ziel:** Mail wird an Listenmitglieder zugestellt, mit korrekten Headers und Bounce-Aliasen.

Outputs (erledigt 2026-05-27):
- Model `OutboundMessage` (Message-ID, From, Recipient, alias-token, anonymized_from, status, attempts, last_error, sent_at, list_id) — Migration 0006.
- procrastinate-Task `lists.send_outbound_message` mit `RetryStrategy(max_attempts=5, exponential_wait=60, retry_exceptions={SMTPException, ConnectionError, OSError})`. Idempotent auf bereits-SENT-Rows, terminal-fail flippt Status auf FAILED.
- Header-Generation: `Message-ID`, `From` (Original oder Alias), `List-Id` (`<email_alias.MAIL_DOMAIN>`), `List-Post`, `List-Unsubscribe` (URL auf Detail-Page, Platzhalter bis Phase 6), `Auto-Submitted: auto-generated`, `Precedence: list` — gebaut über Djangos `EmailMessage(from_email=envelope, headers={'From': visible, ...})` damit Envelope-From und sichtbares From: getrennt sind.
- Alias-Generierung via `alias_address(kind, token)` mit `kind ∈ {bounce, alias}` (`bounce-<token>@<MAIL_DOMAIN>` für Envelope-From / DSN-Routing in Phase 5b, `alias-<token>@<MAIL_DOMAIN>` für anonymisiertes From:). Token pro Row aus `secrets.token_urlsafe(16)`.
- Fan-Out `enqueue_list_fanout(list, from, subject, body, anonymize=False)` schreibt eine OutboundMessage-Row pro Empfänger (ListAccess-User mit nicht-leerer PERSON.email, dedupliziert über `distinct()` für Shared-Family-Mailbox-Fall) und deferred die SMTP-Tasks auf `transaction.on_commit` — gerollback'te Caller können keine Orphan-Tasks erzeugen.
- Super-Admin-Test-Route `/lists/<pk>/send-test/` mit `TestSendForm` (Subject + Body, M9-Sanitization). Nur `is_superuser`, archiverte Listen 403, leere Empfängerliste rendert Info statt 500. Button auf `lists/detail.html` für Super-Admins sichtbar.
- `MAIL_DOMAIN` in `settings.py` aus Env (Fallback `example.invalid` für Tests).
- Tests: `OutboundHelpersTests` (Alias-Shape, Header-Builder, Round-Trip via `EmailMessage.message()`), `OutboundFanoutTests` (Empfänger-Resolution, Dedup, on_commit-Rollback-Garantie, leere Liste, M9-Sanitization), `SendOutboundMessageTests` (Success → SENT, Idempotenz, SMTPException → PENDING + last_error, MAX_SEND_ATTEMPTS → FAILED, Retry-Then-Success), `TestSendRouteTests` (Anon → Login-Redirect, Member → 403, ListAdmin-ohne-Super → 403, Super-Admin GET + POST, archiverte Liste, leere Form-Felder).

**Verify (manuell auf VM):** `docker compose run --rm web python manage.py migrate` → Tabelle `lists_outboundmessage` existiert; Super-Admin loggt sich auf der Live-Domain ein, öffnet `/lists/<pk>/send-test/`, sendet Test-Mail; `docker compose exec db psql -U fichtelink fichtelink -c "SELECT message_id, status, sent_at FROM lists_outboundmessage ORDER BY id DESC LIMIT 5;"` zeigt SENT-Row, Mail-Empfänger inspiziert Header (Message-ID, List-Id, List-Post, List-Unsubscribe, Return-Path = `bounce-<token>@…`).

## Phase 5a — IMAP IDLE Daemon

- [x] **Ziel:** Daemon hört auf Catch-All, persistiert Inbound-Rows idempotent, enqueued Downstream-Tasks.

Outputs (erledigt 2026-05-27):
- `InboundMessage`-Model (Migration 0007) mit `(imap_uidvalidity, imap_uid)` als zusammengesetztem UNIQUE-Constraint (UID-Idempotenz nach Reconnect), `decision`-Enum (`pending|forwarded|pending_approval|rejected|suppressed|bounce|unknown_alias`), `raw_eml` als BinaryField, optionalen FKs `matched_list` (auf `List.email_alias`-Match) und `matched_outbound` (für Bounce-/Reply-Routing in Phase 5b).
- `manage.py imap_idle_daemon` (in `lists/management/commands/`) als dünner Wrapper um `lists.imap_consumer.main()` — die eigentliche async-Logik (aioimaplib-Connection, SELECT INBOX, drain-UNSEEN, IDLE-Loop mit DONE-vor-Server-Timeout, Fallback-`UID SEARCH UNSEEN`-Poll bei `wait_server_push`-Timeout, exponentielles Reconnect-Backoff `1s→…→300s`, SIGTERM-/SIGINT-Handler) lebt im Consumer-Modul, das Command bricht ab mit `CommandError` wenn `IMAP_HOST`/`IMAP_USER` fehlt.
- `lists/inbound.py` mit pure-sync Helfern: `extract_recipient_alias` scannt `Delivered-To`/`X-Original-To`/`Envelope-To`/`To`/`Cc` und pickt die erste Adresse auf `MAIL_DOMAIN`, Plus-Tag-Strip + lowercase. `resolve_alias` matched auf `List.email_alias` (case-insensitive, nur nicht-archivierte) ODER auf `bounce-<token>` / `alias-<token>` gegen `OutboundMessage.alias_token` (List gewinnt bei Token-Kollision). `parse_and_persist` schreibt die Row und deferred via `transaction.on_commit` den `lists.process_inbound`-Task; auf `IntegrityError` (UID-Dedup) gibt es `None` ohne erneutes Enqueue.
- Stub-Task `lists.process_inbound` in `lists/tasks.py` — Phase 5b ersetzt den Body durch die Decision-Pipeline.
- Settings: `IMAP_HOST`, `IMAP_PORT`, `IMAP_USER`, `IMAP_PASS`, `IMAP_USE_SSL`, `IMAP_IDLE_TIMEOUT` (default 1740 s = 29 min, vor RFC-2177-Kick), `IMAP_FALLBACK_POLL_INTERVAL` (default 300 s). Alle in `.env.example` dokumentiert.
- `docker-compose.yml`: `imap_idle`-Service ruft jetzt `python manage.py imap_idle_daemon` statt `sleep infinity`.
- Admin-Registrierung für `InboundMessage` (readonly-Felder für raw_eml/Header/UID, Filter nach decision/to_alias).
- Tests:
  - `InboundHeaderParsingTests` (Delivered-To Vorrang, Plus-Tag-Strip, Case-Insensitivity, Fallback bei Domain-Miss),
  - `ResolveAliasTests` (List-Match, archivierte Liste matched nicht, bounce-/alias-Token-Match, unknown, List gewinnt bei Token-Kollision),
  - `ParseAndPersistTests` (alle Felder gesetzt, raw_eml-Round-Trip, UID-Idempotenz returns None ohne Dedup-Enqueue, UIDVALIDITY trennt Rows, on_commit deferred process_inbound, unknown alias bleibt PENDING, fehlende Message-ID, internaldate-Default = now),
  - `ImapFetchParsingTests` (`_parse_internaldate` 2-stellig + 1-stellig + Garbage, `_parse_fetch_response` BODY nach `{N}`, `_is_new_mail_signal` erkennt EXISTS/RECENT),
  - `ImapIdleCommandTests` (CommandError ohne IMAP_HOST/IMAP_USER).

**Verify (manuell auf VM):** `docker compose run --rm web python manage.py migrate` → Tabelle `lists_inboundmessage` existiert. `docker compose up -d imap_idle && docker compose logs -f imap_idle` zeigt `IMAP connected: uidvalidity=…`. Eine Test-Mail an `5a@<MAIL_DOMAIN>` → Log-Zeile `inbound persisted: pk=… alias='5a'`, Row in `lists_inboundmessage`, korreliertes `matched_list_id`. `docker compose restart imap_idle` während die UID schon \Seen ist → kein Doppel-Insert (dedup-Pfad), Row-Count stabil.

## Phase 5b — Inbound Pipeline

- [ ] **Ziel:** End-to-end Forward (Release-Click oder Admin-Approval), Anti-Loop, Bounce-Korrelation, Retention.

Outputs:
- Suppression-Checks (Auto-Submitted, Return-Path, Precedence, In-Reply-To gegen Outbound)
- Sender-Identifikation (Set von USERs via `PERSON.email`)
- Decision-Algorithmus (member → release-link, send-permitted → forward, sonst → admin-approval)
- Release-Token + Click-Endpoint + Approval-UI
- Reply-Routing für Anonymization-Aliase
- DSN-Parser + Korrelation mit Outbound
- Periodische Tasks: EXPUNGE nach 7d, Metadaten-Pruning 30–90d, Token-Expiry

**Verify:** Mail an Liste → Release → Forward; OOO wird blockiert; Bounce wird korreliert.

## Phase 6 — Schulklassen-Lifecycle

- [ ] **Ziel:** Rollover, Klassenwechsel, Admin-Handover live nutzbar.

Outputs:
- Cohort-UI auf LIST (Edit für Super- bzw. Listen-Admin)
- Rollover-Wizard (Vorschau, Anpassen, Ausführen) mit N:1-Merge für K1 (G8 + G9)
- Atomarer Alias-Wechsel
- `PENDING_TRANSFER`-Model + Zwei-Stufen-Flow
- Selbst-Austragungs-Endpoint mit "letzter Admin"-Block
- `ADMIN_INVITE_TOKEN` + Handover-UI mit Pflicht-Auth

**Verify:** Schuljahres-Übergang im Test-Datensatz, eine PERSON von 8a nach 9b umziehen, Admin abgeben.

## Phase 7 — Aggregat-Aliase & Forms

- [ ] **Ziel:** `eltern@…`-Aliase funktionieren mit Sendezeit-Resolution; Forms-Modul nutzbar.

Outputs:
- `AGGREGATE_ALIAS`-Model + Super-Admin-UI
- Resolution-Service (PERSON_RELATIONSHIP-Query, Subtree-Scope)
- Permission-Check via LIST_SEND_PERMISSION-Graph; strengster `requires_release_click`-Resolver
- Forwarding-Fan-Out (gleiche Mechanik wie reguläre Listen)
- FORM / FORM_PART / FORM_PART_ASSET / FORM_ACCESS-Models
- Form-Rendering mit dynamischem List-Part

**Verify:** Vorsitz-Elternbeirat darf direkt senden; normaler Elternvertreter triggert Super-Admin-Approval.

## Phase 8 — Polish, Backups, erstes Live-Deployment

- [ ] **Ziel:** Live auf `fichtelink.caos.cloud`, Smoke-Tests grün.

Outputs:
- Logging-Setup, Error-Pages, leere-Listen-States
- Backup-Skript (`pg_dump` per Host-Cron, Beispiel im README)
- README-Update (jeder dokumentierte Command funktioniert)
- Erstes Deployment auf der echten VM + manueller Smoke-Test (Registrierung, Listen-Anlage, Mail-Versand, Bounce)

---

## So nutzt du diesen Plan

**Phase starten:** „Arbeite an Phase N aus `PLAN.md`." — Claude liest `CLAUDE.md`, `PLAN.md`, `filink.md` und die betroffenen App-Verzeichnisse, sonst nichts. Die Konversation davor ist nicht relevant.

**Phase abschließen:** Commit (Konvention `Phase N: <kurzbeschreibung>`), Checkbox in diesem Plan abhaken.

**Kontext wird eng innerhalb einer Phase:** Lieber zwischendurch committen und mit „Setze Phase N fort, fertig sind: …" neu starten als kompaktieren.

**Wenn eine Phase zu groß wird:** Phasen 3, 5 und 6 sind die schwersten und schon vorgeschnitten (3a/b, 5a/b). Phase 6 lässt sich notfalls in „Rollover" + „Transfer/Handover" splitten.

**Tests:** Werden phasenparallel geschrieben, nicht als separate Phase. Kritische Pfade auf jeden Fall mit Unit-Tests: Sichtbarkeits-Service (3a), Decision-Algorithmus (5b), Permission-Resolver für Aggregat-Aliase (7).

**Bewusst nicht im Plan:** Performance-Tuning, i18n (UI bleibt Deutsch hardcoded), erweiterte Anti-Spam-Heuristiken jenseits der CLAUDE.md-Regeln, Mobile-App.
