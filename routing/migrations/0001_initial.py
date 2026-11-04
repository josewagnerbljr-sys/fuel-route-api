from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="FuelStation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("opis_id", models.IntegerField(unique=True)),
                ("name", models.CharField(max_length=255)),
                ("address", models.CharField(blank=True, max_length=512)),
                ("city", models.CharField(max_length=128)),
                ("state", models.CharField(db_index=True, max_length=2)),
                ("rack_id", models.IntegerField(blank=True, null=True)),
                ("retail_price", models.DecimalField(decimal_places=6, max_digits=8)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
            ],
            options={
                "ordering": ["opis_id"],
                "indexes": [
                    models.Index(
                        fields=["state", "retail_price"],
                        name="routing_fue_state_51895b_idx",
                    )
                ],
            },
        ),
    ]
