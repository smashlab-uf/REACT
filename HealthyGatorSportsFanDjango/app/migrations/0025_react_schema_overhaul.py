import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0024_jitailog'),
    ]

    operations = [
        # ── User: remove Fitbit fields, add enrollment fields ──────────────
        migrations.RemoveField(model_name='user', name='fitbit_user_id'),
        migrations.RemoveField(model_name='user', name='fitbit_access_token'),
        migrations.RemoveField(model_name='user', name='fitbit_refresh_token'),
        migrations.RemoveField(model_name='user', name='fitbit_token_expires'),
        migrations.AddField(
            model_name='user',
            name='is_enrolled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='enrolled_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ── Drop old REACT tables that need structural changes ─────────────
        migrations.DeleteModel(name='JITAILog'),
        migrations.DeleteModel(name='EMA'),
        migrations.DeleteModel(name='ActivitySummary'),
        migrations.DeleteModel(name='HeartRateSample'),
        migrations.DeleteModel(name='WearableDevice'),

        # ── WearableDevice: OneToOneField, fitabase_participant_id ─────────
        migrations.CreateModel(
            name='WearableDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('fitabase_participant_id', models.CharField(max_length=64, unique=True)),
                ('device_name', models.CharField(blank=True, max_length=100, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
        ),

        # ── HeartRateSample: FK to User, source field, indexes ─────────────
        migrations.CreateModel(
            name='HeartRateSample',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('bpm', models.PositiveSmallIntegerField()),
                ('source', models.CharField(default='garmin_fitabase', max_length=32)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
            options={
                'ordering': ['-timestamp'],
                'indexes': [models.Index(fields=['user', 'timestamp'], name='app_heartrate_user_ts_idx')],
            },
        ),

        # ── StressSample ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='StressSample',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(db_index=True)),
                ('stress_score', models.PositiveSmallIntegerField()),
                ('source', models.CharField(default='garmin_fitabase', max_length=32)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
            options={
                'ordering': ['-timestamp'],
                'indexes': [models.Index(fields=['user', 'timestamp'], name='app_stress_user_ts_idx')],
            },
        ),

        # ── SleepSummary ───────────────────────────────────────────────────
        migrations.CreateModel(
            name='SleepSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('total_minutes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('light_minutes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('deep_minutes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('rem_minutes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('awake_minutes', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('sleep_score', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('source', models.CharField(default='garmin_fitabase', max_length=32)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
            options={
                'unique_together': {('user', 'date')},
            },
        ),

        # ── EMA: prompt_id, sent_at, responded_at, status, 1-7 Likert ──────
        migrations.CreateModel(
            name='EMA',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('prompt_id', models.CharField(max_length=64)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('completed', 'Completed'), ('expired', 'Expired')],
                    default='pending',
                    max_length=16,
                )),
                ('mood', models.PositiveSmallIntegerField(
                    blank=True, null=True,
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)],
                )),
                ('stress', models.PositiveSmallIntegerField(
                    blank=True, null=True,
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)],
                )),
                ('energy', models.PositiveSmallIntegerField(
                    blank=True, null=True,
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)],
                )),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
            options={
                'ordering': ['-sent_at'],
            },
        ),

        # ── JITAILog: prompt_id, triggered_at, hr/stress at trigger ────────
        migrations.CreateModel(
            name='JITAILog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('prompt_id', models.CharField(max_length=64)),
                ('triggered_at', models.DateTimeField(auto_now_add=True)),
                ('trigger_reason', models.CharField(max_length=128)),
                ('hr_at_trigger', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('stress_at_trigger', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('delivered', 'Delivered'),
                        ('opened', 'Opened'),
                        ('interacted', 'Interacted'),
                        ('failed', 'Failed'),
                    ],
                    default='delivered',
                    max_length=16,
                )),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='app.user')),
            ],
            options={
                'ordering': ['-triggered_at'],
            },
        ),
    ]
