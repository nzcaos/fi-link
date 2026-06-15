# Projektkontext für Claude Code

## Was dieses Projekt ist
Ehrenamtlich betriebenes Eltern-Kommunikationssystem eines Schul-Fördervereins.
Details in CLAUDE.md

## Was gebaut wird
Anbindung eines **Matrix-Homeservers (Synapse)** für Klassen-Chats – ein Raum pro
Klasse, automatischer Beitritt bei Registrierung am bestehenden Server.

## Wichtigste Architekturregeln (Details: matrix-architecture.md)
- Synapse + PostgreSQL, im selben Docker-Netz wie unser Server.
- **Föderation AN** (Matrix als WhatsApp-Alternative), aber Klassenräume
  `invite`-only und `invite`-Power-Level NUR beim Server-Account.
- **Kein E2EE** – unverschlüsselte Räume, Server ist Alias-Vermittler;
  Schutz über TLS + verschlüsselte Ablage.
- Konten ausschließlich via **Synapse Admin-API** durch unseren Server anlegen;
  öffentliche Matrix-Registrierung AUS.
- Pseudonymität primär über **nichtsprechende Matrix-ID + steuerbaren
  Anzeigenamen**, nicht über Profil-Settings allein.
- **Onboarding START = Weg A** (Server vergibt Matrix-Passwort, ein Admin-API-Call).
  Weg B (MAS + eigener OIDC-Provider via pyop) ist Ausbaustufe, erst nach Pilot.
- Client-Operationen mit **matrix-nio** (ohne E2EE).
- Netzwerk: Protokoll durchweg HTTPS/TCP. Öffentlich **nur Port 443** (Reverse-Proxy
  + .well-known-Delegation); 8008/8448 bleiben intern. Details und Config-Skizze:
  deployment-network.md.

## Umsetzung
Konkreter schrittweiser Arbeitsplan für den Start (Weg A):
implementation-plan-weg-a.md. Schritt für Schritt abarbeiten, nach jedem
Schritt testen.

## Arbeitsweise
- Vor Änderungen: bestehende Projektstruktur lesen, Konventionen übernehmen.
- Neue Matrix-Logik gekapselt halten (eigenes Modul), nicht in bestehende
  Auth-/Mail-Logik verweben.
- Bei „[zu verifizieren]"-Punkten aus dem Architekturdokument: nicht raten,
  sondern als offene Annahme markieren und Rückfrage stellen.
- Secrets (Admin-Shared-Secret, Tokens) nie in Code/Repo; über Umgebung/Config.
