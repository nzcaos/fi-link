# Fichtelink — Benutzerhandbuch

Fichtelink ist ein selbstverwalteter Mailinglisten- und Kontaktdienst für
Elterngruppen, Fördervereine und Kurse. Sie verwalten Ihre Daten selbst und
entscheiden **pro Liste und pro Feld**, wer was sieht. Wer an eine Listen­adresse
schreibt, erreicht alle Mitglieder — ohne dass die einzelnen Adressen
offengelegt werden müssen.

Dieses Handbuch richtet sich an Endnutzer. Ein eigener Abschnitt am Ende
beschreibt die zusätzlichen Funktionen für Listen-Admins (z. B. Elternvertreter).

---

## Inhalt

1. [Voraussetzungen](#1-voraussetzungen)
2. [Registrieren und Anmelden](#2-registrieren-und-anmelden)
3. [Passkeys verwalten](#3-passkeys-verwalten)
4. [Die Startseite: Meine Listen & Formulare](#4-die-startseite-meine-listen--formulare)
5. [Einer Liste beitreten](#5-einer-liste-beitreten)
6. [Mein Eintrag: Daten erfassen und pflegen](#6-mein-eintrag-daten-erfassen-und-pflegen)
7. [Wer sieht was? Die Sichtbarkeit steuern](#7-wer-sieht-was-die-sichtbarkeit-steuern)
8. [E-Mail an eine Liste senden und empfangen](#8-e-mail-an-eine-liste-senden-und-empfangen)
9. [Formulare: sich für Aktionen eintragen](#9-formulare-sich-für-aktionen-eintragen)
10. [Messenger (Matrix-Klassenraum)](#10-messenger-matrix-klassenraum)
11. [Eintrag umziehen oder löschen](#11-eintrag-umziehen-oder-löschen)
12. [Konto-Wiederherstellung bei Geräteverlust](#12-konto-wiederherstellung-bei-geräteverlust)
13. [Für Listen-Admins](#13-für-listen-admins)
14. [Häufige Fragen](#14-häufige-fragen)

---

## 1. Voraussetzungen

Fichtelink nutzt **Passkeys statt Passwörter**. Sie melden sich mit Face ID,
Touch ID, Windows Hello oder einem Sicherheits-Schlüssel an — es gibt kein
Passwort, das man vergessen oder das geleakt werden könnte.

Sie brauchen ein Gerät, das Passkeys unterstützt:

- iPhone/iPad ab **iOS 16**
- Android ab **Version 9**
- aktuelles **macOS / Windows / Linux** mit modernem Browser

Außerdem läuft Fichtelink ausschließlich über **HTTPS** — das ist für Passkeys
technisch zwingend und unter der gewohnten Adresse Ihrer Installation (z. B.
`https://fichtelink.caos.cloud`) bereits eingerichtet.

> **Geräte ohne Passkey-Unterstützung** (z. B. sehr alte Windows-Rechner ohne
> Smartphone) können Fichtelink nicht nutzen. Das ist bewusst so gewählt.

---

## 2. Registrieren und Anmelden

### Konto anlegen

1. Auf der Startseite **„Registrieren"** wählen.
2. **Vorname**, **Nachname** und **E-Mail-Adresse** eingeben und
   **„Aktivierungs-Link anfordern"** klicken.
3. Sie erhalten eine E-Mail mit einem **Aktivierungs-Link**. Diesen Link öffnen.
4. Auf der Seite **„Passkey einrichten"** klicken Sie auf *„Passkey einrichten"*;
   Ihr Gerät fragt nach Face ID / Touch ID / Windows Hello / Sicherheits-Schlüssel.
   Optional können Sie dem Passkey eine **Bezeichnung** geben (z. B. „iPhone",
   „Arbeit-Laptop").
5. Fertig — Sie sind angemeldet.

> Die **E-Mail-Adresse ist Pflicht**: Fichtelink lebt von der Kommunikation per
> Mail (Listenpost, Bestätigungslinks, Einladungen). Ihre Adresse ist aber **nur
> sichtbar, wenn Sie sie pro Liste ausdrücklich freigeben** (siehe
> [Abschnitt 7](#7-wer-sieht-was-die-sichtbarkeit-steuern)).

**Sie haben eine Einladung erhalten?** Dann ist der Einladungs-Link gleichzeitig
Ihr Aktivierungs-Link — ein Klick aktiviert das Konto, richtet den Passkey ein
und trägt Sie direkt in die Liste ein. Siehe
[Abschnitt 5](#5-einer-liste-beitreten).

### Anmelden

1. **„Anmelden"** wählen.
2. Klicken Sie auf den Anmelde-Button — der Browser zeigt Ihnen die verfügbaren
   Passkeys an (beschriftet mit Ihrem Namen).
3. Den eigenen Passkey auswählen und mit Face ID / Touch ID / o. ä. bestätigen.

Eine E-Mail-Eingabe ist in der Regel **nicht** nötig — der Browser bietet Ihre
Passkeys direkt an.

> **Gemeinsame Familien-Mailadresse:** Wenn beide Elternteile dieselbe
> E-Mail-Adresse nutzen, hat jede Person ein eigenes Konto mit eigenem Passkey.
> Beim Anmelden zeigt der Browser beide Passkeys mit Namen an — jeder wählt
> seinen eigenen, auch auf einem gemeinsam genutzten Gerät.

Nach dem Anmelden landen Sie **direkt in einer Listenansicht** — es gibt keine
zusätzliche Startseite. Welche Liste angezeigt wird, ist die zuletzt geöffnete
(oder die erste Ihrer Listen).

---

## 3. Passkeys verwalten

Über das Menü oben rechts → **Mein Profil** → Passkeys (bzw. direkt
**„Passkeys"**) können Sie:

- **weitere Passkeys hinzufügen** (z. B. zusätzlich zum iPhone noch den
  Laptop). Mehrere Passkeys sind ausdrücklich erwünscht — sie sind Ihr Schutz
  gegen Geräteverlust.
- **einzelne Passkeys löschen** (z. B. bei einem abgegebenen Altgerät).

> **Tipp:** Richten Sie von Anfang an einen Passkey auf einem **zweiten Gerät**
> ein. Verlieren Sie eines, kommen Sie mit dem anderen weiterhin hinein, ohne
> einen Admin um Hilfe bitten zu müssen.

---

## 4. Die Startseite: Meine Listen & Formulare

Das Menü oben rechts (Ihr Name) führt zu **„Meine Listen & Formulare"**. Dort
finden Sie:

- alle **Listen**, in denen Sie Mitglied oder Admin sind,
- alle **Formulare**, bei denen Sie eingetragen sind,
- darunter ggf. **öffentliche Listen**, denen Sie noch nicht beigetreten sind.

Über **„Neue Liste anlegen"** erstellen Sie eine eigene Unterliste (sofern Sie
dazu berechtigt sind — die obersten Listen einer Installation richtet der
Super-Admin ein).

Über das Menü erreichen Sie außerdem jederzeit **Mein Profil** und die
**Abmelden**-Funktion. Innerhalb einer Liste gibt es oben einen
**Listen-Umschalter**, um schnell zwischen Ihren Listen zu springen.

---

## 5. Einer Liste beitreten

Es gibt drei Wege in eine Liste:

### a) Persönliche Einladung

Ein Admin lädt Sie per E-Mail ein. Sie erhalten einen Link:

- **Sie haben noch kein Konto:** Der Link führt durch Registrierung
  (E-Mail-Bestätigung + Passkey). Die E-Mail-Adresse ist vorausgefüllt, kann
  aber korrigiert werden (z. B. wenn Sie Ihre persönliche statt der geteilten
  Familienadresse möchten). Danach werden Sie automatisch in die Liste
  eingetragen.
- **Sie haben bereits ein Konto:** Ein Klick + Anmeldung mit dem Passkey, und
  Sie landen direkt im **Eintrag-Formular** mit bereits ausgefülltem Namen — nur
  noch speichern. Kein neuer Passkey, keine Daten doppelt eingeben.

### b) QR-Code

Listen ohne persönliche Einladung können einen **QR-Code** haben — z. B.
ausgehängt beim Elternabend. Sie scannen ihn, registrieren sich (falls nötig)
und treten bei. QR-Codes laufen nach kurzer Zeit ab.

### c) Öffentliche Listen

Öffentliche Listen erscheinen in der Übersicht und können direkt geöffnet
werden.

> In allen Fällen gilt: **Es wird nichts gespeichert, solange Sie nicht
> ausdrücklich bestätigen.** Eine unerwartete Einladungsseite können Sie einfach
> schließen.

---

## 6. Mein Eintrag: Daten erfassen und pflegen

Jede Person hat **pro Liste höchstens einen Eintrag**. Wie Sie sich eintragen,
hängt von der Art der Liste ab:

### Listen „über sich selbst" (z. B. VHS-Kurs, Förderverein)

- In der Liste auf **„Mich eintragen"** klicken.
- Ihre Felder ausfüllen (Name ist gesetzt, dazu z. B. Telefon, Adresse).
- Speichern. Später bearbeiten Sie ihn über **„Mein Eintrag bearbeiten"** im Menü.

### Schulklassen-Listen (Sie tragen z. B. Ihr Kind ein)

Hier ist das **Mitglied** das Kind, und Sie selbst (sowie ggf. ein zweiter
Elternteil) sind als **zugehörige Bezugspersonen** hinterlegt.

1. Im Menü **„Mitglied (z. B. Kind) hinzufügen"** wählen.
2. Den Namen des **Kindes** und dessen Felder eintragen.
3. Sich selbst als **Bezugsperson** erfassen (Name, Rolle wie „Mutter von" /
   „Vater von" / „Erziehungsberechtigte von", Telefon, Adresse, E-Mail).
4. Optional eine **zweite Bezugsperson** (zweiter Elternteil) gleich
   mit anlegen.

Die Klassenliste zeigt dann **eine Zeile pro Kind** mit den Daten des Kindes und
daneben pro Elternteil dessen Name, Adresse, Telefon und (falls freigegeben)
E-Mail — so wie die gewohnte Papier-Klassenliste, nur dass jeder selbst
bestimmt, was davon sichtbar ist.

> Der **zweite Elternteil** kann seinen Eintrag später selbst übernehmen (per
> eigener Einladung) und dann seine Sichtbarkeit selbst einstellen.

### Wer darf einen Eintrag bearbeiten?

- **Sie selbst** Ihren eigenen Eintrag.
- **Erziehungsberechtigte** den Eintrag des von ihnen erfassten Kindes.
- **Listen-Admins** dürfen Werte korrigieren (Moderation) — aber **nicht** die
  Sichtbarkeits-Einstellungen ändern. Die gehören immer der betroffenen Person.

---

## 7. Wer sieht was? Die Sichtbarkeit steuern

Im **Bearbeiten-Dialog** eines Eintrags hat jedes Feld eine Zeile
**„Sichtbar für:"** mit Auswahlkästchen. Damit entscheiden Sie **pro Feld und
pro Zielgruppe**, wer den Wert sehen darf. Zielgruppen sind z. B. „Mitglieder
dieser Liste" oder „öffentlich".

Wichtige Punkte:

- **Der Name** ist eine eigene Zeile ganz oben. Geben Sie ihn für niemanden
  frei, erscheint statt des Namens ein **`?N`** (eine Platzhalter-Nummer pro
  Listenansicht).
- **Die E-Mail-Adresse** ist standardmäßig **verborgen** (Opt-in). Sie müssen
  sie ausdrücklich freigeben, damit andere Mitglieder sie sehen.
- Manche Felder sind durch die Vorlage **immer öffentlich** — das steht dann
  direkt am Feld („Dieses Feld ist immer öffentlich").
- **Listen-Admins und Super-Admins** sehen Name und E-Mail immer — das ist für
  Moderation, Mail-Freigaben und Verwaltung nötig.

> Auch wenn Sie Ihre E-Mail-Adresse **nicht** freigeben, erreichen Sie
> Listen-Mails ganz normal — Fichtelink verschickt sie, ohne Ihre Adresse
> offenzulegen (siehe nächster Abschnitt).

---

## 8. E-Mail an eine Liste senden und empfangen

Jede Liste hat eine **eigene E-Mail-Adresse** (z. B. `5a@…`). Wer dorthin
schreibt, erreicht alle Mitglieder.

### Als Mitglied an die Liste schreiben

1. Sie schreiben ganz normal eine Mail an die Listenadresse.
2. Fichtelink sendet Ihnen eine **Bestätigungs-Mail mit einem Freigabe-Link**.
3. Erst **nach Ihrem Klick** auf diesen Link wird die Mail an die Mitglieder
   weitergeleitet.

Dieser Zwischenschritt ist der Schutz davor, dass jemand in Ihrem Namen an die
Liste schreibt. Auf der Freigabeseite können Sie zusätzlich wählen:

> ☑ **„Meine Absender-Adresse nicht anzeigen (anonymisiert weiterleiten).
> Antworten erreichen mich trotzdem."**

So bleibt Ihre Adresse verborgen, Antworten kommen aber dennoch bei Ihnen an.

### Wenn jemand schreibt, der kein Mitglied ist

Dann geht der Freigabe-Link an einen **Listen-Admin**, der entscheidet, ob die
Mail weitergeleitet wird.

### Empfangen

Listenpost landet automatisch in Ihrem Postfach. Antworten Sie wie auf jede
normale Mail — die Zustellung an den ursprünglichen Absender regelt Fichtelink.

> **Abwesenheits-/Auto-Antworten** und Bounces werden automatisch erkannt und
> **nicht** an die Liste weitergeleitet, damit keine Schleifen entstehen.

---

## 9. Formulare: sich für Aktionen eintragen

Formulare sind für **Selbst-Eintragungen zu Aktionen** gedacht — etwa
Helfer­listen oder Kuchen-Spenden für ein Schulfest. Sie bekommen meist einen
**Link** zugeschickt (oder scannen einen QR-Code) und können das Formular auch
**ohne Anmeldung ansehen**. Zum **Eintragen** ist eine Anmeldung nötig — beim
ersten Klick werden Sie durch Anmeldung/Registrierung geführt und landen danach
wieder beim Formular.

Ein Formular kann zwei Arten von Mitmach-Teilen haben:

### Positionen / Plätze (Slots)

Eine Tabelle mit Positionen und Kapazität, z. B. „Aufbau Freitag 14–15 Uhr,
2 Plätze". Sie sehen pro Position **„X/Y"** belegt und „noch N frei".

- **„Eintragen"** belegt einen freien Platz.
- **„Sichtbarkeit ändern"** stellt ein, ob Ihr Name/Ihre E-Mail anderen
  angezeigt wird.
- **„Austragen"** gibt den Platz wieder frei.

Ist Ihr Name verborgen, erscheint die Position als **„vergeben"**.

### Freitext-Beiträge

Eine offene Liste, z. B. „Wer bringt was mit?". Sie tragen einen Text ein
(z. B. „Apfelkuchen").

- **„Beitrag eintragen"** / **„Meinen Beitrag bearbeiten"** / **„Entfernen"**.
- Der **Beitragstext ist immer sichtbar**; Ihr Name erscheint als **„anonym"**,
  wenn Sie ihn verbergen.

> Die Sichtbarkeit bei Formularen ist einfacher als bei Listen: Es gibt nur
> „für alle sichtbar, die das Formular sehen" — ja oder nein. Standard: Name
> sichtbar, E-Mail verborgen.

---

## 10. Messenger (Matrix-Klassenraum)

Optional bietet eine Installation pro Klasse einen **Matrix-Chatraum** für
kurzfristige Infos (z. B. „Ausflug wegen Wetter abgesagt") — parallel zur
E-Mail.

So verbinden Sie sich (über **Mein Profil → Messenger-Zugang**):

1. Eine **Matrix-App** installieren (z. B. Element X, FluffyChat, SchildiChat).
2. In der App „Anmelden" wählen.
3. Als **Benutzername** die angezeigte **Matrix-ID** eintragen (falls nach dem
   Server gefragt: den angezeigten Servernamen eingeben).
4. Das angezeigte **Passwort** eingeben.

Danach empfangen Sie die Klassen-Nachrichten in der App. Auf dem Desktop
erleichtert ein QR-Code das Weiterreichen an Ihr Handy.

> Der Messenger ist nur aktiv, wenn die Installation und die jeweilige Liste ihn
> aktiviert haben. Ist er nicht verfügbar, sehen Sie einen entsprechenden Hinweis.

---

## 11. Eintrag umziehen oder löschen

Beides finden Sie **unten im Bearbeiten-Dialog** des Eintrags:

- **Umziehen** („In eine andere Klasse/Liste umziehen"): Stellt einen
  **Umzugs-Antrag**. Der Admin der **Zielliste** muss zustimmen. Bei
  Schulkindern ziehen die zugehörigen Bezugspersonen automatisch mit. Wird der
  Umzug angenommen, übernimmt die Zielliste Ihre Werte 1:1.
- **Löschen**: Entfernt Ihren Eintrag (nach Rückfrage). Sie können sich — und
  von Ihnen verwaltete Personen — **ohne Zustimmung** aus einer Liste austragen.

> **Ausnahme:** Würde Ihr Austritt eine Liste **ohne Admin** zurücklassen, wird
> er blockiert — vorher muss eine Nachfolge bestimmt werden (siehe
> Admin-Übergabe).

---

## 12. Konto-Wiederherstellung bei Geräteverlust

Haben Sie Ihren Passkey verloren (z. B. auf dem Smartphone oder im
Geräte-Schlüsselbund gelöscht), können Sie sich **selbst** wieder Zugang
verschaffen — ohne Admin:

1. Auf der Anmeldeseite **„Passkey verloren? Zugang wiederherstellen"** wählen
   (oder direkt `/auth/recover/`).
2. Ihre **hinterlegte E-Mail-Adresse** eingeben.
3. Sie erhalten eine Mail mit einem **Link zum Einrichten eines neuen
   Passkeys** — auf diesem oder einem anderen Gerät.
4. Link öffnen, neuen Passkey anlegen, fertig. Sie sind angemeldet.

Ihre Daten, Mitgliedschaften und Familienbeziehungen bleiben dabei vollständig
erhalten. **Bestehende Passkeys bleiben ebenfalls erhalten** — einen nicht mehr
funktionierenden (z. B. den des alten Handys) können Sie nach der Anmeldung unter
*Mein Profil → Passkeys* selbst entfernen.

> **Gemeinsame Familien-Mailadresse:** Sind unter Ihrer Adresse mehrere Konten
> registriert (z. B. beide Elternteile), enthält die Mail je einen Link pro
> Konto, mit Namen beschriftet — wählen Sie den für Ihr Konto.

> **Noch besser:** Richten Sie frühzeitig einen **zweiten Passkey** auf einem
> anderen Gerät ein. Dann melden Sie sich bei Verlust einfach dort an und legen
> einen neuen an — ganz ohne Wiederherstellungs-Mail.

Falls Sie **auch keinen Zugriff mehr auf Ihr E-Mail-Postfach** haben oder die
Wiederherstellung nicht klappt, hilft Ihr Listen-Admin (bei Schulklassen die
Elternvertreter) bzw. — in letzter Instanz — der Super-Admin weiter.

---

## 13. Für Listen-Admins

Jede Liste hat eine oder mehrere **Admins**. Wer das ist, hängt von der Art der
Liste ab:

- Bei **Schulklassen-Listen** sind das in der Regel die **Elternvertreter**.
- Bei **anderen Listen** (VHS-Kurs, Förderverein o. ä.) ist es zunächst
  **die Person, die die Liste ins Leben gerufen hat** — wer eine Liste erstellt,
  wird automatisch ihr Admin. Weitere Admins können ergänzt werden.

Admin-Funktionen erscheinen im Menü oben rechts unter **„Administration"**
(nur sichtbar, wenn Sie Admin der jeweiligen Liste sind). In der Liste sehen Sie
außerdem den Hinweis *„Sie sind Admin dieser Liste."*.

### Mitglieder verwalten

- **Mitglied einladen:** E-Mail-Adresse eingeben; optional eine bereits
  registrierte Person per Auswahl direkt adressieren. Der/die Eingeladene
  bekommt einen Link (Registrierung oder Ein-Klick-Beitritt).
- **QR-Codes:** Beitritts-QR-Codes erzeugen (z. B. für den Elternabend). Jeder
  Code läuft nach **48 Stunden** ab und kann jederzeit **widerrufen** werden.
- **Admins:** weitere Admins hinzufügen oder die Rolle übergeben
  (Admin-Übergabe). Eine Liste darf nie ganz ohne Admin zurückbleiben — vor dem
  eigenen Austritt zuerst eine Nachfolge bestimmen. Auch die Übergabe verlangt
  zwingend eine Anmeldung des Nachfolgers.

### E-Mail moderieren

- Schreibt ein **Nicht-Mitglied** an die Liste, erhalten Sie einen
  **Freigabe-Link** und entscheiden über **Weiterleiten** oder **Ablehnen**.

### Schulklassen-spezifisch

- **Kohorten-Daten:** Jahrgangsstufe, Klassenzug (a/b/c …) und Bildungsgang
  (G8/G9) pflegen — Grundlage für den Schuljahres-Übergang.
- **Umzugs-Anträge:** Anträge von Schülern, die in **Ihre** Klasse wechseln
  wollen, annehmen oder ablehnen.

> **Sichtbarkeit bleibt Sache der Betroffenen:** Als Admin dürfen Sie zwar Werte
> korrigieren, aber **nicht** die Sichtbarkeits-Einstellungen anderer Personen
> ändern. Deren Namen sehen Sie für die Moderation trotzdem immer.

### Funktionen, die dem Super-Admin vorbehalten sind

Einige Funktionen erscheinen nur beim **Super-Admin** der Installation — etwa
das Senden von Test-Mails, der **Schuljahres-Übergang (Rollover)** und die
Verwaltung von Vorlagen, obersten Listen und Aggregat-Aliasen (`eltern@…`).
Diese sind in einem eigenen Dokument beschrieben:
**[Super-Admin-Handbuch](superadmin-handbuch.md)**.

---

## 14. Häufige Fragen

**Ich habe kein Passwort bekommen — wie melde ich mich an?**
Fichtelink hat keine Passwörter. Sie melden sich mit Ihrem Passkey an (Face ID,
Touch ID, Windows Hello oder Sicherheits-Schlüssel).

**Sehen jetzt alle meine Telefonnummer / E-Mail?**
Nur, wenn Sie das Feld freigeben. Standardmäßig ist die E-Mail verborgen. Die
Sichtbarkeit stellen Sie pro Feld im Bearbeiten-Dialog ein
([Abschnitt 7](#7-wer-sieht-was-die-sichtbarkeit-steuern)).

**Ich habe mein Handy verloren / den Passkey gelöscht.**
Wenn Sie einen zweiten Passkey haben (anderes Gerät), melden Sie sich einfach
damit an und löschen den alten. Sonst fordern Sie über **„Passkey verloren?
Zugang wiederherstellen"** auf der Anmeldeseite selbst einen neuen
Einrichtungs-Link an Ihre hinterlegte E-Mail-Adresse an
([Abschnitt 12](#12-konto-wiederherstellung-bei-geräteverlust)).

**Warum muss ich meine eigene Mail an die Liste erst per Link freigeben?**
Das verhindert, dass jemand in Ihrem Namen an die Liste schreibt
([Abschnitt 8](#8-e-mail-an-eine-liste-senden-und-empfangen)).

**Kann ich aus einer Liste wieder austreten?**
Ja — im Bearbeiten-Dialog über **Löschen**. Ausnahme: Sie sind der einzige Admin
([Abschnitt 11](#11-eintrag-umziehen-oder-löschen)).

**Beide Elternteile teilen sich eine E-Mail-Adresse — geht das?**
Ja. Jeder legt ein eigenes Konto mit eigenem Passkey an; beim Anmelden wählt
jeder seinen eigenen Passkey aus, auch auf einem gemeinsamen Gerät.
