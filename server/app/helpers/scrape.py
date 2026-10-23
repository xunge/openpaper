import logging
import os

from firecrawl import FirecrawlApp

logger = logging.getLogger(__name__)

# Upstream requires this key at import time, which makes a self-hosted install
# without a Firecrawl account fail to boot over a feature used only by web
# citation recovery. Warn instead, and fail the one call that needs it.
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
if not FIRECRAWL_API_KEY:
    logger.warning(
        "FIRECRAWL_API_KEY is not set — web page scraping is disabled, so "
        "citation recovery from the open web will not run."
    )

firecrawl_app = FirecrawlApp(api_key=FIRECRAWL_API_KEY) if FIRECRAWL_API_KEY else None


def scrape_web_page(url: str) -> str:
    """
    Scrape the content of a web page using Firecrawl.

    Args:
        url (str): The URL of the web page to scrape.

    Returns:
        str: The scraped content of the web page.
    """
    if firecrawl_app is None:
        raise ValueError(
            "Web scraping is disabled: FIRECRAWL_API_KEY is not set for this deployment."
        )

    try:
        # firecrawl-py 4.x: scrape() returns a Document (with .markdown) on
        # success and raises on failure. There is no .error attribute.
        document = firecrawl_app.scrape(url, formats=["markdown"])
        if document.markdown:
            return document.markdown
        raise Exception(f"Failed to scrape {url}: no markdown content returned")
    except Exception as e:
        # Scrape failures are expected (anti-bot walls, dead links) and the
        # caller logs them at warning; don't also log at error here.
        raise Exception(f"Error scraping {url}: {str(e)}")
