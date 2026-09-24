from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("aa_intel_watcher", "0002_alter_id_bigautofield"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamkey",
            name="live_key",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="streamkey",
            name="live_session_id",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
