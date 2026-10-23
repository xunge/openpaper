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

// Local password sign-in. The hosted product offers Google OAuth and an emailed
// one-time code, but both need the public internet (a registered public
// redirect URI, a mail provider), so on an internal deployment neither can
// complete. This deployment keeps the credential in its own database instead,
// created with `python -m app.scripts.set_password`.
function LoginContent() {
	const { user, loading } = useAuth()
	const router = useRouter()
	const searchParams = useSearchParams()
	const returnTo = searchParams.get("returnTo") || "/"
	const errorParam = searchParams.get("error")

	const [email, setEmail] = useState("")
	const [password, setPassword] = useState("")
	const [error, setError] = useState<string | null>(null)
	const [submitting, setSubmitting] = useState(false)

	useEffect(() => {
		if (errorParam === "invalid_session") {
			setError("That sign-in link has expired. Use your email and password instead.")
		}
	}, [errorParam])

	// Already signed in: go where the app wanted to send them.
	useEffect(() => {
		if (user && !loading) {
			router.push(returnTo)
		}
	}, [user, loading, router, returnTo])

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault()
		setSubmitting(true)
		setError(null)
		try {
			const data = await fetchFromApi("/api/auth/login", {
				method: "POST",
				body: JSON.stringify({ email, password }),
			})
			if (!data?.success) {
				setError(data?.message || "Sign-in failed.")
				return
			}
			// A full navigation, not router.push: the auth provider has to re-read
			// the session cookie the server just set, and its state is cached in
			// this render tree.
			window.location.href = returnTo
		} catch (err) {
			setError(err instanceof Error ? err.message : "Sign-in failed.")
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
					<CardTitle className="text-2xl">Sign in to Open Paper</CardTitle>
					<CardDescription>
						Use the account configured on this server.
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
							placeholder="Password"
							autoComplete="current-password"
							value={password}
							onChange={(e) => setPassword(e.target.value)}
							disabled={submitting}
							required
						/>
						<Button
							type="submit"
							className="w-full"
							size="lg"
							disabled={submitting || !email || !password}
						>
							{submitting ? (
								<Loader2 className="h-4 w-4 animate-spin" />
							) : (
								"Sign in"
							)}
						</Button>
					</form>
				</CardContent>
				<CardFooter className="text-sm text-muted-foreground justify-center">
					<span>No account yet?</span>
					<Link href="/register" className="ml-1 text-primary hover:underline">
						Create one
					</Link>
				</CardFooter>
			</Card>
		</div>
	)
}

export default function LoginPage() {
	return (
		<Suspense
			fallback={
				<div className="h-full flex items-center justify-center">
					<Loader2 className="h-12 w-12 animate-spin text-primary" />
				</div>
			}
		>
			<LoginContent />
		</Suspense>
	)
}
