// Talk to the API directly instead of through a Next rewrite.
//
// The rewrite normalises trailing slashes, while FastAPI adds them back with a
// 307 whose Location names the internal service host — the browser cannot reach
// it, which surfaced as "Failed to fetch" on every upload. Calling the API on
// the page's own hostname keeps the session cookie same-site (ports do not
// affect SameSite) and lets each request reach its route verbatim.
function resolveApiBase(): string {
    if (process.env.NEXT_PUBLIC_API_URL) {
        return process.env.NEXT_PUBLIC_API_URL;
    }
    if (typeof window !== 'undefined') {
        return `${window.location.protocol}//${window.location.hostname}:${process.env.NEXT_PUBLIC_API_PORT ?? '8100'}`;
    }
    // Server-side render: inside the compose network the service name resolves.
    return 'http://server:8000';
}

const API_BASE_URL = resolveApiBase();

// The server reads this to decide the language of answers and of the extraction
// prompts for new uploads. Read from localStorage rather than React context so
// the helper stays usable from non-component code.
const languageHeader = (): Record<string, string> => {
    try {
        const lang = window.localStorage.getItem('op_language');
        return { 'Accept-Language': lang === 'zh' ? 'zh-CN' : 'en' };
    } catch {
        return { 'Accept-Language': 'en' };
    }
};

export async function fetchFromApi(endpoint: string, options: RequestInit = {}) {
    const headers: HeadersInit = { ...languageHeader() };

    // Only set Content-Type to application/json if we're not sending FormData
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers: {
            ...headers,
            ...options.headers,
        },
        credentials: 'include', // Include cookies for auth
    });

    if (!response.ok) {
        let errorMessage: unknown = `API error: ${response.status}`;

        try {
            const errorData = await response.json();
            if (errorData.message) {
                errorMessage = errorData.message;
            } else if (errorData.error) {
                errorMessage = errorData.error;
            } else if (errorData.detail) {
                errorMessage = errorData.detail;
            }
        } catch {
            // If we can't parse the error response, fall back to status text
            errorMessage = `API error: ${response.status} ${response.statusText}`;
        }

        if (typeof errorMessage !== 'string') {
            errorMessage = JSON.stringify(errorMessage);
        }

        throw new Error(errorMessage as string)
    }

    if (response.status === 204) {
        return null; // No content to return
    }

    return response.json();
}

export async function fetchStreamFromApi(
    endpoint: string,
    options: RequestInit = {}
): Promise<ReadableStream<Uint8Array>> {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        ...options,
        headers: {
            ...languageHeader(),
            ...options.headers,
            // For SSE, we want text/event-stream instead of octet-stream
            Accept: 'text/event-stream',
        },
        credentials: 'include', // Include cookies for auth
    });

    if (!response.ok) {
        let errorMessage: unknown = `API error: ${response.status}`;

        try {
            const errorData = await response.json();
            if (errorData.message) {
                errorMessage = errorData.message;
            } else if (errorData.error) {
                errorMessage = errorData.error;
            } else if (errorData.detail) {
                errorMessage = errorData.detail;
            }
        } catch {
            // If we can't parse the error response, fall back to status text
            errorMessage = `API error: ${response.status} ${response.statusText}`;
        }

        if (typeof errorMessage !== 'string') {
            errorMessage = JSON.stringify(errorMessage);
        }

        throw new Error(errorMessage as string);
    }

    if (!response.body) {
        throw new Error('Response body is null');
    }

    return response.body;
}

export async function getProjectsForPaper(paperId: string) {
    return fetchFromApi(`/api/projects/papers/from/${paperId}`);
}

/**
 * Fetch a fresh presigned file URL for a single paper within a project.
 * Access is granted via project membership, so this works for collaborators
 * who don't own the paper. Use this instead of refetching the whole project
 * paper list just to refresh one URL.
 */
export async function getProjectPaperFileUrl(
    projectId: string,
    paperId: string,
): Promise<string | null> {
    const response = await fetchFromApi(
        `/api/projects/papers/${projectId}/${paperId}/file-url`,
    );
    return response?.file_url ?? null;
}

/**
 * Fetch a fresh presigned file URL for a single owned paper. The cheap path
 * for refreshing an expired URL — avoids the metadata enrichment and full
 * document payload of GET /api/paper.
 */
export async function getPaperFileUrl(paperId: string): Promise<string | null> {
    const response = await fetchFromApi(`/api/paper/${paperId}/file-url`);
    return response?.file_url ?? null;
}

/**
 * Download a binary export through the existing cookie-authenticated API.
 * Returns the blob and the server-provided filename; errors carry the JSON
 * `detail`/`message` when the backend rejected the request.
 */
export async function downloadFromApi(
    endpoint: string,
): Promise<{ blob: Blob; filename: string | null }> {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
        method: 'GET',
        credentials: 'include',
        headers: languageHeader(),
    });
    if (!response.ok) {
        let message = `API error: ${response.status}`;
        try {
            const data = await response.json();
            message = data?.detail || data?.message || data?.error || message;
        } catch {
            // keep the status-based message
        }
        throw new Error(typeof message === 'string' ? message : String(message));
    }
    const disposition = response.headers.get('content-disposition') || '';
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    let filename: string | null = null;
    if (encoded?.[1]) {
        try {
            filename = decodeURIComponent(encoded[1].replace(/["']/g, ''));
        } catch {
            filename = encoded[1];
        }
    } else if (plain?.[1]) {
        filename = plain[1];
    }
    return { blob: await response.blob(), filename };
}
