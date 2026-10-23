import os
import logging
import atexit
from posthog import Posthog

POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY", '')
DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")

class _NoopPosthog:
    """Stand-in for the PostHog client when no API key is configured.

    Matches the no-op used by the server so a self-hosted deployment without
    PostHog logs one line about analytics being off instead of SDK warnings
    about an empty key and missing feature-flag secrets.
    """

    debug = False

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop


# Unfortunately we have to use sync_mode here because PostHog's async mode is not compatible with our Celery setup. See also: https://github.com/PostHog/posthog-python/issues/79
if POSTHOG_API_KEY:
    posthog = Posthog(POSTHOG_API_KEY, host="https://us.i.posthog.com", sync_mode=True)
else:
    posthog = _NoopPosthog()

logger = logging.getLogger(__name__)

if DEBUG:
    posthog.debug = True

# Ensure PostHog shuts down properly on exit
atexit.register(lambda: posthog.shutdown())

def track_event(event_name, distinct_id="celery", properties=None):
    if POSTHOG_API_KEY and not DEBUG:
        try:
            logger.info(f"Sending event: {event_name} for {distinct_id}")
            posthog.capture(
                distinct_id=distinct_id, event=event_name, properties=properties or {}
            )
            logger.info("Event sent successfully")
        except Exception as e:
            logger.error(f"Error sending to PostHog: {e}")
    else:
        logger.info(f"PostHog tracking disabled. Event: {event_name}, Properties: {properties}")
