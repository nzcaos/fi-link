# Umsetzungsplan – Weg A (Onboarding mit serververgebenem Passwort)

> Arbeitsauftrag für Claude Code. Schrittweise abarbeiten, nach jedem Schritt
> testen. Architektur-Hintergrund: docs/matrix-architecture.md.
> Alle API-Details unten sind [belegt] (Synapse-Admin-Doku, matrix-nio-Doku).

## Ziel
Wenn ein Elternteil sich am bestehenden Python-Server (PassKey) registriert und
einer Klasse zugeordnet wird, soll automatisch:
1. ein Matrix-Konto angelegt werden,
2. mit pseudonymem Anzeigenamen,
3. das Elternteil in den Klassenraum eingeladen werden,
4. die Power-Level korrekt gesetzt sein,
5. dem Elternteil die Matrix-Zugangsdaten (für Element) bereitgestellt werden.

## Grundprinzipien
- Neue Logik in EIGENES Modul kapseln (z.B. `matrix/`), nicht in Auth-/Mail-Logik verweben.
- Synapse nur intern erreichbar: `MATRIX_BASE_URL=http://synapse:8008`.
- Secrets aus Umgebung: `MATRIX_ADMIN_SHARED_SECRET`. Nie ins Repo.
- Bei `[zu verifizieren]`-Stellen: nicht raten, als Annahme markieren, rückfragen.

---

## Schritt 0 – Modulgerüst & Konfiguration
- Modul `matrix/` anlegen mit Client-Klasse (httpx async oder matrix-nio).
- Config laden: `base_url`, `server_name`, `admin_shared_secret`.
- Health-Check: `GET {base_url}/_matrix/client/versions` muss antworten.
- **Test:** Container hochfahren, Health-Check grün.

---

## Schritt 1 – Konto anlegen via Admin-API  [belegt]
Zweistufig: Nonce holen, dann registrieren.

```python
import hmac, hashlib, httpx

def generate_mac(shared_secret, nonce, user, password, admin=False, user_type=None):
    # Reihenfolge & NUL-Trennung exakt nach Synapse-Doku
    mac = hmac.new(key=shared_secret.encode("utf8"), digestmod=hashlib.sha1)
    mac.update(nonce.encode("utf8")); mac.update(b"\x00")
    mac.update(user.encode("utf8")); mac.update(b"\x00")
    mac.update(password.encode("utf8")); mac.update(b"\x00")
    mac.update(b"admin" if admin else b"notadmin")
    if user_type:
        mac.update(b"\x00"); mac.update(user_type.encode("utf8"))
    return mac.hexdigest()

async def create_account(base_url, shared_secret, username, password, displayname):
    async with httpx.AsyncClient() as c:
        nonce = (await c.get(f"{base_url}/_synapse/admin/v1/register")).json()["nonce"]
        mac = generate_mac(shared_secret, nonce, username, password, admin=False)
        r = await c.post(f"{base_url}/_synapse/admin/v1/register", json={
            "nonce": nonce,
            "username": username,
            "displayname": displayname,   # pseudonym, z.B. "Elternteil 5a-12"
            "password": password,
            "admin": False,
            "mac": mac,
        })
        r.raise_for_status()
        return r.json()  # access_token, user_id, home_server, device_id
```

Regeln für username/displayname (siehe §4 Architektur):
- `username` = nichtsprechend, z.B. zufälliges Token → ergibt `@u-7f3a9c:server_name`.
- `displayname` = Klarname nur bei Zustimmung, sonst Pseudonym.
- `password` = kryptografisch zufällig generieren, in eurer DB dem Elternteil
  zuordnen (verschlüsselt ablegen, wie übrige personenbezogene Daten).
- `admin=False` für Eltern. Nur der Service-Account ist Admin.

**Test:** Konto anlegen, mit Element gegen euren Server einloggen.

---

## Schritt 2 – Klassenraum anlegen (einmalig pro Klasse)  [zu verifizieren: exakte Params]
Über die Client-Server-API als Service-Account (`POST /_matrix/client/v3/createRoom`).
Wichtige Parameter (gegen aktuelle Spec verifizieren):
- `preset`: privater Raum (invite-only).
- `name`/`topic`: z.B. "Klasse 5a".
- `power_level_content_override`:
  - `invite`: 100  → nur Service-Account darf einladen.
  - `events_default`: 0 (Chat-Modus) ODER 50 (Broadcast-Modus).
  - `kick`/`ban`: 50+ beim Service-Account.
- `initial_state`:
  - `m.room.history_visibility` = `invited` (oder `joined`).
  - `m.room.join_rules` = `invite`.

Raum-ID (`!...:server_name`) in eurer DB der Klasse zuordnen.

**Test:** Raum anlegen, Power-Level/Join-Rule/History-Visibility per
`GET .../state` prüfen.

---

## Schritt 3 – Elternteil in Klassenraum einladen  [belegt: matrix-nio room_send analog]
Als Service-Account: `POST /_matrix/client/v3/rooms/{roomId}/invite` mit
`{"user_id": "@u-7f3a9c:server_name"}`.
- Nur der Service-Account hat `invite`-Recht (Schritt 2) → Eltern können das nicht.
- Optional: Auto-Join serverseitig, sonst tritt das Elternteil beim ersten
  App-Login selbst bei (Einladung sichtbar).

**Test:** Einladung sichtbar in Element des Test-Elternteils; Beitritt möglich.

---

## Schritt 4 – Nachricht senden (Verteil-/Testpfad)  [belegt]
matrix-nio:
```python
from nio import AsyncClient
client = AsyncClient(base_url, service_user_id)
await client.login(service_password)
await client.room_send(
    room_id="!abc:server_name",
    message_type="m.room.message",
    content={"msgtype": "m.text", "body": "Bus 5a: 45 Min Verspätung."},
)
await client.close()
```

**Test:** Nachricht erscheint bei allen Mitgliedern; Broadcast-Modus prüfen,
dass normale Eltern nicht senden können (falls so konfiguriert).

---

## Schritt 5 – Zugangsdaten an Eltern übergeben
- Im Web-Frontend (nach PassKey-Login) anzeigen: server_name, Matrix-User-ID,
  Matrix-Passwort – als Text UND QR-Code für Element.
- Bebilderte Kurzanleitung: Element installieren → "eigener Server" → Daten.
- Onboarding-Status je Elternteil in DB führen (angelegt / eingeladen / aktiv).

**Test:** Onboarding mit echtem zweitem Gerät vollständig durchspielen.

---

## Schritt 6 – Fehlerfälle & Betrieb
- Idempotenz: erneutes Anlegen vorhandener Konten abfangen (Username-Kollision).
- Ban als Notfall (`POST .../ban`) beim Service-Account.
- Logging ohne Secrets (kein Passwort/Token in Logs).
- Federation-Tester grün (siehe deployment-network.md) BEVOR echte Eltern starten.

---

## Was bewusst NICHT in Weg A enthalten ist
- Kein PassKey-SSO / kein QR-Login über MAS (das ist Weg B, Ausbaustufe).
- Kein E2EE.
- Kein Application-Service-Muster (erst falls viele virtuelle Identitäten nötig).

## Definition of Done (Pilot)
Ein Elternteil kann sich registrieren, bekommt automatisch Matrix-Konto +
Klassenraum-Einladung, verbindet Element per angezeigten Zugangsdaten, empfängt
und (im Chat-Modus) sendet Nachrichten. Service-Account kann an die Klasse
senden. Föderation getestet. Keine Secrets im Repo/Log.
