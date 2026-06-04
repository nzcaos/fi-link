"""Backfill: Attribut-Sichtbarkeit fuer bestehende via_associate-Eintraege.

Der AssociateWizardForm schrieb bisher die Attributwerte der Bezugspersonen
(Telefon, Adresse, ...), aber keine LIST_RECORD_ACCESS-Freigabe dafuer. Nur der
Name war per post_save-Signal oeffentlich, die E-Mail opt-in. Damit waren diese
Felder fuer jedes normale Benutzergruppen-Mitglied unsichtbar — obwohl die
Klassenliste die Papier-Klassenliste reproduzieren soll (CLAUDE.md / *List
display defaults*). Der Wizard schreibt jetzt eine Default-Freigabe an die
Benutzergruppe der Liste; diese Migration holt das fuer alle *bestehenden*
Eintraege nach (ein Image-Rebuild allein aendert bestehende Daten nicht).

Pro aktivem Eintrag auf einer via_associate-Liste wird fuer jedes Attribut der
Vorlage, dessen applies_to_role zur Rolle des Eintrags passt und das nicht
must_be_public ist, eine Reihe (record, attribute, audience=die Liste selbst)
geschrieben. Name (oeffentlich) und E-Mail (opt-in) bleiben unberuehrt.

Nur via_associate-Listen: self-Mode-Listen bleiben bewusst privacy-first
(Felder verborgen, Opt-in ueber die Matrix). Irreversibel — die erzeugten
Reihen sind nicht von spaeter manuell gesetzten Freigaben unterscheidbar, ein
Reverse wuerde diese mitloeschen.
"""
from __future__ import annotations

from django.db import migrations


def backfill_associate_visibility(apps, schema_editor):
    ListTemplate = apps.get_model("lists", "ListTemplate")
    ListAttribute = apps.get_model("lists", "ListAttribute")
    ListRecord = apps.get_model("lists", "ListRecord")
    ListRecordAccess = apps.get_model("lists", "ListRecordAccess")

    template_ids = list(
        ListTemplate.objects.filter(member_subject_mode="via_associate").values_list(
            "pk", flat=True
        )
    )
    if not template_ids:
        return

    # template_id -> {applies_to_role -> [attribute_id, ...]} (must_be_public
    # ausgenommen: die sind ohnehin immer sichtbar, brauchen keine Reihe).
    attrs_by_template: dict[int, dict[str, list[int]]] = {}
    for attr_id, tmpl_id, role in ListAttribute.objects.filter(
        template_id__in=template_ids, must_be_public=False
    ).values_list("pk", "template_id", "applies_to_role"):
        attrs_by_template.setdefault(tmpl_id, {}).setdefault(role, []).append(attr_id)

    rows = []
    records = ListRecord.objects.filter(
        list__template_id__in=template_ids, archived_at__isnull=True
    ).values_list("pk", "list_id", "list__template_id", "role")
    for record_id, list_id, tmpl_id, role in records:
        for attr_id in attrs_by_template.get(tmpl_id, {}).get(role, []):
            rows.append(
                ListRecordAccess(
                    record_id=record_id,
                    attribute_id=attr_id,
                    audience_id=list_id,
                )
            )

    if rows:
        # ignore_conflicts: bereits manuell gesetzte Freigaben (gleicher
        # record/attribute/audience) und ein zweiter Migrationslauf sind no-ops.
        ListRecordAccess.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("lists", "0011_multi_person_row_schema"),
    ]

    operations = [
        migrations.RunPython(
            backfill_associate_visibility,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
