"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import { CheckCircle2, Loader2, RefreshCw, ScanText } from "lucide-react"

import { fetchFromApi } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

export interface FocusRegion {
    page: number
    box: number[]
}

interface Block {
    id: string
    label: string
    boxes_norm: number[][]
    en_md: string
    zh_md: string
    image_url: string | null
    /** Searchable source text for image-ified tables/algorithms/code (not rendered). */
    search_text?: string
}

interface Page {
    index: number
    image_url: string | null
    blocks?: Block[]
    zh_md: string
    en_md: string
    raw_ocr?: string
    ocr_degenerate?: boolean
}

interface BilingualDocument {
    id: string
    status: "pending" | "running" | "completed" | "failed"
    progress: number
    page_count: number | null
    stage?: "pending" | "render" | "ocr" | "translate" | "assemble" | "done" | null
    stage_progress?: number
    stage_total?: number
    error: string | null
    content_url?: string | null
    pages: Page[]
}

/** Human-readable label for the stage the worker is currently in. */
const STAGE_LABEL: Record<string, string> = {
    pending: "Queued",
    render: "Rendering pages",
    ocr: "Running OCR",
    mineru: "MinerU parsing",
    prepare: "Preparing blocks",
    translate: "Translating",
    assemble: "Assembling pages",
    done: "Done",
}

const MARKER = /\b[A-Za-z_]+\s*\[\[[-?\d,\s\[\]]+\]\]/g
const stripMarkers = (text: string) => (text || "").replace(MARKER, "").trim()

/**
 * `$$...$$` on one line is parsed as *inline* math by remark-math, and KaTeX
 * rejects `\tag` there. Documents written before the worker normalised display
 * math still hold that shape, so normalise again at render time.
 */
const blockifyDisplayMath = (text: string) =>
    (text || "").replace(/\$\$([\s\S]+?)\$\$/g, (_match, body: string) => `\n$$\n${body.trim()}\n$$\n`)

const markdownComponents = {
    table: ({ children }: { children?: React.ReactNode }) => (
        <div className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-xs">{children}</table>
        </div>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-border bg-muted px-1.5 py-1 text-left font-semibold">{children}</th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-border px-1.5 py-1 align-top">{children}</td>
    ),
    h1: ({ children }: { children?: React.ReactNode }) => <h1 className="mb-2 mt-3 text-base font-bold">{children}</h1>,
    h2: ({ children }: { children?: React.ReactNode }) => <h2 className="mb-1.5 mt-3 text-sm font-semibold">{children}</h2>,
    h3: ({ children }: { children?: React.ReactNode }) => <h3 className="mb-1 mt-2 text-sm font-semibold">{children}</h3>,
    p: ({ children }: { children?: React.ReactNode }) => <p className="my-1.5 leading-6">{children}</p>,
    ul: ({ children }: { children?: React.ReactNode }) => <ul className="my-1.5 list-disc pl-5">{children}</ul>,
    ol: ({ children, start }: { children?: React.ReactNode; start?: number }) => (
        <ol start={start} className="my-1.5 list-decimal pl-5">{children}</ol>
    ),
    code: ({ children }: { children?: React.ReactNode }) => (
        <code className="rounded bg-muted px-1 py-0.5 text-[0.85em]">{children}</code>
    ),
    pre: ({ children }: { children?: React.ReactNode }) => (
        <pre className="my-2 overflow-x-auto rounded-md bg-muted p-2 text-[0.7rem]">{children}</pre>
    ),
}

/** OCR emits HTML tables; react-markdown escapes HTML without rehype-raw. */
function HtmlTable({ html }: { html: string }) {
    return (
        <div
            className="my-2 overflow-x-auto text-xs [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-border [&_td]:px-1.5 [&_td]:py-1 [&_th]:border [&_th]:border-border [&_th]:bg-muted [&_th]:px-1.5 [&_th]:py-1"
            dangerouslySetInnerHTML={{ __html: html }}
        />
    )
}

