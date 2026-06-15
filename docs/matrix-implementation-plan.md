# Matrix-Integration – konkreter Implementierungsplan (Weg A, Pilot)

Konkretisierung von `docs/implementation-plan-weg-a.md` auf die *reale* Fichtelink-
Codebasis. Phasenweise abarbeiten, nach jeder Phase auf der Deploy-VM testen
(`docker compose run --rm …`); es gibt keinen lokalen Runtime-Stack.

> **Vorrangregel:** Wo die Matrix-Dokumente (`matrix.md`, `matrix-architecture.md`,
> `deployment-network.md`, `implementation-plan-weg-a.md`) `CLAUDE.md` oder den
> getroffenen Projektentscheidungen widersprechen, gilt `CLAUDE.md`/Projektwissen.
> Die Matrix-Dokumente entstanden in einem Claude.ai-Chat ohne vollständigen
> Projektkontext. Im Zweifel rückfragen, nicht raten.

## Festgezurrte Entscheidungen

| Thema | Entscheidung |
|---|---|
| `server_name` | `fichtelink.caos.cloud` (unveränderlich; IDs `@u-xxx:fichtelink.caos.cloud`) |
| Reverse-Proxy | **Bestehenden externen Apache erweitern** (Caddy-Skizze aus `deployment-network.md` verworfen) |
| Synapse-DB | **Gleiche Postgres-Instanz**, separate Datenbank `synapse` (`ENCODING UTF8 LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0`). Für den Piloten; später ohne Breaking Change zu eigenem Container migrierbar (`pg_dump | pg_restore`). |
| Client-Lib | **httpx (sync)**, gekapselt in `matrix/client.py`. Sync, weil alle Aufrufer (Views, Commands, procrastinate-Tasks) synchron sind und keine Nebenläufigkeit brauchen. Kein matrix-nio (E2EE ist AUS; Weg B/C betrifft den *Eltern*-Login, nicht unseren Service-Account). |
| Identitäten | **Echte Konten + Service-Account** (kein Application-Service-Muster) |
| E2EE | AUS |
| Föderation | AN; Klassenräume durch `invite`-only + `invite`-Power-Level=100 (nur Service-Account) dicht |

## Offene Annahmen (vor/while Umsetzung verifizieren, nicht raten)

- **A1 – Displayname-Policy:** Start mit *immer pseudonym* (`Elternteil 5a-12`); Klarname-bei-Zustimmung erst, wenn an die Namens-Sichtbarkeitslogik der Liste gekoppelt. Bewusst für den Piloten reduziert.
- **A2 – Föderation durch Apache:** `.well-known`-Delegation auf `:443` muss auch den Föderationsverkehr (`/_matrix/federation/*`) durchreichen. Mit offiziellem Federation-Tester gegen `fichtelink.caos.cloud` prüfen — nicht annehmen, dass es läuft (bekannte Stolperfalle, `matrix-architecture.md` §9).
- **A3 – Ist-Collation der App-DB:** `postgres:16-alpine` initialisiert mit musl-libc vermutlich bereits `lc_collate=C`. Per Katalog-Query verifizieren (siehe README-Ops). Hat keine Auswirkung auf die separate `synapse`-DB.
- **A4 – Service-Account-Token-Ablage:** Token wird beim Bootstrap erzeugt und verschlüsselt in einer Singleton-Zeile abgelegt (nicht in `.env`, nicht im Log).
- **A5 – Broadcast-Default:** Klassenräume starten im **Chat-Modus** (`events_default=0`); Broadcast-Only (`50`) ist pro Liste umschaltbar.

---

## Phase 0 – Synapse-Infrastruktur (Ops, kein App-Code)

Ziel: Synapse läuft, ist über den bestehenden Apache öffentlich erreichbar, Föderation grün, Testkonto in Element nutzbar.

