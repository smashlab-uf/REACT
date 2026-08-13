# Generated manually for EMA rotating item support.

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0037_merge_jitai_prompt_fields_latency_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='ema',
            name='ema_type',
            field=models.CharField(
                choices=[
                    ('scheduled_check_in', 'Scheduled Check-in'),
                    ('post_prompt', 'Post-prompt Outcome Window'),
                    ('extra_check_in', 'Extra Check-in'),
                ],
                default='scheduled_check_in',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='ema',
            name='expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ema',
            name='outcome_window_end',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ema',
            name='outcome_window_start',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ema',
            name='source_jitai_log',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='ema_prompts',
                to='app.jitailog',
            ),
        ),
        migrations.CreateModel(
            name='EMAItemResponse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_id', models.CharField(choices=[('B1', 'B1'), ('B2', 'B2'), ('B3', 'B3'), ('B4', 'B4'), ('B5', 'B5'), ('B6', 'B6'), ('B7', 'B7'), ('B8', 'B8')], max_length=8)),
                ('value', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(7)])),
                ('ema', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='item_responses', to='app.ema')),
            ],
            options={
                'ordering': ['item_id'],
                'unique_together': {('ema', 'item_id')},
            },
        ),
    ]
