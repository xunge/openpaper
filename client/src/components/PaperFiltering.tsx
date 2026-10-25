"use client"
import { useI18n } from "@/lib/i18n";

import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuGroup,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuSub,
    DropdownMenuSubContent,
    DropdownMenuSubTrigger,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command"
import { Button } from "@/components/ui/button"
import { ChevronsUpDown, Check } from "lucide-react"
import { PaperItem } from "@/lib/schema";
import { PaperStatusEnum } from "@/components/utils/PdfStatus";

export type Filter = {
    type: "author" | "status" | "tag"
    value: string
}

export const NO_TAGS_FILTER_VALUE = "__NO_TAGS__";

export type Sort = {
    type: "publish_date"
    order: "asc" | "desc"
}

interface PaperFilteringProps {
    papers: PaperItem[]
    onFilterChange: (filters: Filter[]) => void
    onSortChange: (sort: Sort) => void
    filters: Filter[]
    sort: Sort
    showSort?: boolean
}

export function PaperFiltering({
    papers, onFilterChange, onSortChange, filters, sort, showSort = true }: PaperFilteringProps) {
    const { t } = useI18n();
    const authors = Array.from(new Set(papers.flatMap(p => p.authors || [])))
    const tags = Array.from(new Set(papers.flatMap(p => p.tags?.map(tag => tag.name) || [])))

    const handleSelectFilter = (filter: Filter) => {
        const newFilters = filters.some(f => f.type === filter.type && f.value === filter.value)
            ? filters.filter(f => !(f.type === filter.type && f.value === filter.value))
            : [...filters, filter]
        onFilterChange(newFilters)
    }

    const handleSelectSort = (newSort: Sort) => {
        onSortChange(newSort)
    }

    const isFilterActive = (filter: Filter) => {
        return filters.some(f => f.type === filter.type && f.value === filter.value)
    }

    const isSortActive = (newSort: Sort) => {
        return sort.type === newSort.type && sort.order === newSort.order
    }

    return (
        <div>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <Button
                        variant="outline"
                        className="w-[200px] justify-between"
                    >
                        {showSort ? t("Filter & Sort") : t("Filter")}
                        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56">
                    {showSort &&
                        <>
                            <DropdownMenuLabel>{t("Sort by")}</DropdownMenuLabel>
                            <DropdownMenuGroup>
                                <DropdownMenuItem onSelect={() => handleSelectSort({ type: "publish_date", order: "desc" })}>
                                    {t("Publish Date (Newest)")}
                                    {isSortActive({ type: "publish_date", order: "desc" }) && <Check className="ml-auto h-4 w-4" />}
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => handleSelectSort({ type: "publish_date", order: "asc" })}>
                                    {t("Publish Date (Oldest)")}
                                    {isSortActive({ type: "publish_date", order: "asc" }) && <Check className="ml-auto h-4 w-4" />}
                                </DropdownMenuItem>

                            </DropdownMenuGroup>
                            <DropdownMenuSeparator />
                        </>
                    }
                    <DropdownMenuLabel>{t("Filter by")}</DropdownMenuLabel>
                    <DropdownMenuGroup>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>{t("Authors")}</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandInput
                                        placeholder={t("Filter author...")}
                                        autoFocus={true}
                                        className="h-9"
                                    />
                                    <CommandList>
                                        <CommandEmpty>{t("No author found.")}</CommandEmpty>
                                        <CommandGroup>
                                            {authors.map(author => (
                                                <CommandItem
                                                    key={author}
                                                    value={author}
                                                    onSelect={() => handleSelectFilter({ type: "author", value: author })}
                                                >
                                                    {isFilterActive({ type: "author", value: author }) && <Check className="mr-2 h-4 w-4" />}
                                                    {author}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>{t("Tags")}</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandInput
                                        placeholder={t("Filter tag...")}
                                        autoFocus={true}
                                        className="h-9"
                                    />
                                    <CommandList>
                                        <CommandEmpty>{t("No tags found.")}</CommandEmpty>
                                        <CommandGroup>
                                            <CommandItem
                                                value="No tags"
                                                onSelect={() => handleSelectFilter({ type: "tag", value: NO_TAGS_FILTER_VALUE })}
                                                className="text-muted-foreground"
                                            >
                                                {isFilterActive({ type: "tag", value: NO_TAGS_FILTER_VALUE }) && <Check className="mr-2 h-4 w-4" />}
                                                {t("No tags")}
                                            </CommandItem>
                                            {tags.map(tag => (
                                                <CommandItem
                                                    key={tag}
                                                    value={tag}
                                                    onSelect={() => handleSelectFilter({ type: "tag", value: tag })}
                                                >
                                                    {isFilterActive({ type: "tag", value: tag }) && <Check className="mr-2 h-4 w-4" />}
                                                    {tag}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>{t("Status")}</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandList>
                                        <CommandGroup>
                                            {Object.values(PaperStatusEnum).map(status => (
                                                <CommandItem
                                                    key={status}
                                                    value={status}
                                                    onSelect={() => handleSelectFilter({ type: "status", value: status })}
                                                >
                                                    {isFilterActive({ type: "status", value: status }) && <Check className="mr-2 h-4 w-4" />}
                                                    {status}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                    </DropdownMenuGroup>
                </DropdownMenuContent>
            </DropdownMenu>

        </div>
    )
}
