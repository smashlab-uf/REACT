from django.db import migrations, models


def clear_legacy_item_responses(apps, schema_editor):
    # Pre-redesign rows only ever stored one flat value per item_id, with no
    # sub-item distinction — not convertible to the new schema. Safe to drop:
    # no real participant data exists yet (collection hasn't started), this
    # is leftover local/synthetic test data.
    EMAItemResponse = apps.get_model('app', 'EMAItemResponse')
    EMAItemResponse.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0039_eventday'),
    ]

    operations = [
        migrations.RunPython(clear_legacy_item_responses, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='emaitemresponse',
            name='value',
        ),
        migrations.AlterField(
            model_name='emaitemresponse',
            name='item_id',
            field=models.CharField(max_length=8),
        ),
        migrations.AddField(
            model_name='emaitemresponse',
            name='sub_item_id',
            field=models.CharField(default='', max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='emaitemresponse',
            name='response_type',
            field=models.CharField(
                choices=[
                    ('likert', 'Likert'),
                    ('single_choice', 'Single choice'),
                    ('multi_choice', 'Multiple choice'),
                    ('number', 'Number'),
                    ('yes_no', 'Yes/No'),
                ],
                default='likert',
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='emaitemresponse',
            name='value_numeric',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='emaitemresponse',
            name='value_choice',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='emaitemresponse',
            name='value_choices',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AlterUniqueTogether(
            name='emaitemresponse',
            unique_together={('ema', 'sub_item_id')},
        ),
        migrations.AlterModelOptions(
            name='emaitemresponse',
            options={'ordering': ['sub_item_id']},
        ),
    ]
