import remarkGfm from 'remark-gfm'
import createMDX from '@next/mdx'

/** @type {import('next').NextConfig} */
const nextConfig = {
    // Configure `pageExtensions` to include markdown and MDX files
    pageExtensions: ['js', 'jsx', 'md', 'mdx', 'ts', 'tsx'],
    // Enable source maps in production for error tracking
    productionBrowserSourceMaps: true,
    // Transpile packages that import CSS from node_modules
    transpilePackages: ['react-pdf-highlighter-extended', 'pdfjs-dist'],
    // Next strips trailing slashes with a 308 before rewrites run, while FastAPI
    // adds them back with a 307. Through the proxy that becomes an endless
    // redirect loop, and the 307's Location points at the internal service host,
    // which the browser cannot reach (surfaces as "Failed to fetch" on upload).
    // Letting the slash through means each API route is reached directly.
    skipTrailingSlashRedirect: true,
    // Proxy the API through the web origin so the browser only ever talks to
    // one host. Cross-origin calls to :8100 rely on a session cookie that
    // SameSite=lax will not attach to cross-site XHR, which shows up as a
    // successful login immediately bouncing back to /login.
    async rewrites() {
        const api = process.env.API_INTERNAL_URL || 'http://server:8000'
        return [
            { source: '/api/:path*', destination: `${api}/api/:path*` },
        ]
    },
    // Add image remote patterns configuration
    images: {
        remotePatterns: [
            {
                protocol: 'https' as const,
                hostname: 'assets.khoj.dev',
                port: '',
                pathname: '/**',
            },
            {
                protocol: 'https' as const,
                hostname: 'openpaper.ai',
                port: '',
                pathname: '/**',
            },
            {
                protocol: 'https' as const,
                hostname: 'lh3.googleusercontent.com',
                port: '',
                pathname: '/**',
            }
        ],
    },
}

const withMDX = createMDX({
    // Add markdown plugins here, as desired
    options: {
        remarkPlugins: [remarkGfm],
    }
})

// Merge MDX config with Next.js config
export default withMDX(nextConfig)
