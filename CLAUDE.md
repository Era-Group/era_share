AI Vibe Coding – Security Playbook

# AssemblyAI API
Always fetch `https://www.assemblyai.com/docs/llms.txt` before writing AssemblyAI code. The API changes; do not rely on memorized parameter names.

# Authentication & Sessions
01 – Session Lifetime. Set session expiration limits. JWT sessions should never exceed 7 days and must use refresh token rotation.
02 – Never use AI-built auth. Use Clerk, Supabase, or Auth0.
03 – Due to chat access, keep API keys strictly secured. Use process.env keys.

# Secure API Development
04 – Rotate secrets every 90 days minimum.
05 – Have the AI verify all suggested packages for security before installing.
06 – Always opt for newer, more secure package versions.
07 – Run npm audit fix after every build.
08 – Sanitize all inputs using parameterized queries always.

# API & Access Control
09 – Enable Row-Level Security in your DB from day one.
10 – Remove all console.log statements before deploying production domain.
11 – Use CORS to restrict access to your allow-listed production domain.
12 – Validate all redirect URLs against an allow-list.
13 – Add auth and rate limiting to every endpoint.

# Data & Infrastructure
14 – Cap AI API costs within your code and dashboard.
15 – Add DDoS protection via Cloudflare or Vercel edge config.
16 – Lock down storage access so users can only use their own files.
17 – Validate upload limits by signature, not by extension.
18 – Verify webhook signatures before processing payment data.

# Other Rules
19 – Review permissions server-side—UI-level checks are not security.
20 – Log critical actions: deletions, role changes, payments, exports.
21 – Build real account deletion flows. Large fines are not fun.
22 – Automate backups then actually test them. An untested backup is useless.
23 – Keep test and production environments fully separate.
24 – Never let webhooks touch real systems in the test environment.
