"use client"

import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import {
	Card,
	CardContent,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { AlertCircle, Loader2 } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useState } from "react"
import { fetchFromApi } from "@/lib/api"

// Self-service signup for a local account. The account, its password hash, and
// its session all live in this deployment's Postgres; nothing leaves the box.
function RegisterContent() {
	const { user, loading } = useAuth()
	const router = useRouter()
	const searchParams = useSearchParams()
	const returnTo = searchParams.get("returnTo") || "/"

	const [name, setName] = useState("")
	const [email, setEmail] = useState("")
	const [password, setPassword] = useState("")
	const [confirm, setConfirm] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [submitting, setSubmitting] = useState(false)

	// Already signed in: nothing to register.
	useEffect(() => {
		if (user && !loading) {
			router.push(returnTo)
		}
	}, [user, loading, router, returnTo])

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault()
		if (password !== confirm) {
			setError("The two passwords don't match.")
			return
		}
		setSubmitting(true)
		setError(null)
		try {
			const data = await fetchFromApi("/api/auth/register", {
				method: "POST",
				body: JSON.stringify({ email, password, name: name || undefined }),
			})
			if (!data?.success) {
				setError(data?.message || "Could not create the account.")
				return
			}
			// Full navigation so the auth provider picks up the new session cookie.
			window.location.href = returnTo
		} catch (err) {
			setError(err instanceof Error ? err.message : "Could not create the account.")
		} finally {
			setSubmitting(false)
		}
	}

	if (loading) {
		return (
			<div className="h-full flex flex-col items-center justify-center py-8 space-y-6">
				<Loader2 className="h-12 w-12 animate-spin text-primary" />
			</div>
		)
	}

	return (
		<div className="flex items-center justify-center h-full p-4">
			<Card className="w-full max-w-md">
				<CardHeader className="text-center">
					<CardTitle className="text-2xl">Create an account</CardTitle>
					<CardDescription>
						This account is stored on this server only.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={handleSubmit} className="space-y-4">
						{error && (
							<Alert variant="destructive">
								<AlertCircle className="h-4 w-4" />
								<AlertDescription>{error}</AlertDescription>
							</Alert>
						)}

						<Input
							placeholder="Name (optional)"
							autoComplete="name"
							value={name}
							onChange={(e) => setName(e.target.value)}
							disabled={submitting}
						/>
						<Input
							type="email"
							placeholder="you@example.com"
							autoComplete="username"
							value={email}
							onChange={(e) => setEmail(e.target.value)}
							disabled={submitting}
							required
						/>
						<Input
							type="password"
							placeholder="Password (at least 8 characters)"
							autoComplete="new-password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							disabled={submitting}
							required
						/>
						<Input
							type="password"
							placeholder="Confirm password"
							autoComplete="new-password"
							value={confirm}
							onChange={(e) => setConfirm(e.target.value)}
							disabled={submitting}
							required
						/>
						<Button
							type="submit"
							className="w-full"
							size="lg"
							disabled={submitting || !email || !password || !confirm}
						>
							{submitting ? (
								<Loader2 className="h-4 w-4 animate-spin" />
							) : (
								"Create account"
							)}
						</Button>
					</form>
				</CardContent>
				<CardFooter className="text-sm text-muted-foreground justify-center">
					<span>Already have an account?</span>
					<Link href="/login" className="ml-1 text-primary hover:underline">
						Sign in
					</Link>
				</CardFooter>
			</Card>
		</div>
	)
}

export default function RegisterPage() {
	return (
		<Suspense
			fallback={
				<div className="h-full flex items-center justify-center">
					<Loader2 className="h-12 w-12 animate-spin text-primary" />
				</div>
			}
		>
			<RegisterContent />
		</Suspense>
	)
}
