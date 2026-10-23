from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0054_jitailog_distress_flag_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='schedule_restarted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
