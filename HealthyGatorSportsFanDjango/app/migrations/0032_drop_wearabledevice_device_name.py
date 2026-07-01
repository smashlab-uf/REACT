from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0031_strip_user_legacy_fields'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='wearabledevice',
            name='device_name',
        ),
    ]
