import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing import geo
from routing.models import FuelStation
from routing.stations import reset_index


class Command(BaseCommand):
    help = (
        "Load fuel stations from the price CSV. Rows outside the USA are skipped, "
        "repeated OPIS ids are merged (lowest price wins) and each station gets "
        "city-level coordinates from the offline ZIP gazetteer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(Path(settings.BASE_DIR) / "fuel-prices-for-be-assessment.csv"),
            help="CSV file to read (default: the file shipped with the project).",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_file():
            raise CommandError(f"File not found: {path}")

        stats = {"rows": 0, "non_us": 0, "invalid": 0, "merged": 0, "no_coordinates": 0}
        best: dict[int, dict] = {}

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f"CSV is missing columns: {', '.join(sorted(missing))}")
            for row in reader:
                stats["rows"] += 1
                state = (row["State"] or "").strip().upper()
                if state not in geo.US_STATE_CODES:
                    stats["non_us"] += 1
                    continue
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = Decimal(row["Retail Price"].strip())
                    rack = int(row["Rack ID"]) if (row["Rack ID"] or "").strip() else None
                except (ValueError, InvalidOperation):
                    stats["invalid"] += 1
                    continue
                city = " ".join((row["City"] or "").split())
                if price <= 0 or not city:
                    stats["invalid"] += 1
                    continue
                current = best.get(opis_id)
                if current is not None:
                    stats["merged"] += 1
                    if price >= current["price"]:
                        continue
                best[opis_id] = {
                    "name": " ".join(row["Truckstop Name"].split())[:255],
                    "address": " ".join((row["Address"] or "").split())[:512],
                    "city": city[:128],
                    "state": state,
                    "rack": rack,
                    "price": price,
                }

        objects = []
        for opis_id, item in best.items():
            centre = geo.city_centroid(item["city"], item["state"])
            if centre is None:
                stats["no_coordinates"] += 1
                self.stderr.write(f"No coordinates for {item['city']}, {item['state']} (station {opis_id}); skipped.")
                continue
            objects.append(FuelStation(
                opis_id=opis_id, name=item["name"], address=item["address"], city=item["city"],
                state=item["state"], rack_id=item["rack"], retail_price=item["price"],
                latitude=centre[0], longitude=centre[1],
            ))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(objects, batch_size=500)
        reset_index()

        self.stdout.write(
            f"Rows read: {stats['rows']} | outside USA: {stats['non_us']} | invalid: {stats['invalid']} | "
            f"merged duplicates: {stats['merged']} | without coordinates: {stats['no_coordinates']}"
        )
        self.stdout.write(self.style.SUCCESS(f"Stations loaded: {len(objects)}"))
