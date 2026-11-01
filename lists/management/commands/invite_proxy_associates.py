"""Backfill: invite the parents that were captured by the other parent before
the wizard sent invitations itself (see `lists/associate_invites.py`).

Dry-run by default: lists every associate without an account and whether it
would be invited. Pass `--send` to create the invitations and mail them.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from lists.associate_invites import (
    _record_creator,
    create_invitation,
    proxy_associates_without_account,
    send_invitation,
    skip_reason,
)
from lists.models import List


class Command(BaseCommand):
    help = (
        "Invite proxy-captured parents (associate records without an account) "
        "so they receive the list's mail. Dry-run unless --send is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--list", type=int, dest="list_id", help="Only this list (id).")
        parser.add_argument(
            "--person-id",
            type=int,
            action="append",
            dest="person_ids",
            help="Only these persons (repeatable).",
        )
        parser.add_argument(
            "--exclude-person-id",
            type=int,
            action="append",
            dest="exclude_ids",
            help="Skip these persons, e.g. likely duplicates (repeatable).",
        )
        parser.add_argument("--send", action="store_true", help="Actually invite.")

    def handle(self, *args, **options):
        list_obj = None
        if options["list_id"] is not None:
            list_obj = List.objects.filter(pk=options["list_id"]).first()
            if list_obj is None:
                raise CommandError(f"Liste {options['list_id']} gibt es nicht.")
        records = proxy_associates_without_account(list_obj)
        if options["person_ids"]:
            records = records.filter(subject_id__in=options["person_ids"])
        if options["exclude_ids"]:
            records = records.exclude(subject_id__in=options["exclude_ids"])

        invited = failed = skipped = 0
        for record in records:
            person = record.subject
            label = f"[{record.list.title}] person={person.pk} {person} <{person.email or '-'}>"
            reason = skip_reason(person)
            if reason is not None:
                skipped += 1
                self.stdout.write(f"  übersprungen  {label}: {reason}")
                continue
            if not options["send"]:
                invited += 1
                self.stdout.write(f"  würde einladen {label}")
                continue
            with transaction.atomic():
                inv = create_invitation(
                    person=person,
                    list_obj=record.list,
                    invited_by=_record_creator(person, record.list),
                )
            if inv is None:
                skipped += 1
                self.stdout.write(f"  übersprungen  {label}")
            elif send_invitation(inv):
                invited += 1
                self.stdout.write(self.style.SUCCESS(f"  eingeladen    {label}"))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  Mail-Fehler   {label}"))

        verb = "eingeladen" if options["send"] else "einzuladen"
        self.stdout.write(f"{invited} {verb}, {skipped} übersprungen, {failed} Mail-Fehler.")
        if not options["send"] and invited:
            self.stdout.write("Mit --send ausführen, um die Einladungen zu verschicken.")