- `synapse`-Datenbank + `synapse`-User in der bestehenden Instanz anlegen (einmalig, `TEMPLATE template0`, Collation `C`).
- Compose: Service `synapse` (`matrixdotorg/synapse`), Volume `./synapse:/data`, `depends_on: db`, **kein** öffentliches Port-Mapping nach außen — nur ein VM-internes Port, das die Firewall ausschließlich dem Apache-Host öffnet (analog `web`).
- `synapse/homeserver.yaml`: `server_name: fichtelink.caos.cloud`; Listener `8008` (`client` + `federation`, `x_forwarded: true`); `database` → `db`-Container, DB `synapse`; `enable_registration: false` + `registration_shared_secret`; Datenschutz-Settings (`require_auth_for_profile_requests`, `limit_profile_requests_to_users_who_share_rooms`, `include_profile_data_on_invite: false`).
- Apache-Vhost (Proxy-Host) erweitern: `/_matrix/*` und `/_synapse/client/*` → VM:synapse-Port; `/.well-known/matrix/server` → `{"m.server":"fichtelink.caos.cloud:443"}`; `/.well-known/matrix/client` → Homeserver-Base-URL. `/_synapse/admin/*` **nicht** öffentlich routen.
- `.env.example` + `settings.py`: `MATRIX_ENABLED`, `MATRIX_BASE_URL` (`http://synapse:8008`), `MATRIX_SERVER_NAME`, `MATRIX_ADMIN_SHARED_SECRET`.

**Test:** `curl …/_matrix/client/versions` über Apache liefert JSON; Federation-Tester grün gegen die Bare-Domain; Testkonto via Admin-API anlegen → Element-Login funktioniert.

---

## Phase 1 – `matrix`-App-Gerüst: Modelle, httpx-Client, Healthcheck, Service-Account

Ziel: gekapseltes Modul, Client-Methoden vorhanden, Health grün, Service-Account existiert.

- Neue App `matrix/` (in `INSTALLED_APPS`), entkoppelt von Auth-/Mail-Logik.
- `matrix/models.py`:
  - `MatrixAccount` (`OneToOne → User`, `matrix_user_id`, `password` via `encrypt()`, `onboarding_status` ∈ {created, invited, active}, `created_at`).
  - `MatrixRoom` (`OneToOne → List`, `room_id`, `broadcast_only` ggf. hier oder auf `List`, `created_at`).
  - `MatrixServiceAccount` (Singleton: `user_id`, `access_token` via `encrypt()`).
  - **Handgeschriebene** Migration (Konvention!), `encrypt()` aus `django_cryptography.fields`.
- `matrix/client.py` (async httpx, hinter eigener Schnittstelle): `versions()`, `generate_mac()`, `register_user()`, `create_room()`, `invite()`, `set_power_levels()`, `send_message()`, `kick()`, `ban()`. Logging ohne Secrets.
- `settings.py`: Matrix-Config + Validierung; alles no-op wenn `MATRIX_ENABLED=False` (Tests/CI).
- Management-Commands: `matrix_healthcheck`; `matrix_bootstrap_service_account` (Admin-Konto via Admin-API anlegen, Token verschlüsselt ablegen — A4).

**Test:** `matrix_healthcheck` grün gegen laufendes Synapse; Client-Unit-Tests mit gemocktem httpx.

---

## Phase 2 – Konto-Provisionierung (lazy) + Zugangsdaten-Anzeige (Plan-Schritt 1, 5)

Ziel: Eltern bekommen ein Matrix-Konto + können die Zugangsdaten abrufen.

- `matrix/service.py`: `ensure_matrix_account(user)` — idempotent; zufälliger nichtsprechender Localpart (`u-<token>`), kryptografisch zufälliges Passwort, Pseudonym-Displayname (A1); verschlüsselt ablegen; `onboarding_status`.
- procrastinate-Task `provision_matrix_account(user_id)` (in `matrix/tasks.py`, Auto-Discovery) für Retry/Transaktionalität.
- `matrix/views.py`: Zugangsdaten-Seite nach Passkey-Login — `server_name`, Matrix-User-ID, Passwort (einmalig sichtbar), **QR-Code** für Element (vendored `qrcode.js`-Muster wie bestehende Invite-QRs); bebilderte Kurzanleitung. Menü-Eintrag (`menu_user`-Slot).

**Test:** Eltern-Registrierung → `MatrixAccount`-Zeile; Zugangsdaten-Seite rendert; echter Element-Login auf Zweitgerät (vgl. Memory zu iCloud-Sync-Falle beim Passkey-Test — hier irrelevant, da Passwort-Login).

---

## Phase 3 – Klassenraum-Anlage + Power-Levels (Plan-Schritt 2)

Ziel: pro Klassenliste ein korrekt konfigurierter Raum.

