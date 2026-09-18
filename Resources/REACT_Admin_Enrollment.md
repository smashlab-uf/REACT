# Account creation and enrollment during a lab visit

Complete Garmin and Labfront setup first. Copy the participant's actual Labfront
participant ID; an email address or a `TEST-…` placeholder is not a substitute.

## New REACT account

1. On the REACT Admin homepage, select **Create account and enroll** in the
   **Participant onboarding** panel. The same shortcut is available under
   **App → Users**.
2. Enter the email, password and confirmation, birthdate, gender, and Labfront
   participant ID. First and last name are optional.
3. Select **Create account and enroll**.

The backend hashes the password, creates the account and active wearable link,
and records the first enrollment timestamp in one transaction. Enrollment starts
the participant's study clock, so save this form when the visit has reached that
step. A failure leaves no partially created account.

## Existing REACT account

Find the participant under **App → Users**, open the record, and choose
**Enroll with Labfront**. Enter or confirm the real Labfront participant ID and
save. This keeps their password, profile, and original enrollment timestamp.
An existing `TEST-…` wearable ID is replaced. An existing real ID cannot be changed
through this form; correct a mistaken mapping under **Wearable devices** first.

Duplicate email addresses and Labfront IDs already linked to another account are
rejected. If the email already exists, use the existing-account flow.

Use these forms for real participant onboarding. The older **Enroll for
notifications** shortcut remains available for testing and can create a `TEST-…`
wearable placeholder; it does not link a real Labfront participant.

## Before the participant leaves

- Have them sign into REACT and allow notifications. Confirm **Push token** is
  present in their user record; the enrollment form cannot obtain it for them.
- Have them complete a check-in and confirm it appears in admin.
- Check Garmin syncing in Labfront. Saving the wearable ID only records the link;
  automated Labfront ingestion in this backend is not yet implemented.

Staff need an active staff login, **Add user** for new accounts or **Change user**
for existing accounts, and both **Add wearable device** and **Change wearable
device**. Superusers already have these permissions.
