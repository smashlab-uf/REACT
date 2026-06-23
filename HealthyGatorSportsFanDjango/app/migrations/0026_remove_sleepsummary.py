from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0025_react_schema_overhaul'),
    ]

    operations = [
        migrations.DeleteModel(name='SleepSummary'),
    ]
