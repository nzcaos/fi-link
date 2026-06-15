# Matrix-Integration – Architekturentscheidungen

Referenzdokument für die Anbindung eines Matrix-Homeservers an das bestehende
Eltern-Kommunikationssystem (Python + PostgreSQL, PassKey/WebAuthn, HTTPS).

Ziel: Eltern einer Schulklasse schnell und datenschutzkonform informieren –
parallel zur bestehenden E-Mail-Lösung (virtuelle Klassenadresse `5a@<domain>`),
und perspektivisch als echte WhatsApp-Alternative auf Matrix-Basis.

> Status der Aussagen: Mit „[belegt]" markierte Punkte sind durch offizielle
> Doku/Spec abgesichert. Mit „[zu verifizieren]" markierte Punkte beruhen auf
> allgemeinem Architekturwissen und müssen vor Umsetzung geprüft werden.

---

## 1. Grundsatzentscheidungen

| Thema | Entscheidung | Begründung |
|---|---|---|
| Homeserver | **Synapse** | Python + PostgreSQL, passt zum bestehenden Stack; Referenzimplementierung [belegt] |
| Datenbank | PostgreSQL (nicht SQLite) | SQLite ist laut Doku nicht produktionstauglich [belegt] |
| Föderation | **AN** | Eltern sollen Matrix als echte WhatsApp-Alternative nutzen und auch außerhalb der Klassenräume kommunizieren können |
| Verschlüsselung (E2EE) | **AUS** (unverschlüsselte Räume) | Server muss als Alias-Vermittler mitlesen; konsistent mit E-Mail-Modell; E2EE+Automatisierung beißen sich (siehe §5) |
| Konto-Anlage | Ausschließlich durch unseren Server via Admin-API | Öffentliche Registrierung am Matrix-Server bleibt AUS |
| Raum-Modell | Ein `invite`-only-Raum pro Klasse | Mitgliedschaft = Sendeberechtigung |
| Onboarding (Start) | **Weg A**: Server vergibt Matrix-Passwort | Sofort lauffähig, ein API-Call [belegt] |
| Onboarding (Ausbau) | **Weg B**: MAS + eigener OIDC-Provider | PassKey-SSO + QR-Login; erst nach Pilotphase entscheiden |

---

## 2. Räume und Berechtigungen

### Raum pro Klasse
- Join-Rule: `invite` — nur wer `invite`-Status hat, darf beitreten [belegt].
  Schützt zuverlässig auch gegen föderierte Fremde (harte Auth-Regel, nicht nur Sichtbarkeit).
- History-Visibility: `invited` oder `joined` (nicht `shared`), damit später
  beitretende Eltern nicht den gesamten Altverlauf sehen [belegt].

### Power Levels (`m.room.power_levels`) [belegt]
- `events_default`:
  - **Klassen-Chat-Modus**: `0` → alle Mitglieder dürfen senden.
  - **Broadcast-Modus** (nur Elternvertretung): `50` → nur Accounts mit Level ≥ 50 senden.
- `invite`: **exklusiv beim Server-Account (Level 100)**.
  → Eltern können KEINE Fremden hereinholen. Dies ist der eigentliche Zugangsschutz.
- `kick`/`ban`: beim Server-Account. Ban als Notfallwerkzeug; wird über die
  Matrix-ID durchgesetzt [belegt].

### Was NICHT als Schutz taugt
- `allow_profile_lookup_over_federation`: betrifft nur Sichtbarkeit von
  Profildaten (Name/Avatar), NICHT Zugangskontrolle. Sitzt zudem auf dem Server
  des Fremden, nicht bei uns. Für unser Schutzziel irrelevant [belegt].

---

## 3. Föderation absichern (trotz „AN")

- Föderation bleibt aktiv, damit Eltern frei mit der Matrix-Welt kommunizieren.
- Klassenräume bleiben durch `invite`-only + `invite`-Power-Level dicht.
- Föderations-Port (8448) im Docker-Setup **nicht** nach außen mappen, falls
  reine Klassen-Nutzung; sonst bewusst öffnen. Doku empfiehlt zusätzlich
  Firewall-Absicherung des Föderations-Listeners [belegt].

---

## 4. Datenschutz / Pseudonymität

Anforderung: Kontaktdaten sind innerhalb der Klasse sichtbar (Normalfall), aber
Eltern können sich gegen Veröffentlichung entscheiden → dann Alias.

