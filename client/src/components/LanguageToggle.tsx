"use client"

import { Button } from "@/components/ui/button"
import { Languages } from "lucide-react"
import { useI18n } from "@/lib/i18n"

/**
 * Switches the interface language. Also decides what language the server
 * answers in: `fetchFromApi` forwards the choice as Accept-Language, and the
 * paper-extraction prompts follow it for new uploads.
 */
export function LanguageToggle() {
	const { lang, setLang } = useI18n()
	const next = lang === "zh" ? "en" : "zh"

	return (
		<Button
			variant="ghost"
			size="sm"
			className="gap-1.5 text-xs font-medium"
			onClick={() => setLang(next)}
			title={lang === "zh" ? "Switch to English" : "切换到中文"}
			aria-label={lang === "zh" ? "Switch to English" : "切换到中文"}
		>
			<Languages className="h-4 w-4" />
			{lang === "zh" ? "EN" : "中文"}
		</Button>
	)
}
