"""
Simplified LLM client for metadata extraction.
"""
import json
import logging
import os
import re
import asyncio
import random
import httpx
from dotenv import load_dotenv

load_dotenv()

from src.observability import configure_langfuse

configure_langfuse()

from typing import Any, Dict, List, Optional, Type, TypeVar, Callable

# This worker used the Google GenAI SDK directly: native PDF parts, explicit
# context caching, and `response_schema`. DeepSeek is OpenAI-compatible but
# offers none of those, so the client below talks chat-completions instead and
# keeps the parts that are provider-independent (prompts, retries, Pydantic
# validation, the parse-then-repair loop).
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from pydantic import BaseModel, ValidationError, create_model, Field, ConfigDict

from src.prompts import EXTRACT_COLS_INSTRUCTION, SYSTEM_INSTRUCTIONS_CACHE, EXTRACT_METADATA_PROMPT_TEMPLATE
from src.schemas import (
    DataTableRow,
    PaperMetadataExtraction,
    TitleAuthorsAbstract,
    InstitutionsKeywords,
    SummaryAndCitations,
    Highlights,
    CellEntry,
    DataTableCellValue,
    ResponseCitation,
)
from src.utils import retry_llm_operation, time_it

logger = logging.getLogger(__name__)


def _format_api_error(e: Exception) -> Any:
    """Pull the concise error payload out of an API exception for logging.

    Some SDK exceptions stringify as the entire response body, which overwhelms
    the logs. When the exception exposes a dict on `.details`/`.body`, return its
    'error' entry; otherwise (a timeout, or no such field) fall back to the whole
    exception.
    """
    details = getattr(e, "details", None)
    if isinstance(details, dict):
        return details.get("error", e)
    return e


# HTTP status codes worth another attempt. Everything else the API returns as a
# 4xx is deterministic — a 400 INVALID_ARGUMENT for an oversized prompt, a 403
# for a bad key, a 404 for an expired cache — and re-sending the identical
# request just burns quota and wall-clock before failing the same way.
RETRYABLE_STATUS_CODES = frozenset({408, 429})


def _is_retryable(e: Exception) -> bool:
    """Whether re-sending the identical request could plausibly succeed.

    Transport-level failures (timeouts) and server-side errors are transient.
    Client errors are not, except for request timeout and rate limiting.
    """
    if isinstance(e, httpx.TimeoutException):
        return True
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(e, RateLimitError):
        return True
    if isinstance(e, APIError):
        status = getattr(e, "status_code", None)
        if status is None:
            return True
        return status in RETRYABLE_STATUS_CODES or status >= 500
    return False


# Constants
DEFAULT_CHAT_MODEL = os.getenv("DEEPSEEK_DEFAULT_MODEL", "deepseek-chat")
FAST_CHAT_MODEL = os.getenv("DEEPSEEK_FAST_MODEL", DEFAULT_CHAT_MODEL)
CACHE_TTL_SECONDS = 3600

# Gemini rejects caches below 1024 tokens. Below this many characters the content
# is likely under that floor, so caching can't help — skip it and inline instead
# of making a doomed caches.create call. ~5000 chars (~1024 tokens even for sparse
# text at ~5 chars/token) keeps us safely above the floor before we bother caching.
CACHE_MIN_CONTENT_CHARS = 5000

# Pydantic model type variable
T = TypeVar("T", bound=BaseModel)


class JSONParser:

    @staticmethod
    def validate_and_extract_json(json_data: str) -> dict:
        """Extract and validate JSON data from various formats"""
        if not json_data or not isinstance(json_data, str):
            raise ValueError("Invalid input: empty or non-string data")

        json_data = json_data.strip()

        # Case 1: Try parsing directly first
        try:
            return json.loads(json_data)
        except json.JSONDecodeError:
            pass

        # Case 2: Check for code block format
        if "```" in json_data:
            code_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", json_data)

            for block in code_blocks:
                block = block.strip()
                block = re.sub(r"}\s+\w+\s+}", "}}", block)
                block = re.sub(r"}\s+\w+\s+,", "},", block)

                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    continue

        raise ValueError(
            "Could not extract valid JSON from the provided string. "
            "Please ensure the response contains proper JSON format."
        )


