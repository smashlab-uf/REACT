import json

from django.core.management.base import BaseCommand, CommandError

from app.ema_catalog import EMA_ITEM_BANK
from dashboard.data.config import ITEM_BANK_VERSION
from dashboard.data.item_bank import item_bank_path, serialize_item_bank


class Command(BaseCommand):
    help = (
        'Freeze EMA_ITEM_BANK to a committed JSON file so monitoring '
        'completeness is scored against a fixed item set. Run with --check in '
        'CI to catch an edit to the catalog that was never frozen.'
    )

    def add_arguments(self, parser):
        # Not --version: BaseCommand already owns that flag.
        parser.add_argument(
            '--bank-version',
            type=str,
            default=ITEM_BANK_VERSION,
            help=f'Item-bank version to write (default: {ITEM_BANK_VERSION}).',
        )
        parser.add_argument(
            '--check',
            action='store_true',
            help='Exit non-zero if the committed file differs from EMA_ITEM_BANK.',
        )

    def handle(self, *args, **options):
        version = options['bank_version']
        path = item_bank_path(version)
        payload = json.dumps(
            serialize_item_bank(EMA_ITEM_BANK, version),
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
        ) + '\n'

        if options['check']:
            if not path.exists():
                raise CommandError(
                    f'{path} does not exist — run "manage.py dump_item_bank" and commit it.'
                )
            if path.read_text(encoding='utf-8') != payload:
                raise CommandError(
                    f'{path} is stale. EMA_ITEM_BANK has changed since it was frozen. '
                    f'Either re-run "manage.py dump_item_bank" if {version} is not yet in '
                    f'the field, or cut a new version with --bank-version and bump '
                    f'ITEM_BANK_VERSION.'
                )
            self.stdout.write(self.style.SUCCESS(f'{path.name} matches EMA_ITEM_BANK.'))
            return

        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding='utf-8')

        sub_items = sum(len(item['sub_items']) for item in EMA_ITEM_BANK.values())
        verb = 'Updated' if existed else 'Wrote'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {path} — {len(EMA_ITEM_BANK)} items, {sub_items} sub-items.'
        ))
        if existed:
            self.stdout.write(self.style.WARNING(
                f'Overwrote an existing {version}. If {version} has already scored data in '
                f'the field, cut a new version instead and bump ITEM_BANK_VERSION.'
            ))
