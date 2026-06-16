# Matrix Schritt 7 / Weg B + C — Plan (ruht vorerst)

> **Status: zurückgestellt.** Bewusst **nicht jetzt** umsetzen — Entscheidung über
> Weg B erst **nach echten Onboarding-Zahlen aus dem Piloten** (Weg A). Dieses
> Dokument hält den Plan fest, damit der Wiedereinstieg später ohne Re-Recherche
> möglich ist. Quellen: `docs/matrix-architecture.md` §6, `docs/matrix-implementation-plan.md`
> Phase 7, `docs/implementation-plan-weg-a.md`.

## Worum es geht

Heute läuft der Messenger über **Weg A**: Unser Server legt per Synapse-Admin-API
ein Matrix-Konto pro USER an und vergibt ein **serververgebenes Passwort**, das
Eltern in Element eintippen. Funktioniert, hat aber den Nachteil eines **zweiten
Geheimnisses** neben dem Passkey.

**Weg B** ersetzt das durch **Passkey-SSO**: Eltern melden sich am Messenger mit
demselben Passkey an wie an Fichtelink — kein Matrix-Passwort mehr. **Weg C**
(QR-Login, MSC4108) ist ein Komfort-Feature *obendrauf*, kein eigener Weg.

## Architektur von Weg B

Matrix-seitig kann „modernes" Auth nur über einen **OIDC-Authorization-Code-Flow**
angebunden werden. Daraus folgt eine dreiteilige Kette, **alle Teile bei uns**:

1. **MAS** (Matrix Authentication Service) wird vor Synapse geschaltet und
   übernimmt das Login (Synapse delegiert Auth an MAS, **MSC3861**). MAS
   akzeptiert ausschließlich **OIDC-Provider**.
2. Unser **WebAuthn/Passkey-Stack ist kein OIDC-Provider** → wir bauen eine
   **OIDC-Protokollhülle** davor. Kandidat: **pyop** (schlank, die eigentliche
   Auth bleibt bei uns); Alternativen Authlib/pyoidc.
3. **`claims_imports` in MAS** bildet die Upstream-Claims unseres Providers auf
   **Matrix-localpart + Displayname** ab — die Stelle für die
   **Pseudonymisierung an der Quelle** (nichtsprechende ID `@u-7f3a9c:…`,
   Displayname „Elternteil 5a-12" sofern keine Klarnamen-Zustimmung).

## Weg C (QR-Login)

MSC4108, baut auf OAuth-Device-Code-Grant + MAS auf. Reifegrad „sehr begrenzt /
im Fluss"; **Uhren-Sync** zwischen Client und Server ist kritisch. Erst sinnvoll,
wenn B steht.

## Bewusst offen / Risiken

- **Auslöser:** B wird erst nach echten Onboarding-Zahlen aus dem Piloten
  entschieden (nicht jetzt).
- `[zu verifizieren]`: reibungsloses Zusammenspiel **pyop/Authlib ↔ MAS inkl.
  Pseudonym-Mapping** ist nur per **Integrationstest** klärbar — das zentrale
  technische Risiko.
- **MAS wird von Element gepflegt** (nicht mehr der Foundation) → Risiko-Register.
- **Bestandskonten:** alle Weg-A-Konten existieren bereits mit Passwort →
  Koexistenz oder Migration muss entschieden werden.
- httpx aus Phase 1–6 fährt uns hier **nicht** fest (betrifft nur unseren
  Service-Account, nicht den Eltern-Login).

## Phasenschnitt (je 1 Usage-Slot)

Empfehlung: **8 Phasen** — eine Research-Phase vorweg, sechs Bau-Phasen, eine
Härtung. Jede ist für sich auf der VM testbar.

| Phase | Ziel | Fertig-Kriterium |
|---|---|---|
| **7.0 Spike & Entscheidungen** | MAS-Reife + unsere Synapse-Version auf MSC3861 prüfen, Versionen pinnen, **pyop vs. Authlib** entscheiden, **Koexistenz vs. Migration** der Weg-A-Konten festlegen. Kein Prod-Code. | Entscheidungsnotiz in `docs/`, Versionsmatrix, Go/No-Go |
| **7.1 OIDC-Provider in Django** | pyop-Hülle: Discovery, JWKS, `/authorize`, `/token`, `/userinfo`; Brücke zur bestehenden Passkey-Session; **pseudonyme `sub`-Claims** (kein Klarname). Standalone, noch ohne MAS. | Unit-Tests grün; Flow per Test-Client durchläuft |
| **7.2 MAS-Deployment** | `mas`-Service in docker-compose, Config, Secrets, eigene/geteilte DB, Apache-Routen. Noch keine Synapse-Verdrahtung. | MAS läuft gesund, spricht seine DB |
| **7.3 Synapse ↔ MAS (Happy Path)** | Synapse-Auth an MAS delegieren; MAS-Upstream = unser OIDC-Provider; `claims_imports` → pseudonyme localpart + Displayname. | **Neuer** Test-Elternteil loggt sich Element → MAS → Passkey → Synapse durch |
| **7.4 Konto-Integration & Sync** | MAS-erzeugte Identitäten mit unserer Pro-USER-Provisionierung/Pseudonym-Abbildung versöhnen; `ListAccess → Raum-Invite`-Signale weiter korrekt. | Mitgliedschafts-Sync grün mit MAS-Identitäten |
| **7.5 Migration/Cutover + UI** | „Messenger-Zugang"-Profilseite von „hier ist dein Passwort" auf „mit Passkey/QR anmelden" umstellen; Passwortvergabe ablösen; Bestandskonten migrieren/verknüpfen. | Bestands- und Neu-User loggen sich per Passkey ein |
| **7.6 Weg C (QR-Login)** | MSC4108 in MAS/Element aktivieren, Uhren-Sync prüfen, Zweitgerät-Test. | QR-Login auf zweitem Gerät funktioniert |
| **7.7 Härtung & Doku** | Föderations-Retest, Risiko-Register (MAS/Element), MAS-DB-Backup + at-rest, README/CLAUDE.md, volle Testsuite grün. | Suite grün auf VM, Doku aktuell |

**Kompaktere Varianten:**

- 7.6 + 7.7 zusammenziehen → **7 Slots**.
- 7.0 ist reine Recherche/Entscheidung und könnte bei genug Vorwissen entfallen →
  minimal **6 Slots**.
- Die kritischen, vollen Slots sind **7.1, 7.3 und 7.4** — diese **nicht** weiter
  bündeln.

## Wiedereinstieg

Trigger zum Aufnehmen: „Starte Matrix Weg B, Phase 7.0" (oder direkt die
gewünschte Phase). Voraussetzung laut Plan: belastbare Onboarding-Zahlen aus dem
Weg-A-Piloten liegen vor.
