from django.core.management.base import BaseCommand, CommandError

from app.models import JITAILog, User
from app.notification_service import is_valid_expo_push_token, send_jitai_prompt


class Command(BaseCommand):
    help = "Send a test content-free JITAI push to an existing backend user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            required=True,
            help="Backend app user_id to send the test push to.",
        )
        parser.add_argument(
            "--push-token",
            type=str,
            default="",
            help="Optional Expo token to save on the user before sending.",
        )
        parser.add_argument(
            "--prompt-id",
            type=str,
            default="default",
            help="Prompt template id sent in the data payload. Defaults to 'default'.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Create the test JITAI log and print the payload without calling Expo.",
        )

    def handle(self, *args, **options):
        user_id = options["user_id"]
        push_token = options["push_token"].strip()
        prompt_id = options["prompt_id"].strip() or "default"

        try:
            user = User.objects.get(user_id=user_id)
        except User.DoesNotExist as exc:
            raise CommandError(f"User with user_id={user_id} does not exist.") from exc

        if push_token:
            if not is_valid_expo_push_token(push_token):
                raise CommandError(
                    "Invalid Expo push token. Expected ExponentPushToken[...] "
                    "or ExpoPushToken[...]."
                )
            user.push_token = push_token
            user.save(update_fields=["push_token"])
            self.stdout.write(self.style.SUCCESS(f"Saved push token for user_id={user.user_id}."))

        if not user.push_token:
            raise CommandError(
                "User has no push_token. Pass --push-token or save one on the user first."
            )

        if not is_valid_expo_push_token(user.push_token):
            raise CommandError(
                "User push_token is not a valid Expo token. "
                "Expected ExponentPushToken[...] or ExpoPushToken[...]."
            )

        jitai_log = JITAILog.objects.create(
            user=user,
            prompt_id=prompt_id,
            trigger_reason="manual test push",
            send_prompt=True,
            status="pending",
        )

        payload = {
            "type": "ema_prompt",
            "prompt_id": jitai_log.prompt_id,
            "jitai_log_id": jitai_log.pk,
        }

        self.stdout.write(f"Created JITAILog id={jitai_log.pk} for user_id={user.user_id}.")
        self.stdout.write(f"Payload: {payload}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run only. Expo was not called."))
            return

        sent = send_jitai_prompt(user, jitai_log)
        jitai_log.refresh_from_db()

        if sent:
            self.stdout.write(self.style.SUCCESS("Expo accepted the test push."))
        else:
            self.stdout.write(self.style.ERROR("Expo did not accept the test push."))

        self.stdout.write(f"JITAILog status: {jitai_log.status}")
        self.stdout.write(
            "Note: status='delivered' means Expo accepted the request, "
            "not that the phone confirmed receipt."
        )