class AsyncLLMClient:
    """
    A simple LLM client for metadata extraction.
    This is a placeholder implementation that would need to be replaced
    with actual LLM API calls (OpenAI, Anthropic, Google, etc.)
    """

    DEFAULT_TIMEOUT = 90_000  # 90s for text/cached operations
    PDF_TIMEOUT = 120_000    # 120s for PDF file operations

    def __init__(
        self,
        api_key: str,
        default_model: Optional[str] = None,
    ):
        self.api_key = api_key
        self.default_model: str = default_model or DEFAULT_CHAT_MODEL
        # The OpenAI SDK needs an explicit base URL; unlike the GenAI SDK
        # there is no built-in notion of "the DeepSeek endpoint".
        self.base_url: str = os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )

    def _create_client(self, timeout: int = DEFAULT_TIMEOUT) -> AsyncOpenAI:
        """Create a fresh client instance for thread-safe concurrent calls."""
        if not self.api_key:
            raise ValueError("API key is not set")
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            # The callers below pass milliseconds; the SDK wants seconds.
            timeout=timeout / 1000,
            # Retries live in the loops below, which can also re-ask with a
            # corrective instruction; letting the SDK retry underneath them
            # would multiply the worst-case wall clock.
            max_retries=0,
        )

    async def _close_client(self, client: AsyncOpenAI) -> None:
        """Close the async transport before the task's event loop goes away.

        The SDK schedules aclose() at GC time, which in a Celery worker lands on
        a *later* task's loop and dies with "Event loop is closed". Closing
        explicitly keeps cleanup on the loop that owns the connections.
        """
        try:
            await client.close()
        except Exception as e:
            logger.debug(f"Error closing LLM client: {e}")

    async def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        """Approximate input token count for `text`.

        The Gemini endpoint this replaced advertised an exact countTokens call;
        DeepSeek has no equivalent, so this is a character estimate. Three
        characters per token is pessimistic for prose and about right for dense
        paper text (tables, math, CJK) — the direction callers want, since it
        makes them truncate slightly sooner rather than overflow the window.
        """
        if not text:
            return 0
        return max(1, len(text) // 3)

    async def create_cache(self, cache_content: str, client: AsyncOpenAI, model: Optional[str] = None) -> Optional[str]:
        """No-op: DeepSeek caches prompt prefixes automatically.

        Upstream created an explicit Gemini cache here so the paper text and the
        system instructions were billed once across four extraction calls. That
        API has no DeepSeek equivalent, and asking for one would fail the whole
        extraction, so this returns None. Callers treat None as "inline the
        content in the prompt", which is exactly the fallback they already had
        for papers below the cache floor.
        """
        logger.debug(
            "Skipping explicit cache creation: DeepSeek caches prompt prefixes automatically"
        )
        return None

    async def create_file_cache(
        self,
        file_path: str,
        client: AsyncOpenAI,
        system_instructions: Optional[str] = None,
    ) -> Optional[str]:
        """No-op, for the same reason as `create_cache`."""
        logger.debug("Skipping file cache creation: unsupported for DeepSeek")
        return None

    def _build_messages(
        self, prompt: str, file_path: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """System instructions plus the user turn, paper text inlined.

        Gemini used to carry `SYSTEM_INSTRUCTIONS_CACHE` inside the cache entry
        it created; with no cache the instructions have to travel with every
        request or the extraction loses them. A PDF given by path is reduced to
        extracted text on the way in, because this provider accepts no file
        parts.
        """
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": SYSTEM_INSTRUCTIONS_CACHE}
        ]

        content = prompt
        if file_path:
            # Imported here: src.parser pulls in the S3 service, and this module
            # is imported by tasks that must not drag that in at module load.
            from src.parser import extract_text_from_pdf

            document_text = extract_text_from_pdf(file_path)
            content = (
                f'<document filename="{os.path.basename(file_path)}">\n'
                f"{document_text}\n</document>\n\n{prompt}"
            )

        messages.append({"role": "user", "content": content})
        return messages

    @staticmethod
    def _schema_instruction(schema: Type[BaseModel]) -> str:
        """The schema, spelled out for endpoints without strict schema mode."""
        return (
            "\n\nRespond with a single JSON object matching this schema — "
            "no prose, no markdown code fences, and properly escape any quotes "
            "inside string values:\n"
            + json.dumps(schema.model_json_schema())
        )

    async def generate_content(
        self,
        prompt: str,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
        cache_key: Optional[str] = None,
        model: Optional[str] = None,
        schema: Optional[Type[BaseModel]] = None,
        file_path: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        client: Optional[AsyncOpenAI] = None,
    ) -> str:
        """
        Generate content using the LLM with automatic retry and exponential backoff.

        Args:
            prompt: The prompt to send to the LLM
            model: Optional specific model to use, defaults to self.default_model
            max_retries: Maximum number of retry attempts (default: 3)
            base_delay: Base delay in seconds for exponential backoff (default: 1.0)
            client: Optional client to use (for concurrent calls)

        Returns:
            str: The generated content from the LLM
        """
        if not client:
            raise ValueError("Client is required for generate_content")

        if not model:
            model = self.default_model

        if image_bytes:
            logger.warning(
                "Image input is not supported by this LLM backend; the image is "
                "omitted from the request"
            )

        messages = self._build_messages(prompt, file_path=file_path)

        kwargs: Dict[str, Any] = {}
        if schema:
            kwargs["response_format"] = {"type": "json_object"}
            messages[-1]["content"] += self._schema_instruction(schema)

        last_exception: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                response = await client.chat.completions.create(
                    model=model, messages=messages, **kwargs
                )

                text = response.choices[0].message.content if response.choices else None
                if text:
                    return text

                raise ValueError("No content generated from LLM response")

            except (APIError, APIConnectionError, APITimeoutError, httpx.TimeoutException) as e:
                last_exception = e
                if not _is_retryable(e):
                    logger.error(f"Non-retryable LLM API error for generate_content: {_format_api_error(e)}")
                    break
                if attempt < max_retries:
                    # Exponential backoff with jitter
                    backoff_time = base_delay * (2 ** attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        f"LLM API error (attempt {attempt + 1}/{max_retries + 1}): {_format_api_error(e)}. "
                        f"Retrying in {backoff_time:.2f}s"
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    logger.error(f"All {max_retries + 1} attempts failed for generate_content: {_format_api_error(e)}")

        # If we reach here, all retries failed
        raise last_exception or ValueError("Failed to generate content after all retries")

    async def generate_structured(
        self,
        prompt: str,
        schema: Type[T],
        client: AsyncOpenAI,
        model: Optional[str] = None,
        file_path: Optional[str] = None,
        cache_key: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ) -> T:
        """
        Generate content constrained to a Pydantic schema and return a validated instance.

        The schema is stated in the prompt and the request asks for `json_object`;
        this backend has no strict-schema mode, so validation is the caller's and
        happens here. Parse failures are retried with a corrective instruction
        exactly as transport failures are.
        """
        if not client:
            raise ValueError("Client is required for generate_structured")

        if not model:
            model = self.default_model

        base_messages = self._build_messages(prompt, file_path=file_path)
        base_messages[-1]["content"] += self._schema_instruction(schema)

        kwargs: Dict[str, Any] = {"response_format": {"type": "json_object"}}

        last_exception: Optional[Exception] = None
        last_raw: Optional[str] = None

        for attempt in range(max_retries + 1):
            messages = list(base_messages)
            if attempt > 0 and last_raw is not None:
                # The model saw its own malformed output; show it the rejection so
                # the retry is a correction rather than a re-roll of the dice.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response could not be parsed as valid JSON. "
                            "Respond with ONLY a single JSON object matching the schema — "
                            "no prose, no markdown code fences, and properly escape any "
                            "quotes inside string values. Previous response:\n"
                            + last_raw[:4000]
                        ),
                    }
                )

            try:
                response = await client.chat.completions.create(
                    model=model, messages=messages, **kwargs
                )

                last_raw = (
                    response.choices[0].message.content if response.choices else None
                )
                if not last_raw:
                    raise ValueError("No content generated from LLM response")

                response_json = JSONParser.validate_and_extract_json(last_raw)
                return schema.model_validate(response_json)

            except (APIError, APIConnectionError, APITimeoutError, httpx.TimeoutException) as e:
                last_exception = e
                if not _is_retryable(e):
                    logger.error(
                        f"Non-retryable LLM API error for generate_structured "
                        f"({schema.__name__}): {_format_api_error(e)}"
                    )
                    break
                if attempt < max_retries:
                    backoff_time = base_delay * (2 ** attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        f"LLM API failed (attempt {attempt + 1}/{max_retries + 1}): {_format_api_error(e)}. "
                        f"Retrying in {backoff_time:.2f}s"
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    logger.error(f"All {max_retries + 1} attempts failed for generate_structured: {_format_api_error(e)}")
            except (ValueError, ValidationError) as e:
                last_exception = e
                if attempt < max_retries:
                    backoff_time = base_delay * (2 ** attempt) * (0.5 + 0.5 * random.random())
                    logger.warning(
                        f"Structured parse failed for {schema.__name__} "
                        f"(attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {backoff_time:.2f}s"
                    )
                    await asyncio.sleep(backoff_time)
                else:
                    logger.error(
                        f"All {max_retries + 1} attempts failed to parse structured response for "
                        f"{schema.__name__}. Raw response (first 1000 chars): {(last_raw or '')[:1000]!r}"
                    )

        raise last_exception or ValueError("Failed to generate structured content after all retries")


class PaperOperations(AsyncLLMClient):
    """
    Simplified LLM client for metadata extraction.
    This is a placeholder implementation that would need to be replaced
    with actual LLM API calls (OpenAI, Anthropic, Google, etc.)
    """

    def __init__(self, api_key: str, default_model: Optional[str] = None):
        """Initialize the LLM client for paper operations."""
        super().__init__(api_key, default_model=default_model)

    async def count_metadata_prompt_tokens(self, paper_content: str) -> int:
        """Token count for `paper_content` as the metadata extraction model sees it.

        Keeps the choice of extraction model (and therefore of tokenizer) inside
        this client, so callers can ask "does this fit?" without knowing it.
        """
        return await self.count_tokens(paper_content, model=FAST_CHAT_MODEL)

    async def _extract_single_metadata_field(
        self,
        model: Type[T],
        paper_content: str,
        schema: Type[BaseModel],
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> T:
        """
        Helper function to extract a single metadata field.

        Args:
            model: The Pydantic model for the data to extract.
            paper_content: The paper content.
            status_callback: Optional function to update task status.
            client: The OpenAI-compatible client to use.
            llm_model: Optional LLM model override.

        Returns:
            An instance of the provided Pydantic model.
        """
        prompt = EXTRACT_METADATA_PROMPT_TEMPLATE.format(
        )

        if paper_content and not cache_key:
            prompt = f"Paper Content:\n\n{paper_content}\n\n{prompt}"

        instance = await self.generate_structured(
            prompt, schema=model, cache_key=cache_key, client=client, model=llm_model
        )

        if model == SummaryAndCitations:
            n_citations = len(getattr(instance, "summary_citations", []))
            status_callback(f"Compiled with {n_citations} citations")
        elif model == InstitutionsKeywords:
            keywords = getattr(instance, "keywords", [])
            institutions = getattr(instance, "institutions", [])
            first_keyword = keywords[0] if keywords else ""
            if first_keyword:
                status_callback(
                    f"Building on {first_keyword} context"
                )
            elif institutions:
                institutions = getattr(instance, "institutions", [])
                first_institution = institutions[0] if institutions else ""
                status_callback(
                    f"Adding context from institution: {first_institution}"
                )
            else:
                status_callback("Processing without keyword data")
        elif model == Highlights:
            highlights = getattr(instance, "highlights", [])
            if highlights:
                status_callback(
                    f"Formulated {len(highlights)} annotations"
                )
            else:
                status_callback("No annotations extracted")
        elif model == TitleAuthorsAbstract:
            title = getattr(instance, "title", "")
            status_callback(
                f"Reading {title if title else 'untitled paper'}"
            )
        else:
            status_callback(f"Successfully extracted {model.__name__}")

        return instance

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_title_authors_abstract(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> TitleAuthorsAbstract:
        result = await self._extract_single_metadata_field(
            model=TitleAuthorsAbstract,
            cache_key=cache_key,
            schema=TitleAuthorsAbstract,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )
        return result

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_institutions_keywords(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> InstitutionsKeywords:
        return await self._extract_single_metadata_field(
            model=InstitutionsKeywords,
            cache_key=cache_key,
            schema=InstitutionsKeywords,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_summary_and_citations(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> SummaryAndCitations:
        result = await self._extract_single_metadata_field(
            model=SummaryAndCitations,
            cache_key=cache_key,
            schema=SummaryAndCitations,
            paper_content=paper_content,
            status_callback=status_callback,
            client=client,
            llm_model=llm_model,
        )
        return result

    @retry_llm_operation(max_retries=3, delay=1.0)
    async def extract_highlights(
        self,
        paper_content: str,
        status_callback: Callable[[str], None],
        client: AsyncOpenAI,
        cache_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> Highlights:
        return await self._extract_single_metadata_field(
            model=Highlights,
            paper_content=paper_content,
            status_callback=status_callback,
            cache_key=cache_key,
            schema=Highlights,
            client=client,
            llm_model=llm_model,
        )

    async def extract_paper_metadata(
        self,
        paper_content: str,
        job_id: str,  # Add job_id here
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> PaperMetadataExtraction:
        """
        Extract metadata from paper content using LLM.

        Args:
            paper_content: The extracted text content from the PDF
            status_callback: Optional function to update task status

        Returns:
            PaperMetadataExtraction: Extracted metadata
        """
        async with time_it("Extracting paper metadata from LLM", job_id=job_id):
            extraction_model = FAST_CHAT_MODEL

            # Create a fresh client for this operation
            client = self._create_client()

            try:
                # Caching only pays off (and is only accepted by Gemini) above the
                # 1024-token floor; for small papers, skip straight to inline.
                if len(paper_content) < CACHE_MIN_CONTENT_CHARS:
                    logger.info(
                        f"Paper content ({len(paper_content)} chars) below cache floor; "
                        f"using inline extraction for job {job_id}"
                    )
                    cache_key = None
                else:
                    try:
                        async with time_it("Creating cache for paper content", job_id=job_id):
                            cache_key = await self.create_cache(paper_content, client, model=extraction_model)
                    except Exception as e:
                        # Inline fallback always covers us, so a cache miss is a warning, not a page.
                        logger.warning(f"Failed to create cache, falling back to inline extraction: {_format_api_error(e)}")
                        cache_key = None

                # Run all extraction tasks concurrently
                async with time_it("Running all metadata extraction tasks concurrently", job_id=job_id):
                    tasks = [
                        asyncio.create_task(time_it("Extracting title, authors, and abstract", job_id=job_id)(
                            self.extract_title_authors_abstract
                        )(
                            paper_content=paper_content,
                            cache_key=cache_key,
                            status_callback=status_callback,
                            client=client,
                            llm_model=extraction_model,
                        )),
                        asyncio.create_task(time_it("Extracting institutions and keywords", job_id=job_id)(
                            self.extract_institutions_keywords
                        )(
                            paper_content=paper_content,
                            cache_key=cache_key,
                            status_callback=status_callback,
                            client=client,
                            llm_model=extraction_model,
                        )),
                        asyncio.create_task(time_it("Extracting summary and citations", job_id=job_id)(
                            self.extract_summary_and_citations
                        )(
                            paper_content=paper_content,
                            cache_key=cache_key,
                            status_callback=status_callback,
                            client=client,
                            llm_model=extraction_model,
                        )),
                        asyncio.create_task(time_it("Extracting highlights", job_id=job_id)(
                            self.extract_highlights
                        )(
                            paper_content=paper_content,
                            cache_key=cache_key,
                            status_callback=status_callback,
                            client=client,
                            llm_model=extraction_model,
                        )),
                    ]

                    # Use shield to prevent task cancellation during cleanup
                    shielded_tasks = [asyncio.shield(task) for task in tasks]
                    results = await asyncio.gather(*shielded_tasks, return_exceptions=True)

                # Process results and handle potential errors
                (
                    title_authors_abstract,
                    institutions_keywords,
                    summary_and_citations,
                    highlights,
                ) = results

                # gather(return_exceptions=True) hands back Exception objects in
                # place of failed results. Without this, getattr() below would
                # silently coerce a failed subtask into empty defaults, masking
                # the real error (e.g. an empty title surfacing downstream as a
                # generic "Failed to extract metadata from PDF"). Log every
                # failure with its traceback so the actual cause is visible.
                subtask_labels = (
                    "title_authors_abstract",
                    "institutions_keywords",
                    "summary_and_citations",
                    "highlights",
                )
                failed_subtasks = [
                    (label, result)
                    for label, result in zip(subtask_labels, results)
                    if isinstance(result, BaseException)
                ]
                if failed_subtasks:
                    # The four subtasks share content/cache/model and differ only by
                    # schema, so a failure common to all of them usually lives in the
                    # shared request. Log that shape (without dumping paper content) so
                    # we can see what an "invalid" request actually looks like — size,
                    # inline-vs-cached path, model, and any odd characters in the text.
                    non_printable = sum(
                        1 for c in paper_content
                        if not c.isprintable() and c not in "\n\r\t"
                    )
                    logger.error(
                        "Metadata extraction failed for job %s [%d/%d subtasks failed]: "
                        "model=%s, path=%s, paper_content_chars=%d, non_printable_chars=%d",
                        job_id,
                        len(failed_subtasks),
                        len(subtask_labels),
                        extraction_model or self.default_model,
                        "inline" if cache_key is None else "cached",
                        len(paper_content),
                        non_printable,
                    )
                for label, result in failed_subtasks:
                    logger.error(
                        f"Metadata subtask '{label}' failed for job {job_id}: {result}",
                        exc_info=result,
                    )

                # Title/authors/abstract is the critical subtask — without it the
                # paper is unusable. Re-raise its real error instead of returning
                # an object with an empty title. The other subtasks can degrade
                # gracefully to their defaults via getattr() below.
                if isinstance(title_authors_abstract, BaseException):
                    raise title_authors_abstract

                # Combine the results into the final metadata object
                return PaperMetadataExtraction(
                    title=getattr(title_authors_abstract, "title", ""),
                    authors=getattr(title_authors_abstract, "authors", []),
                    abstract=getattr(title_authors_abstract, "abstract", ""),
                    institutions=getattr(institutions_keywords, "institutions", []),
                    keywords=getattr(institutions_keywords, "keywords", []),
                    summary=getattr(summary_and_citations, "summary", ""),
                    summary_citations=getattr(
                        summary_and_citations, "summary_citations", []
                    ),
                    highlights=getattr(highlights, "highlights", []),
                    publish_date=getattr(title_authors_abstract, "publish_date", None),
                )

            except Exception as e:
                logger.error(f"Error extracting metadata: {e}", exc_info=True)
                if status_callback:
                    status_callback(f"Error during metadata extraction: {e}")
                raise ValueError(f"Failed to extract metadata: {str(e)}")
            finally:
                await self._close_client(client)

    async def extract_data_table(
        self,
        columns: List[str],
        file_path: str,
        paper_id: str,
        list_columns: Optional[List[str]] = None,
    ) -> DataTableRow:
        """
        Extract structured data table from paper content.

        Args:
            columns: List of column names for the data table
            file_path: The file path to the PDF
            list_columns: Subset of columns extracted as collections (one
                cited entry per instance found) rather than single values
        Returns:
            str: JSON string representing the data table
        """
        # Create a fresh client with longer timeout since we're sending full PDFs
        client = self._create_client(timeout=self.PDF_TIMEOUT)
        list_column_set = set(list_columns or [])

        try:
            # Map each column to a safe field name. User-supplied column names can contain
            # quotes, apostrophes, accents, or spaces (e.g. Italian columns), which become
            # fragile JSON property names that the model struggles to generate correctly.
            aliases: Dict[str, str] = {f"col_{i}": col for i, col in enumerate(columns)}

            cols_str = "\n".join(
                f'- {alias}: "{col}"'
                + (" [LIST: one keyed entry per instance found in the paper]" if col in list_column_set else "")
                for alias, col in aliases.items()
            )
            prompt = EXTRACT_COLS_INSTRUCTION.format(
                cols_str=cols_str,
                n_cols=len(columns)
            )

            # The extraction model must only ever produce value + citations —
            # DataTableCellValue also carries fields the calculator owns
            # (entries), which must not appear in the LLM's output schema.
            class ExtractedCell(BaseModel):
                value: str
                citations: List[ResponseCitation] = []

            class ExtractedEntry(BaseModel):
                key: str = Field(
                    description=(
                        "Label identifying which instance this entry belongs to "
                        "(model name, dataset, condition, etc.). Empty string if "
                        "the paper gives no such label."
                    )
                )
                value: str
                citations: List[ResponseCitation] = []

            # Create the dynamic schema that matches DataTableRow structure.
            # Scalar columns map to one cell; list columns to a list of cells.
            field_definitions: Dict[str, Any] = {
                alias: (
                    (List[ExtractedEntry], Field(description=f"One keyed entry per instance found for column: {col!r}"))
                    if col in list_column_set
                    else (ExtractedCell, Field(description=f"Value and citations for column: {col!r}"))
                )
                for alias, col in aliases.items()
            }

            # Create the values model that enforces all column names as required fields
            ValuesModel = create_model(
                'ValuesModel',
                __config__=ConfigDict(),  # Prevent extra fields
                **field_definitions
            )

            values_instance = await self.generate_structured(
                prompt,
                schema=ValuesModel,
                model=self.default_model,
                file_path=file_path,
                client=client,
            )

            # Map aliased fields back to the original column names.
            values_dict: Dict[str, DataTableCellValue] = {}
            for alias, col in aliases.items():
                extracted = getattr(values_instance, alias)
                if col in list_column_set:
                    entries = [
                        CellEntry(
                            value=e.value,
                            key=e.key.strip() or None,
                            citations=e.citations,
                        )
                        for e in extracted
                        if e.value.strip() and e.value.strip().upper() != "N/A"
                    ]
                    values_dict[col] = DataTableCellValue(
                        value="; ".join(
                            f"{e.key}: {e.value}" if e.key else e.value for e in entries
                        )
                        if entries
                        else "N/A",
                        citations=[],
                        entries=entries,
                    )
                else:
                    values_dict[col] = DataTableCellValue(
                        value=extracted.value, citations=extracted.citations
                    )

            # Create and return the DataTableRow
            return DataTableRow(
                paper_id=paper_id,
                values=values_dict
            )
        except Exception as e:
            logger.error(f"Error extracting data table: {str(e)}", exc_info=True)
            raise ValueError(f"Failed to extract DT for paper {paper_id}: {str(e)}")
        finally:
            await self._close_client(client)


# Create a single instance to use throughout the application. The key is the
# DeepSeek one now; GOOGLE_API_KEY was this worker's Gemini credential.
api_key = os.getenv("DEEPSEEK_API_KEY")

if not api_key:
    raise ValueError("DEEPSEEK_API_KEY environment variable is not set")

llm_client = PaperOperations(api_key=api_key, default_model=DEFAULT_CHAT_MODEL)
fast_llm_client = PaperOperations(api_key=api_key, default_model=FAST_CHAT_MODEL)
