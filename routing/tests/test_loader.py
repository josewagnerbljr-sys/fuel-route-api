import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from routing.models import FuelStation

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


def run(text):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "stations.csv"
        path.write_text(text, encoding="utf-8")
        out = StringIO()
        call_command("load_stations", path=str(path), stdout=out, stderr=StringIO())
    return out.getvalue()


class LoaderRules(TestCase):
    def test_skips_canada_merges_duplicates_and_drops_bad_rows(self):
        run(
            HEADER
            + '1,ALPHA,"I-80, EXIT 1",Chicago,IL,10,3.500\n'
            + '1,ALPHA TWO,"I-80, EXIT 1",Chicago,IL,10,3.300\n'
            + '1,ALPHA THREE,"I-80, EXIT 1",Chicago,IL,10,3.900\n'
            + "2,MAPLE,Hwy 1,Toronto,ON,11,3.100\n"
            + "3,BAD PRICE,Hwy,Dallas,TX,12,abc\n"
            + "4,ZERO,Hwy,Dallas,TX,12,0\n"
            + "5,NO ID,Hwy,Dallas,TX,12,3.1\n".replace("5,", "x,")
            + "6,NOWHERE,Hwy,Nowhereville,TX,12,3.1\n"
            + "7,  SPACED   NAME ,  Hwy ,  Dallas  ,tx,,3.250\n"
        )
        rows = {s.opis_id: s for s in FuelStation.objects.all()}
        self.assertEqual(sorted(rows), [1, 7])
        self.assertEqual(rows[1].retail_price, Decimal("3.300000"))
        self.assertEqual(rows[1].name, "ALPHA TWO")
        self.assertEqual(rows[7].name, "SPACED NAME")
        self.assertEqual(rows[7].city, "Dallas")
        self.assertEqual(rows[7].state, "TX")
        self.assertIsNone(rows[7].rack_id)
        self.assertAlmostEqual(rows[7].latitude, 32.8, delta=0.5)

    def test_reload_replaces_existing_rows(self):
        run(HEADER + "1,A,x,Dallas,TX,1,3.0\n2,B,x,Dallas,TX,1,3.1\n")
        self.assertEqual(FuelStation.objects.count(), 2)
        run(HEADER + "3,C,x,Dallas,TX,1,3.0\n")
        self.assertEqual(list(FuelStation.objects.values_list("opis_id", flat=True)), [3])

    def test_missing_file_and_columns(self):
        with self.assertRaises(CommandError):
            call_command("load_stations", path="/nonexistent.csv")
        with self.assertRaises(CommandError):
            run("a,b\n1,2\n")

    def test_bom_header_is_accepted(self):
        run("﻿" + HEADER + "1,A,x,Dallas,TX,1,3.0\n")
        self.assertEqual(FuelStation.objects.count(), 1)

    def test_shipped_csv(self):
        call_command("load_stations", verbosity=0)
        self.assertEqual(FuelStation.objects.count(), 6626)
        self.assertFalse(FuelStation.objects.exclude(state__regex=r"^[A-Z]{2}$").exists())
        self.assertFalse(FuelStation.objects.filter(latitude__isnull=True).exists())
