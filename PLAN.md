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
- LISTTEMPLATE-CRUD im Django-Admin
- Super-Admin legt Top-Level-Listen an; normale User legen Sub-Listen unter eigenen Parents an
- Service `can_user_see_field(user, record, attribut)` mit Audience-Resolution
- django-guardian-Wiring für Per-Object-Permissions

**Verify:** Unit-Tests decken die Audience-Kombinationen ab (öffentlich, Liste-X-Mitglieder, kein Audience).

## Phase 3b — Member-UI & Onboarding

- [ ] **Ziel:** Eltern können sich via QR oder Einladung anmelden, eigenen Eintrag pflegen, Sichtbarkeit pro Feld setzen.

Outputs:
- Listen-Anzeige mit LISTTEMPLATE-Default-Layout
- Record-Edit als HTMX-Inline-Swap
- Sichtbarkeits-Matrix (Alpine: Audience × Field-Toggle)
- Onboarding-Wizard `self` und `via_associate`
- `LIST_INVITE_TOKEN`-Modell + zwei Klick-Pfade (Architektur: CLAUDE.md *Member invitation and LIST_INVITE_TOKEN*):
  - `target_person_id IS NULL`: Passkey-Enrollment + Onboarding-Wizard (E-Mail aus `target_email` vorausgefüllt, aber editierbar; Pflichtfeld; Privacy-Hinweis inline — siehe Phase-2-Restpunkt)
  - `target_person_id IS NOT NULL`: Passkey-Login-only-Pfad (kein Enrollment, kein PERSON-Datendialog) → direkt ins Record-Edit-Formular, prefilled aus PERSON + bestehenden Records des USERs in anderen Listen → Save finalisiert Beitritt
- QR-Code-Generierung (`qrcode.js` IIFE) für QR-driven Self-Onboarding
- RECORD_MANAGER-Population je nach `basis` (`creator`, `invited`, `guardian`, `self_registered`)
- Inline-Privacy-Hinweis am E-Mail-Feld im Registrierungs-Template nachziehen (Restpunkt Phase 2)

**Verify:** Eltern-Flow von Hand durchspielen (QR scannen → Kind + sich selbst anlegen → in Liste sichtbar mit erwarteten Sichtbarkeiten). Zusätzlich: bestehender USER aus Klasse 5a wird per `LIST_INVITE_TOKEN` in „Elternvertreter" eingeladen, klickt den Mail-Link, loggt sich per Passkey ein, sieht das Record-Edit-Formular mit Namen prefilled, speichert — keine zweite Passkey-Ceremony, keine erneute Stammdaten-Abfrage.

## Phase 4 — Outbound Mail

- [ ] **Ziel:** Mail wird an Listenmitglieder zugestellt, mit korrekten Headers und Bounce-Aliasen.

Outputs:
- Model `OutboundMessage` (Message-ID, From, Recipient, alias-token, sent-at, list_id)
- procrastinate-Tasks: SMTP-Submission mit Retry
- Header-Generation: `List-Id`, `List-Post`, `List-Unsubscribe`
- Alias-Generierung (`alias-<token>@<domain>`, `bounce-<token>@<domain>`)
- Fan-Out (ein Task pro Empfänger)
- Super-Admin-Test-Route `/lists/<id>/send-test/`

**Verify:** Test-Mail über echten SMTP-Server, Header-Inspektion, Outbound-Row in DB.

## Phase 5a — IMAP IDLE Daemon

- [ ] **Ziel:** Daemon hört auf Catch-All, persistiert Inbound-Rows idempotent, enqueued Downstream-Tasks.

Outputs:
- `manage.py imap_idle_daemon` mit `aioimaplib`, Reconnect-Backoff, Fallback-SEARCH-Poll
- UID-basierte Idempotenz
- Inbound-Row mit `decision=pending`
- Alias-Resolution (Liste, Aggregat-Alias, unbekannt) → Enqueue-Route

**Verify:** Mail an Catch-All → Row in DB → kein Doppel-Receive nach Reconnect.

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
