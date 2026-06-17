# Fichtelink — Super-Admin-Handbuch

Dieses Handbuch beschreibt die Funktionen, die **dem Super-Admin der Installation
vorbehalten** sind. In der Regel betrifft das eine **einzige Person** je
Installation. Alle übrigen Funktionen — einschließlich der Admin-Aufgaben für
Elternvertreter bzw. Listen-Ersteller — stehen im
**[Benutzerhandbuch](benutzerhandbuch.md)**.

Der Super-Admin ist die oberste Instanz: Er richtet die strukturelle Spitze der
Installation ein (Vorlagen, oberste Listen, Aggregat-Aliase), führt den
Schuljahres-Übergang aus und ist letzte Stufe der Konto-Wiederherstellung.

---

## Inhalt

1. [Wer ist Super-Admin? Erstinbetriebnahme](#1-wer-ist-super-admin-erstinbetriebnahme)
2. [Die Django-Administration](#2-die-django-administration)
3. [Vorlagen (LISTTEMPLATE)](#3-vorlagen-listtemplate)
4. [Oberste Listen anlegen](#4-oberste-listen-anlegen)
5. [Aggregat-Aliase (`eltern@…`)](#5-aggregat-aliase-eltern)
6. [Schuljahres-Übergang (Rollover)](#6-schuljahres-übergang-rollover)
7. [Test-Mail senden](#7-test-mail-senden)
8. [Formulare anlegen und klonen](#8-formulare-anlegen-und-klonen)
9. [Konto-Wiederherstellung](#9-konto-wiederherstellung)
10. [Sonderrechte gegenüber regulären Admins](#10-sonderrechte-gegenüber-regulären-admins)

---

## 1. Wer ist Super-Admin? Erstinbetriebnahme

Der erste Super-Admin wird einmalig bei der Installation per
Management-Kommando auf dem Server angelegt:

```bash
docker compose exec web python manage.py bootstrap_super_admin
```

Das Kommando fragt nach einer E-Mail-Adresse, legt eine Person + ein als
Super-Admin markiertes Konto an und versendet einen
**Passkey-Aktivierungs-Link**. Es gibt kein Passwort — die Aktivierung erfolgt
ausschließlich über den Link, identisch zum regulären Einladungsablauf.

Super-Admin-Funktionen erscheinen an den passenden Stellen automatisch, sobald
Sie als Super-Admin angemeldet sind (z. B. der Eintrag **„Schuljahres-Übergang"**
und **„Test-Mail senden"** im Admin-Menü einer Liste).

---

## 2. Die Django-Administration

Strukturelle Konfiguration läuft über die **Django-Administration** unter
**`/admin/`**. Dort verwalten Sie, was es im normalen Nutzer-UI bewusst *nicht*
gibt:

- **Vorlagen** (LISTTEMPLATE) inkl. Attribute und Rollen-Taxonomie,
- **Aggregat-Aliase** (`eltern@…`),
- **Formulare** (Aufbau aus Teilen, Slots, Bild-Assets),
- direkte Korrekturen an Datensätzen, wenn nötig.

> `/admin/` ist nur für Super-Admins gedacht. Reguläre Listen-Admins erledigen
> ihre Aufgaben vollständig über das normale UI.

---

## 3. Vorlagen (LISTTEMPLATE)

Vorlagen bestimmen, **welche Felder eine Liste hat** und **wie sie angezeigt
werden**. Sie sind **Super-Admin-only** und werden in der Django-Administration
gepflegt. Eine Vorlage legt fest:

- die **Attribute** (Text, E-Mail, Telefon, Zahl, Auswahl mit optionaler
  Nutzungsobergrenze, Checkbox, Personen-Beziehung),
- ob ein Feld **immer öffentlich** sein muss (`Must_be_public`),
- die **Rolle** eines Attributs: gehört es zum **Mitglied** (z. B. dem Kind) oder
  zur **Bezugsperson** (`applies_to_role`),
- den **Onboarding-Modus** (`member_subject_mode`): `self` (die registrierende
  Person ist selbst das Mitglied, z. B. VHS-Kurs) oder `via_associate` (sie
  meldet ein anderes Mitglied an, z. B. Schulklasse),
- die **Rollen-Taxonomie** für Beziehungen („Mutter von", „Vater von",
  „Erziehungsberechtigte von", …) — konfigurierbar **je Vorlage**,
- die **Anzeige-Voreinstellung**: welche Felder in welcher Zeile/Reihenfolge
  erscheinen (Mehr-Personen-Zeile bei Schulklassen).

> Wer Vorlagen ändert, ändert die Struktur **aller Listen**, die sie nutzen.
> Änderungen an Attributen produktiver Vorlagen mit Bedacht vornehmen.

---

## 4. Oberste Listen anlegen

**Oberste Listen** (ohne übergeordnete Liste) — z. B. `Elternbeirat`,
`Lehrerkollegium` — dürfen **nur Super-Admins** anlegen. Das Aufsetzen der
strukturellen Spitze einer Installation ist ein administrativer Akt, keine
Selbstbedienung.

Reguläre Nutzer können darunter **Unterlisten** erstellen (z. B. einzelne
Klassen) und werden deren Admin. Die Hierarchie ist ein **expliziter Baum** über
die „übergeordnete Liste":

```
Elternbeirat
├── Klasse 5a
│   ├── Klassenfeier-Planung
│   └── Umfrage: Pizza-Wahl
├── Klasse 5b
└── …
Lehrerkollegium   (eigene oberste Liste, nicht im Eltern-Baum)
```

Die Hierarchie steuert u. a. das **Senderecht**: Mitglieder einer
übergeordneten Liste dürfen automatisch an die Unterlisten senden (z. B. der
Elternbeirat an eine Klassenliste).

---

## 5. Aggregat-Aliase (`eltern@…`)

Ein **Aggregat-Alias** ist eine E-Mail-Adresse, die über **mehrere Listen
hinweg** nach Beziehungsrolle ausfächert — z. B. `eltern@<domain>` erreicht alle
Personen mit Rolle „Mutter", „Vater" oder „Erziehungsberechtigte" innerhalb
eines konfigurierten Teilbaums. Konfiguration ist **Super-Admin-only** (Django-
Administration).

Felder eines Alias:

- **`email_alias`** — z. B. `eltern@<domain>`
- **`title`** — Anzeigename
- **`scope_list_id`** — beschränkt auf den Teilbaum dieser Liste (optional)
- **`included_roles`** — die Beziehungsrollen, die einbezogen werden

Eigenschaften:

- **Keine Mitgliederliste**, nur eine Abfrage: Empfänger werden **zum Sendezeit­
  punkt** ermittelt (nicht zwischengespeichert), entlang der Beziehungsrollen
  und des Teilbaums, dedupliziert, beschränkt auf Personen mit hinterlegter
  E-Mail.
- **Senderecht** leitet sich aus dem bestehenden Senderecht-Graphen ab: Senden
  darf, wer auf **jeder** betroffenen Liste Senderecht hat (z. B. der Vorsitz
  des Elternbeirats). Andernfalls landet die Mail in der **Super-Admin-
  Freigabe** — Aggregat-Aliase haben keinen Listen-Admin, sie gehören dem
  Super-Admin.

---

## 6. Schuljahres-Übergang (Rollover)

Eine **Stapeloperation für Schulklassen**, üblicherweise in der letzten Woche
der Sommerferien. Aufruf über das Admin-Menü einer Liste →
**„Schuljahres-Übergang"** (oder `/lists/rollover/`).

Ablauf:

1. Fichtelink zeigt eine **Vorschau aller Klassenlisten** mit gesetztem
   Schulzweig (G8/G9) und der geplanten Aktion (hochstufen, `K1 → K2`,
   `K2 endet → archivieren`).
2. **Titel und E-Mail-Alias** der neuen Klasse sind editierbar — prüfen,
   anpassen.
3. **Ausführen.**

Standardregeln:

- `Na → (N+1)a` für `N ∈ {5, …, 9}` (beide Züge)
- **G8:** `10a..10d → K1` (Zusammenführung), `K1 → K2`, `K2 → archiviert`
- **G9:** `10a → 11a`, …, `11a..11d → K1`, `K1 → K2`, `K2 → archiviert`

Wichtig:

- Listen werden **nicht gelöscht** — sie behalten ihre Identität, nur Titel und
  Kohorten-Felder ändern sich. Nutzer und ihre Einträge bleiben verknüpft.
- Bei Zusammenführungen wird eine neue K1-Liste angelegt und alle Einträge der
  Quelllisten umgehängt; die Quelllisten werden **archiviert** (schreibgeschützt).
- Der **E-Mail-Alias rollt sofort mit** (`5a@…` → `6a@…`). **Keine
  Übergangsfrist:** ab Tag 1 funktioniert nur die neue Adresse, Mail an die alte
  bekommt eine Unzustellbarkeitsmeldung.

> **Die Aktion ist nicht automatisiert reversibel.** Vorher unbedingt ein
> **Backup** ziehen (siehe README → *Backups*).

Voraussetzung: Die teilnehmenden Klassen brauchen gepflegte **Kohorten-Daten**
(Jahrgang, Zug, G8/G9) — diese setzen die jeweiligen Listen-Admins über
**„Kohorten-Daten"** (siehe Benutzerhandbuch, Abschnitt 13). Ohne gesetzten
Schulzweig nimmt eine Liste nicht am Übergang teil.

---

## 7. Test-Mail senden

Über das Admin-Menü einer Liste → **„Test-Mail senden"** (`/lists/<id>/send-test/`)
versenden Sie eine Nachricht **direkt an die Benutzergruppe** der Liste — unter
Umgehung des regulären Inbound-/Freigabe-Pfads. Die Seite zeigt vorab die
**Empfängerliste** (nur Mitglieder mit hinterlegter E-Mail-Adresse). Nützlich,
um nach Einrichtung oder Konfigurationsänderung den Mailversand einer Liste zu
prüfen.

---

## 8. Formulare anlegen und klonen

Formulare (Helfer-/Spendenlisten für Aktionen) werden **ausschließlich in der
Django-Administration** aufgebaut — es gibt bewusst keinen Self-Service-Baukasten.
Die Endnutzer-Seite ist nur der Eintrage-Ablauf (siehe Benutzerhandbuch,
Abschnitt 9).

Ein Formular besteht aus **Teilen** (`FORM_PART`) in frei wählbarer Reihenfolge:

- **`html`** — statischer, vertrauenswürdig gerenderter HTML-Text mit
  Bild-Assets (Super-Admin-autorisiert),
- **`slots`** — Positionen mit Kapazität (z. B. „Aufbau Freitag, 2 Plätze"),
- **`contributions`** — offene Freitext-Beiträge (z. B. „Wer bringt was mit?").

Weitere Punkte:

- **Broadcast-Link:** Pro Formular gibt es **genau einen** Teilen-Link
  (`/forms/s/<token>/`). Er wird beim ersten Öffnen der Detailseite durch einen
  Formular-Admin automatisch erzeugt und dort zum Kopieren angezeigt. Ansehen
  braucht keine Anmeldung, Eintragen schon.
- **`is_open`:** Eintragungen schließen, ohne die Ansicht zu entfernen.
- **Klonen:** Die Admin-Aktion `Form.clone()` kopiert ein Formular für
  wiederkehrende Anlässe (jährliches Schulfest) — mit Teilen, Slots, Bild-Assets
  und Texten, aber **ohne** Eintragungen, Zugriffe und Teilen-Token; die
  Eintragung wird wieder geöffnet.

---

## 9. Konto-Wiederherstellung

Der Super-Admin ist die **letzte Stufe** der menschlich vermittelten
Wiederherstellung (siehe Benutzerhandbuch, Abschnitt 12). Hat ein Nutzer alle
Passkeys verloren und die Hierarchie ist bis zum Super-Admin eskaliert:

```bash
docker compose exec web python manage.py reset_passkeys --email <user-email>
```

Das Kommando löscht die bestehenden Passkeys und **gibt einen frischen
Enrollment-Link auf stdout aus**. Diesen Link reichen Sie dem Nutzer
**out-of-band** weiter (Telefon, persönlich, separate E-Mail). Der Nutzer öffnet
ihn und legt einen neuen Passkey an — Daten, Mitgliedschaften und
Familienbeziehungen bleiben erhalten.

> Passt die E-Mail auf **mehrere** Konten (geteiltes Familienpostfach),
> verweigert das Kommando die Ausführung und listet die Kandidaten auf —
> erneut mit `--user-id` ausführen.

Dieser Ablauf entfernt bewusst das Risiko „gekapertes Postfach = Konto­übernahme":
Ein gestohlener Mail-Zugang allein verschafft keinen Systemzugriff; ein Angreifer
müsste zusätzlich einen Menschen täuschen, der den Berechtigten kennt.

---

## 10. Sonderrechte gegenüber regulären Admins

Als Super-Admin haben Sie über die normalen Listen-Admin-Rechte hinaus:

- **Globale Sicht:** Sie sehen **Namen und E-Mail-Adressen immer**, unabhängig
  von der Sichtbarkeits-Matrix — nötig für Moderation und Wiederherstellung.
- **Umzüge und Austritte ohne Zustimmung:** Sie können Personen ohne die sonst
  nötige Zustimmung der Zielliste verschieben oder entfernen.
- **Verwaltung von Vorlagen, obersten Listen und Aggregat-Aliasen** (siehe oben).

> **Eine Grenze bleibt auch für den Super-Admin selten nötig zu überschreiten:**
> Die Sichtbarkeits-Einstellungen einer Person gehören der Person. Greifen Sie
> nur korrigierend ein, wenn es betrieblich unumgänglich ist.
