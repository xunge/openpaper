"""Create or update a local account, or rotate its password.

    docker compose -f docker-compose.dev.yml exec -T server \
        python -m app.scripts.set_password --email you@example.com --name "You"

Omit --password to be prompted twice; that keeps the password out of shell
history and the container's process list. Pass --admin to grant the admin flag.
"""

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from app.auth.passwords import hash_password, validate_new_password
from app.helpers.entitlements import grant_self_hosted_entitlements
from app.database.crud.user_crud import user as user_crud
from app.database.database import SessionLocal
from app.database.models import User


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a local account password."
    )
    parser.add_argument("--email", required=True, help="Account email (login name)")
    parser.add_argument("--name", help="Display name; defaults to the email local part")
    parser.add_argument(
        "--password",
        help="Password. Omit to be prompted (recommended: keeps it out of history)",
    )
    parser.add_argument(
        "--admin", action="store_true", help="Grant the admin flag to this account"
    )
    args = parser.parse_args()

    email = args.email.strip().lower()
    password = args.password
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1

    invalid = validate_new_password(password)
    if invalid:
        print(invalid, file=sys.stderr)
        return 1

    db = SessionLocal()
    try:
        db_user = user_crud.get_by_email(db=db, email=email)
        created = db_user is None

        if db_user is None:
            db_user = User(
                email=email,
                name=args.name or email.split("@")[0],
                # These two are required by the schema and normally come from
                # the OAuth provider; a local account names itself.
                auth_provider="local",
                provider_user_id=f"local:{email}",
                is_email_verified=True,
                is_active=True,
                is_admin=bool(args.admin),
            )
            db.add(db_user)
            db.commit()
            db.refresh(db_user)
        else:
            if args.name:
                db_user.name = args.name
            if args.admin:
                db_user.is_admin = True

        db_user.password_hash = hash_password(password)
        db.add(db_user)
        db.commit()

        # Same reasoning as registration: no billing here, so BASIC would be a
        # cap with no way out.
        grant_self_hosted_entitlements(db, db_user.id)

        print(
            f"{'Created' if created else 'Updated'} local account "
            f"{email} (id={db_user.id}, admin={bool(db_user.is_admin)})"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