### Primärer Schutz (in unserer Hand)
- **Nichtsprechende Matrix-IDs** für ALLE vergeben, z.B. `@u-7f3a9c:<domain>`
  (Klarname NICHT in die ID kodieren – IDs sind unveränderlich).
- **Anzeigename** steuert Sichtbarkeit: Klarname nur bei Zustimmung, sonst
  Pseudonym („Elternteil 5a-12"). Anzeigename ist änderbar, ID nicht.
- Die strukturierte Kontaktliste (Adresse/Telefon) bleibt im bestehenden System
  (verschlüsselte PostgreSQL), NICHT in Matrix-Profilen.

### Ergänzender Schutz (Defense-in-Depth, Synapse-Settings) [belegt]
- `require_auth_for_profile_requests: true`
- `limit_profile_requests_to_users_who_share_rooms: true`
- `include_profile_data_on_invite: false`
  → schließt die Lücke, dass Profildaten sonst im Invite-Event mitwandern.
- Achtung: Settings 1–2 greifen nur über die Client-Server-API; über Föderation
  können fremde Server Profildaten ggf. weiterhin abrufen. Daher ist der
  Anzeigename-an-der-Quelle-Ansatz der eigentliche Schutz.

---

## 5. Warum kein E2EE

- Matrix-E2EE (Megolm) ist gerätebezogen; ein automatisierter Service müsste
  Schlüssel/Sessions/Geräteverifizierung verwalten.
- Appservice-Verschlüsselung (MSC3202) ist laut Synapse „nicht empfohlen",
  experimentell, und bietet ohnehin keinen Schutz gegen den Betreiber
  (Service kann entschlüsseln) [belegt].
- Unser Server muss als Alias-Vermittler ohnehin mitlesen → E2EE wäre
  inkonsistent. Schutz erfolgt über TLS (Transport) + verschlüsselte Ablage (at rest).
- `matrix-nio` funktioniert ohne E2EE „out of the box"; E2EE nur via
  `matrix-nio[e2e]` mit Krypto-Store [belegt].

---

## 6. Onboarding-Wege

### Weg A – Server vergibt Passwort (START / Prototyp) [belegt]
- `POST /_synapse/admin/v1/register` mit Nonce (`GET` zuvor), Username,
  Displayname, Passwort, Admin-Flag, HMAC-Digest.
- Antwort enthält `access_token`, `user_id`, `home_server`, `device_id`.
- Displayname direkt mitsetzbar → Pseudonymisierung an der Quelle.
- Funktioniert auch bei deaktivierter Server-Registrierung.
- Admin-API (`/_synapse/admin/...`) nur intern erreichbar (Docker-Netz /
  Reverse-Proxy), niemals öffentlich [belegt].
- Eltern erhalten Zugangsdaten (Text + QR) zur Eingabe in Element.
- Nachteil: zweites Geheimnis neben dem PassKey.

### Weg B – MAS + eigener OIDC-Provider (AUSBAU)
- Voraussetzung für PassKey-SSO UND QR-Login (MSC4108).
- MAS akzeptiert nur OIDC-Provider mit Authorization-Code-Flow [belegt].
- Unser WebAuthn-Stack (selbst gebaut) ist KEIN OIDC-Provider → Protokollhülle
  ergänzen, Kandidaten: **pyop** (schlank, Auth bleibt bei uns), Authlib, pyoidc [belegt].
- `claims_imports` in MAS bildet Upstream-Claims → Matrix-localpart/Displayname
  (Stelle für Pseudonymisierung) [belegt].
- MAS wird von Element gepflegt (nicht mehr Foundation) → Risiko-Register [belegt].
- Aufwand = 3 Teile, alle bei uns: OIDC-Schicht bauen + MAS betreiben + sicher verbinden.
- Entscheidung über B erst nach echten Onboarding-Zahlen aus dem Piloten.
- [zu verifizieren] Reibungsloses Zusammenspiel unseres pyop/Authlib-Providers
  mit MAS inkl. Pseudonym-Mapping – nur per Integrationstest klärbar.

### Weg C – QR-Login (Teil von B)
- MSC4108, baut auf OAuth Device-Code-Grant + MAS auf.
- Reifegrad: „sehr begrenzt", im Fluss; Uhren-Sync zwischen Client/Server kritisch [belegt].
- Kein eigener Weg, sondern Komfort-Feature auf B.

### NICHT geeignet: „Login als Nutzer"-Admin-Endpunkt
- Erzeugt kein Gerät, soll vom Nutzer unbemerkt bleiben, ist für Support gedacht –
  kein sauberes Geräte-Onboarding [belegt].

---

## 7. Werkzeuge

- **matrix-nio** (async Python) für Client-Operationen (Login, `room_send`,
  Raum anlegen). Sende-Call:
  `client.room_send(room_id, "m.room.message", {"msgtype":"m.text","body":...})` [belegt].
- Konto-/Raum-Verwaltung via Synapse Admin-API.
- Application-Service-Muster nur falls sehr viele „virtuelle" Identitäten ohne
  echte Konten nötig werden. Bei überschaubarer Eltern-/Klassenzahl genügt
  „echte Konten + nio-Client" [zu verifizieren – AS vs. Client final entscheiden].

---

## 8. Konzeptioneller Unterschied zum E-Mail-Modell

- E-Mail: Server prüft JEDE Nachricht (Freigabe-Link gegen Spoofing).
- Matrix: Server prüft EINMALIG die Mitgliedschaft, danach Vertrauen.
- Robust gegen Außenstehende; kein Pro-Nachricht-Filter gegen ein berechtigtes
  Mitglied. Gegenmittel: Moderation / Kick / Ban / Broadcast-Modus.
- Bewusst so übernehmen.

---

## 9. Netzwerk, Protokolle und Ports

Protokoll durchweg **HTTPS über TCP** – keine UDP-/Sonderprotokolle. Zwei
logisch getrennte Verkehrsarten, die NICHT denselben Port nutzen [belegt]:
- **Client-Server**: Eltern-Apps (Element) ↔ unser Server (eigentlicher Messenger-Verkehr).
- **Föderation**: unser Server ↔ andere Matrix-Server.

### Synapse-interne Ports (nicht öffentlich)
- `8008` HTTP, hinter Reverse-Proxy: Client- und Admin-API [belegt].
- `8448` HTTPS: klassischer Föderations-Port [belegt].
- Beide bleiben im internen Docker-Netz. Unser Python-Server spricht Synapse
  intern über `http://synapse:8008` an (Client- + Admin-API), nie öffentlich.

### Öffentliche Firewall – gewählte Variante: nur 443 via Delegation
- **Nur `443/TCP`** nach außen öffnen (Reverse-Proxy, reguläres Zertifikat).
- Föderation ebenfalls über 443 mittels **`.well-known`-Delegation**:
  `https://<server_name>/.well-known/matrix/server` liefert
  `{"m.server":"<synapse-host>:443"}` [belegt]. Datei liegt auf Port 443.
- `8008`/`8448` NICHT nach außen mappen.
- Reverse-Proxy für Client- UND Föderationsverkehr empfohlen (Synapse muss dann
  kein TLS selbst behandeln) [belegt].
- Zusätzlich `/.well-known/matrix/client` ausliefern (App-Autokonfiguration).

### Alternative (falls keine Delegation): getrennte Ports
- `443/TCP` (Client, → 8008) **und** `8448/TCP` (Föderation) öffentlich öffnen [belegt].

### Stolperfalle
- Föderation nach Setup mit offiziellem Federation-Tester prüfen. Bekannt:
  trotz korrekter `.well-known` auf 443 kann Föderation scheitern, bis 8448
  korrekt behandelt/weitergeleitet ist – nicht annehmen, dass es läuft [belegt].
- `server_name` (z.B. `example.com`) bestimmt die User-ID-Form `@user:example.com`
  und ist unveränderlich gewählt – vor Inbetriebnahme festlegen.

---

## 10. Offene Punkte vor/während Umsetzung

1. AS-Muster vs. „echte Konten + nio-Client" final entscheiden.
2. Synapse-Config konkret: Föderations-Whitelist, Registrierung aus,
   PostgreSQL-Anbindung, Docker-Compose.
3. Raum-Anlage mit korrekten `join_rules` / `history_visibility` / Power-Levels
   in einem Schritt.
4. (Falls B) PassKey-Stack als OIDC-Provider mit pyop + MAS-Integrationstest.
5. „at rest"-Verschlüsselung der Synapse-DB auf unser Niveau bringen.
