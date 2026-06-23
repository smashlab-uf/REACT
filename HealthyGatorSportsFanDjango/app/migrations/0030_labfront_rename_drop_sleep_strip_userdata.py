from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0029_engagementlog_phonetelemetry_sleepsummary'),
    ]

    operations = [
        # WearableDevice: fitabase → labfront
        migrations.RenameField(
            model_name='wearabledevice',
            old_name='fitabase_participant_id',
            new_name='labfront_participant_id',
        ),

        # HeartRateSample: update source default
        migrations.AlterField(
            model_name='heartratesample',
            name='source',
            field=models.CharField(default='garmin_labfront', max_length=32),
        ),

        # StressSample: update source default
        migrations.AlterField(
            model_name='stresssample',
            name='source',
            field=models.CharField(default='garmin_labfront', max_length=32),
        ),

        # UserData: remove legacy HealthyGator fields
        migrations.RemoveField(
            model_name='userdata',
            name='goal_type',
        ),
        migrations.RemoveField(
            model_name='userdata',
            name='weight_value',
        ),
        migrations.RemoveField(
            model_name='userdata',
            name='feel_better_value',
        ),

        # Drop SleepSummary table
        migrations.DeleteModel(
            name='SleepSummary',
        ),
    ]