function BlockContent({ block, text, raw }: { block: Block; text: string; raw: boolean }) {
    if (raw) {
        return (
            <pre className="my-1 whitespace-pre-wrap break-words font-mono text-[0.68rem] leading-5 text-muted-foreground">
                {stripMarkers(text)}
            </pre>
        )
    }
    if (block.image_url) {
        return (
            <a
                href={block.image_url}
                target="_blank"
                rel="noreferrer"
                title={block.label}
                onClick={(event) => event.stopPropagation()}
                className="block"
            >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                    src={block.image_url}
                    alt={block.label}
                    className="my-2 w-full rounded border bg-white transition-opacity hover:opacity-90"
                />
            </a>
        )
    }
    const html = blockifyDisplayMath(stripMarkers(text))
    const tableMatch = html.match(/<table[\s\S]*<\/table>/i)
    if (tableMatch) {
        const start = tableMatch.index ?? 0
        const before = html.slice(0, start).trim()
        const after = html.slice(start + tableMatch[0].length).trim()
        return (
            <div>
                {before && (
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={markdownComponents}>
                        {before}
                    </ReactMarkdown>
                )}
                <HtmlTable html={tableMatch[0]} />
                {after && (
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={markdownComponents}>
                        {after}
                    </ReactMarkdown>
                )}
            </div>
        )
    }
    return (
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={markdownComponents}>
            {html}
        </ReactMarkdown>
    )
}

