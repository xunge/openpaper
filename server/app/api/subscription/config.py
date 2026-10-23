import logging
import os
from enum import Enum

import stripe

logger = logging.getLogger(__name__)

# Billing is optional for a self-hosted install. Upstream refuses to import
# without a Stripe key; here the key is simply absent, `stripe.api_key` stays
# unset, and the subscription endpoints fail when someone calls them instead of
# the whole API failing to boot.
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")
if not STRIPE_API_KEY:
    logger.warning(
        "STRIPE_API_KEY is not set — billing and subscription endpoints are "
        "disabled. The library itself works normally."
    )

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

MONTHLY_PRICE_ID = os.getenv("STRIPE_MONTHLY_PRICE_ID")
YEARLY_PRICE_ID = os.getenv("STRIPE_YEARLY_PRICE_ID")
YOUR_DOMAIN = os.getenv("CLIENT_DOMAIN", "http://localhost:3000")

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY


class SubscriptionInterval(str, Enum):
    MONTHLY = "month"
    YEARLY = "year"


def is_valid_price_id(price_id: str) -> bool:
    """Check if the price ID is one of our configured prices."""
    return price_id in [MONTHLY_PRICE_ID, YEARLY_PRICE_ID]