- `List`-Felder `matrix_room_enabled` (bool, Default an Schulklassen-Vorlage gekoppelt) + `matrix_broadcast_only` (bool, Default False → A5). Handgeschriebene Migration.
- `service.ensure_room_for_list(list)`: `createRoom` invite-only; `m.room.history_visibility=invited`; `m.room.join_rules=invite`; `power_level_content_override`: `invite=100`, `kick=50`, `ban=50` (Service-Account=100), `events_default = 50 if broadcast_only else 0`; `name`/`topic` aus Listentitel. `room_id` in `MatrixRoom`.
- Management-Command/Admin-Action zum (Neu-)Anlegen der Räume für bestehende Klassenlisten.

**Test:** Raum angelegt; `GET …/state` zeigt korrekte `join_rules`/`history_visibility`/Power-Levels.

---

## Phase 4 – Mitgliedschafts-Sync (Plan-Schritt 3)

Ziel: Beitritt/Austritt/Transfer/Rollover spiegeln sich in der Raum-Mitgliedschaft.

- Synchronisations-Primitive über Signale auf `ListAccess`:
  - `post_save` → Task: `ensure_matrix_account` → `ensure_room_for_list` → `invite` + Power-Level (Listen-Admin=50, Mitglied=0).
  - `post_delete` → Task: `kick`.
  - Guard: nur wenn `MATRIX_ENABLED` und Liste matrix-aktiv (deckt die 5 `ListAccess.get_or_create`-Stellen in `lists/views.py` sowie Self-Removal `lists/views.py:1178` automatisch ab).
- `ListAdmin`-Änderungen → Power-Level-Sync (50 ↔ 0).
- Lifecycle-Hooks (`lists/lifecycle.py`): Klassentransfer (alt kicken, neu einladen), Rollover/Merge (gerollte Liste behält Raum, Titel/Topic aktualisieren; K1-Merge → neuer Raum, alle einladen, Quell-Räume read-only/archivieren).

**Test:** Beitritt → Einladung in Element sichtbar; Austritt → entfernt; Transfer → umgezogen; Rollover → Raumname aktualisiert.

---

## Phase 5 – Senden: Service-Account-Broadcast + Admin-Web-UI (Plan-Schritt 4)

Ziel: das eigentliche Feature — Elternvertretung kann kurzfristig die Klasse informieren.

- `service.send_to_list(list, body)` via Service-Account `room_send`.
- procrastinate-Task `send_matrix_broadcast(list_id, body)`.
- `matrix/views.py`: Admin-UI (Elternvertreter/Super-Admin) zum Verfassen+Senden einer kurzen Nachricht an den Klassenraum; im `menu_admin`-Slot der Listen-Detailseite.
- Broadcast-Modus durchsetzen: in `broadcast_only`-Räumen können Mitglieder (PL 0) nicht senden, Admins (PL 50) schon.

**Test:** Admin sendet → Nachricht bei allen Mitgliedern; Nicht-Admin im Broadcast-Modus blockiert.

---

## Phase 6 – Betrieb: Idempotenz, Ban, Reconcile, Logging, Doku (Plan-Schritt 6)

Ziel: produktionsreife Robustheit.

- Idempotenz: `M_USER_IN_USE` abfangen; Doppel-Invite/„already in room" nicht fatal; Re-Provisionierung gefahrlos.
- Ban als Notfallwerkzeug: Admin/Super-Admin-Action `POST …/ban` via Service-Account (`menu_admin`).
- Periodischer Reconcile-Task (procrastinate-Periodik): Drift `ListAccess` ↔ Raum-Mitgliedschaft reparieren (fehlende einladen).
- Logging ohne Secrets; `onboarding_status` im Django-Admin sichtbar.
- Doku: `README.md` (Federation-Tester-Schritt, `synapse`-DB-Backup + at-rest, `.env`-Keys, Katalog-Query für Collation-Check) und `CLAUDE.md` (Architektur-Abschnitt Matrix).
- Test-Suite vollständig grün auf der VM.

---

## Phase 7 – AUSBAU (separat, erst nach Pilotzahlen): Weg B/C

Bewusst **nicht** jetzt. Weg B (MAS + OIDC-Shim via pyop/Authlib vor dem bestehenden
WebAuthn-Stack → Passkey-SSO) und Weg C (QR-Login, MSC4108) betreffen den
*Eltern*-Login, nicht den Service-Account; httpx in Phase 1–6 fährt uns dort nicht
fest. Entscheidung über Weg B erst nach echten Onboarding-Zahlen aus dem Piloten
(`matrix-architecture.md` §6). `[zu verifizieren]`: Zusammenspiel pyop/Authlib ↔ MAS
inkl. Pseudonym-Mapping nur per Integrationstest klärbar.
