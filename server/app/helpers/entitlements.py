"""Entitlements for a deployment that does not sell subscriptions.

Every plan check in the app reads the local `subscriptions` row: an account with
none, or with an expired one, is BASIC. On the hosted product that is the right
default — it is what the upgrade flow converts. Here there is no Stripe, so
BASIC is a dead end: a fresh account would be capped on uploads, projects, and
chat credits with no way to lift the cap.

So local accounts get an active RESEARCHER subscription with an effectively
unbounded period. It is a local row, not a payment: nothing is charged and
nothing calls out.
"""

import datetime
import logging
import uuid

from app.database.crud.subscription_crud import subscription_crud
from app.database.models import Subscription, SubscriptionPlan, SubscriptionStatus
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ~100 years: far enough out that no realistic clock skew or long-running
# deployment trips the expiry check.
_SELF_HOSTED_PERIOD = datetime.timedelta(days=365 * 100)


def grant_self_hosted_entitlements(db: Session, user_id: uuid.UUID) -> None:
    """Ensure `user_id` has an active Researcher subscription. Idempotent."""
    if subscription_crud.get_by_user_id(db, user_id) is not None:
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    db.add(
        Subscription(
            id=uuid.uuid4(),
            user_id=user_id,
            plan=SubscriptionPlan.RESEARCHER.value,
            status=SubscriptionStatus.ACTIVE.value,
            current_period_start=now,
            current_period_end=now + _SELF_HOSTED_PERIOD,
            cancel_at_period_end=False,
        )
    )
    db.commit()
    logger.info("Granted self-hosted entitlements", extra={"user_id": str(user_id)})
