from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0032_drop_wearabledevice_device_name'),
    ]

    operations = [
        migrations.DeleteModel(
            name='UserData',
        ),
    ]