export function BilingualPanel({
    paperId,
    onFocusRegion,
}: {
    paperId: string
    onFocusRegion: (region: FocusRegion) => void
}) {
    const { t, lang } = useI18n()
    const [document, setDocument] = useState<BilingualDocument | null>(null)
    const [loading, setLoading] = useState(true)
    const [starting, setStarting] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const [activeBlock, setActiveBlock] = useState<string | null>(null)
    // Default is the translated, rendered view; the others expose the pipeline
    // stages for checking what OCR and the translator actually produced.
    const [view, setView] = useState<"zh-rendered" | "zh-raw" | "en-rendered" | "en-raw">("zh-rendered")
    const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

    const load = useCallback(async () => {
        try {
            const data = await fetchFromApi(`/api/paper/bilingual/?id=${paperId}`)
            setDocument(data?.document ?? null)
            setError(null)
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to load")
        } finally {
            setLoading(false)
        }
    }, [paperId])

    useEffect(() => {
        load()
    }, [load])

    useEffect(() => {
        if (!document || (document.status !== "pending" && document.status !== "running")) {
            if (pollRef.current) clearInterval(pollRef.current)
            return
        }
        pollRef.current = setInterval(load, 3000)
        return () => {
            if (pollRef.current) clearInterval(pollRef.current)
        }
    }, [document, load])

    const start = useCallback(async () => {
        setStarting(true)
        setError(null)
        try {
            // MinerU 4.1 is the only OCR backend exposed by the product; the
            // server-side `model` query remains for older API clients only.
            const data = await fetchFromApi(`/api/paper/bilingual/?id=${paperId}`, {
                method: "POST",
            })
            setDocument(data?.document ?? null)
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to start")
        } finally {
            setStarting(false)
        }
    }, [paperId])

    if (loading) {
        return (
            <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        )
    }

    const running = document?.status === "pending" || document?.status === "running"
    const hasPages = (document?.pages?.length ?? 0) > 0
    const needsRun = !document || (document.status === "failed" && !hasPages)

    const totalPages = document?.page_count ?? document?.pages.length ?? 0
    const completedPages = document?.status === "completed" ? totalPages : document?.progress ?? 0
    const stageTotal = document?.stage_total ?? 0
    const stageProgress = document?.stage_progress ?? 0
    const percent =
        stageTotal > 0
            ? Math.min(100, Math.round((stageProgress / stageTotal) * 100))
            : totalPages > 0
                ? Math.min(100, Math.round((completedPages / totalPages) * 100))
                : 0
    const done = document?.status === "completed"
    const stageName = document?.stage ? t(STAGE_LABEL[document.stage] ?? "Processing") : ""
    const stageText =
        stageTotal > 0 ? `${stageName} ${stageProgress}/${stageTotal}` : stageName

    const statusText = done
        ? lang === "zh"
            ? `已完成 ${totalPages} 页`
            : `Done · ${totalPages} pages`
        : running
            ? stageText || (lang === "zh" ? "处理中…" : "Processing…")
            : document?.status === "failed"
                ? document?.error ?? t("Failed")
                : t("Not generated yet")

    return (
        <div className="flex flex-col gap-3 pb-4 pl-3 pr-1 text-sm">
            {/* Pinned header: progress stays visible however far down the page the
                reader has scrolled. */}
            <div className="sticky top-0 z-10 -ml-3 border-b bg-background/95 px-3 pb-2 pt-3 backdrop-blur supports-[backdrop-filter]:bg-background/80">
                <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                        {done ? (
                            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
                        ) : running ? (
                            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-blue-500" />
                        ) : (
                            <ScanText className="h-4 w-4 shrink-0 text-muted-foreground" />
                        )}
                        <div className="min-w-0">
                            <p className="truncate text-sm font-semibold">{t("Bilingual view")}</p>
                            <p
                                className={cn(
                                    "truncate text-[0.7rem]",
                                    done ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground",
                                )}
                            >
                                {statusText}
                            </p>
                        </div>
                    </div>
                    <Select value={view} onValueChange={(value) => setView(value as typeof view)}>
                        <SelectTrigger className="h-7 w-[9.5rem] text-xs">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent align="end">
                            <SelectItem value="zh-rendered">{t("Chinese · rendered")}</SelectItem>
                            <SelectItem value="zh-raw">{t("Chinese · raw markdown")}</SelectItem>
                            <SelectItem value="en-rendered">{t("English · rendered")}</SelectItem>
                            <SelectItem value="en-raw">{t("English · raw OCR")}</SelectItem>
                        </SelectContent>
                    </Select>
                    <Button size="sm" variant="outline" onClick={start} disabled={starting || running}>
                        {running ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                        ) : done ? (
                            <RefreshCw className="h-4 w-4" />
                        ) : (
                            <ScanText className="h-4 w-4" />
                        )}
                    </Button>
                </div>

                {(running || done || (document?.status === "failed" && totalPages > 0)) && (
                    <div className="mt-2 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                            <div
                                className={cn(
                                    "h-full rounded-full transition-all duration-500",
                                    done ? "bg-emerald-500" : "bg-blue-500",
                                )}
                                style={{ width: `${done ? 100 : percent}%` }}
                            />
                        </div>
                        <span className="w-9 shrink-0 text-right text-[0.65rem] tabular-nums text-muted-foreground">
                            {done ? 100 : percent}%
                        </span>
                    </div>
                )}
            </div>

            {error && <p className="text-xs text-destructive">{error}</p>}

            {needsRun && (
                <div className="rounded-md border border-dashed p-4 text-center text-xs text-muted-foreground">
                    <p className="mb-2">
                        {t("Generate a Chinese markdown version of this paper, page by page.")}
                    </p>
                    <Button size="sm" onClick={start} disabled={starting}>
                        {starting ? t("Starting…") : t("Start OCR + translation")}
                    </Button>
                </div>
            )}

            {hasPages &&
                (document?.pages ?? [])
                    // A paragraph moved to the previous page leaves this page
                    // with no blocks; don't render an empty page heading.
                    .filter((page) => (page.blocks?.length ?? 0) > 0)
                    .map((page) => (
                    <section key={page.index} className="border-b pb-2 last:border-b-0">
                        <p className="mb-1 text-[0.7rem] font-medium uppercase tracking-wide text-muted-foreground">
                            {lang === "zh" ? `第 ${page.index + 1} 页` : `Page ${page.index + 1}`}
                        </p>
                        {view === "en-raw" ? (
                            <div className="mb-2">
                                {page.ocr_degenerate && (
                                    <p className="mb-1 text-[0.68rem] text-amber-600">
                                        {t("OCR output on this page looked repetitive; shown as-is")}
                                    </p>
                                )}
                                <pre className="whitespace-pre-wrap break-words font-mono text-[0.68rem] leading-5 text-muted-foreground">
                                    {page.raw_ocr || ""}
                                </pre>
                            </div>
                        ) : (
                        (page.blocks ?? []).map((block) => (
                            <div
                                key={block.id}
                                onClick={() => {
                                    setActiveBlock(block.id)
                                    const box = block.boxes_norm[0]
                                    if (box) onFocusRegion({ page: page.index + 1, box })
                                    window.setTimeout(() => setActiveBlock(null), 2000)
                                }}
                                className={cn(
                                    "cursor-pointer rounded px-1.5 py-1 transition-colors",
                                    activeBlock === block.id
                                        ? "bg-blue-100 dark:bg-blue-900/40"
                                        : "hover:bg-muted/60",
                                )}
                            >
                                <BlockContent
                                    block={block}
                                    raw={view.endsWith("raw")}
                                    text={
                                        view.startsWith("zh")
                                            ? block.zh_md
                                            : block.en_md
                                    }
                                />
                            </div>
                        ))
                        )}
                    </section>
                ))}
        </div>
    )
}
