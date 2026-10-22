# REACT Labfront Export Import Guide

## Purpose

The `import_labfront_export` Django management command imports selected wearable
measurements from a Labfront ZIP export into the REACT backend. It reads
Labfront's metadata header, matches each export to an existing participant, and
stores supported measurements in the corresponding Django tables.

The importer does not create users, EMA records, or JITAI decision records. The
decision engine remains based on completed EMA check-ins.

## Supported Data

The command currently imports:

| Labfront export | Django table | Stored value |
| --- | --- | --- |
| `garmin-connect-daily-heart-rate` | `app_heartratesample` | Heart rate in BPM |
| `garmin-device-heart-rate` | `app_heartratesample` | Heart rate in BPM |
| `garmin-connect-stress` | `app_stresssample` | Garmin stress score |
| `garmin-device-stress` | `app_stresssample` | Garmin stress score |
| `garmin-connect-hrv-values` | `app_hrvsample` | Five-minute HRV/RMSSD value |

Garmin Connect stress values `-1` and `-2` indicate that Garmin could not
calculate a valid stress score. The importer ignores these values.

Raw BBI, enhanced BBI, sleep, activity, respiration, temperature, and other
unsupported files remain in the original export and are not written to
PostgreSQL. Preserve the original ZIP in the lab-approved storage location.

## Participant Matching

Every supported CSV contains Labfront file-level metadata. The importer checks:

1. `participantId`, matched against `WearableDevice.labfront_participant_id`.
2. `participantInsignia`, such as `RS01`, matched against `User.study_id`.

The participant must already exist in the target database. Rows that cannot be
matched are reported as `unknown_participant` and are not imported.

## Prerequisites

- Use the repository version containing
  `backend/app/management/commands/import_labfront_export.py`.
- Install the backend Python dependencies.
- Obtain the original Labfront ZIP through the lab-approved transfer process.
- Confirm that the target database contains the corresponding participant study
  IDs or Labfront participant IDs.
- Do not place Labfront exports in Git, GitHub, email, or an unapproved shared
  drive.

All commands below begin in the backend directory:

```bash
cd /path/to/REACT/backend
```

## Safe Local Test

Use a temporary SQLite database to test the command without accessing production:

```bash
export SECRET_KEY='local-test-key'
export DATABASE_URL='sqlite:////tmp/react_labfront_test.sqlite3'
python manage.py migrate --noinput
```

Create placeholder participants matching the sample export. Adjust the ID list
to match the export being tested:

```bash
python manage.py shell -c "from app.models import User, WearableDevice; ids=['RS01','RS02','RS03','RS04','RS05']; users=[User.objects.get_or_create(study_id=sid, defaults={'email':f'{sid.lower()}@react.local','first_name':'Participant','last_name':sid,'birthdate':'2000-01-01','gender':'other'})[0] for sid in ids]; [WearableDevice.objects.get_or_create(user=u, defaults={'labfront_participant_id':f'TEST-{u.study_id}'}) for u in users]"
```

## Dry Run

Always begin with `--dry-run`. This parses the export and checks participant
matching without retaining database changes. Use `--limit` during initial
testing to process only a small number of rows from each supported CSV:

```bash
python manage.py import_labfront_export "/absolute/path/to/labfront-export.zip" --dry-run --limit 10
```

Review the final summary before continuing. In particular, investigate any of
the following nonzero counters:

| Counter | Meaning |
| --- | --- |
| `unknown_participant` | No user or wearable matched the Labfront participant metadata |
| `bad_timestamp` | A timestamp could not be parsed |
| `bad_value` | A measurement value was missing or malformed |
| `missing_participant` | The file did not provide usable participant metadata |
| `missing_required_columns` | A supported file did not contain a recognized timestamp column |
| `unsupported_files` | Files intentionally ignored by the current importer |

`unsupported_files` is expected because one Labfront ZIP contains many data
types that REACT does not load into PostgreSQL.

## Limited Test Import

After the dry run succeeds, remove `--dry-run` but keep a small limit. Do this
only against a temporary or approved test database:

```bash
python manage.py import_labfront_export "/absolute/path/to/labfront-export.zip" --limit 10
```

Check the imported counts:

```bash
python manage.py shell -c "from app.models import HeartRateSample, StressSample, HRVSample, WearableSync; print({'heart_rate':HeartRateSample.objects.count(),'stress':StressSample.objects.count(),'hrv':HRVSample.objects.count(),'syncs':WearableSync.objects.count()})"
```

Run the same limited import again. A successful idempotency check reports zero
new `heart_rate_created`, `stress_created`, and `hrv_created` rows, with the
same records counted under the corresponding `*_existing` fields.

## Full Import

When validation is complete, remove `--limit`:

```bash
python manage.py import_labfront_export "/absolute/path/to/labfront-export.zip"
```

The command imports all supported rows as one database transaction. If the
command fails before completion, Django rolls back that run rather than keeping
a partial import.

## Viewing Imported Records

The records can be reviewed in Django Admin under Heart Rate Samples, Stress
Samples, and HRV Samples. They can also be queried through Django:

```bash
python manage.py shell -c "from app.models import HeartRateSample, StressSample, HRVSample; print(HeartRateSample.objects.order_by('-timestamp').values('user_id','timestamp','bpm')[:10]); print(StressSample.objects.order_by('-timestamp').values('user_id','timestamp','stress_score')[:10]); print(HRVSample.objects.order_by('-timestamp').values('user_id','timestamp','rmssd_ms')[:10])"
```

For the temporary SQLite test database, run `python manage.py dbshell` and use:

```sql
.tables
SELECT * FROM app_heartratesample LIMIT 10;
SELECT * FROM app_stresssample LIMIT 10;
SELECT * FROM app_hrvsample LIMIT 10;
```

## Production Procedure

Only a designated backend administrator should perform a production import.

1. Confirm the export belongs to the expected study and participants.
2. Confirm participant IDs exist in production.
3. Create and verify a Heroku Postgres backup.
4. Run a dry run against the intended database and review all error counters.
5. Make the ZIP available to the execution environment through a lab-approved,
   access-controlled transfer method. A Heroku one-off dyno cannot read a file
   directly from a team member's laptop.
6. Run the full import once.
7. Record the export filename, import date, operator, and summary counts in the
   study operations log.
8. Verify sample counts and participant associations after import.

Do not put the ZIP into the repository or build artifact to transfer it to
Heroku. If there is no approved secure method for placing the file in the
production execution environment, stop and coordinate the transfer process with
the lab before importing.

## Troubleshooting

### `unknown_participant`

Check the CSV metadata values `participantId` and `participantInsignia`. Confirm
that one matches either the participant's Labfront ID on `WearableDevice` or the
RS identifier on `User.study_id`.

### `SECRET_KEY setting must not be empty`

Set a local-only key before running Django commands:

```bash
export SECRET_KEY='local-test-key'
```

Production already receives this value from its environment and must continue
using the production secret.

### Database connection error

Confirm that `DATABASE_URL` points to the intended test database. Never paste a
production database URL into documentation, chat, source code, or Git.

### The second import creates rows again

Stop before importing additional files. Save the command output and report the
export filename and affected data type to the backend maintainer. Repeated
imports are expected to count matching rows as `*_existing`, not create copies.
