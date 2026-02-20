# Project Rules

## Debugging Rules

When I report a bug is not fixed, do NOT repeat the same approach. Analyze WHY the previous fix failed before attempting a new one. Ask clarifying questions if needed rather than guessing.

## Project Context

Before making changes, confirm the tech stack. Ask what frontend framework is being used (Streamlit, React, Lovable, etc.) rather than assuming. Never assume Streamlit unless confirmed.

For this project: the stack is Python (FastAPI backend), and frontends may include Streamlit OR Lovable/React. Always check which frontend applies before editing UI code. Key paths: src/engine.py for business logic, main.py for FastAPI endpoints.

## UI/Styling Rules

When fixing CSS or UI styling issues: make ONE targeted change at a time, verify it works, then proceed. Never do large CSS rewrites or structural conversions (e.g., expanders to tabs) without explicit user approval.

## Testing & Verification

Always verify import paths and module resolution before declaring a task complete. Run the server/app after changes to confirm it starts without ImportError or ModuleNotFoundError.

## Prompt Templates

### When a fix doesn't work
Stop. That fix didn't work. Before trying again: 1) Explain exactly WHY the previous fix failed. 2) What is the actual root cause? 3) Propose a different approach. Do NOT make any code changes until I approve the new approach.

### Scope-locked UI fix
Fix ONLY the [specific issue]. Do NOT restructure components, do NOT convert expanders to tabs, do NOT rewrite other CSS. Change the minimum number of CSS properties needed. Show me the diff before applying.

### Test-first bug fix
I have a bug: [describe bug]. Before writing any fix, first: 1) Write a test that reproduces this exact bug and fails. 2) Run the test to confirm it fails. 3) Implement the minimal fix. 4) Run ALL existing tests plus the new test. 5) If anything fails, iterate on the fix without asking me. 6) Only show me the final diff once every test passes. Do not modify any code unrelated to this bug.

## Credentials & Infrastructure Map

IMPORTANT: Never commit secrets to git. All secrets are in .env (git-ignored) or Render env vars.

### Where credentials live:

| Credential | Location | How to access |
|------------|----------|---------------|
| SUPABASE_URL | `.env` (local) + Render env vars | `cat .env` or Render Dashboard > Service > Environment |
| SUPABASE_KEY (anon) | `.env` (local) + Render env vars | Same as above |
| SUPABASE_SERVICE_ROLE_KEY | Render env vars ONLY | Render Dashboard > Service > Environment (sb_secret_ format) |
| SUPABASE_PUBLISHABLE_KEY | Render env vars ONLY | Alias for SUPABASE_KEY (sb_publishable_ format) |
| SUPABASE_SECRET_API_KEY | Render env vars ONLY | Alias for SUPABASE_SERVICE_ROLE_KEY (sb_secret_ format) |
| OPENROUTER_API_KEY | Render env vars ONLY | Render Dashboard > Service > Environment |
| OPENAI_API_KEY | Render env vars ONLY | Fallback API key (sk-proj_ format) |
| ADMIN_EMAILS | `.env` (local) + Render env vars | Same as above |
| ADMIN_PASSWORD | Render env vars ONLY | Render Dashboard > Service > Environment |
| RENDER_API_KEY | `.env` (local, optional) | Only for Render management scripts |
| GitHub PAT | Windows Credential Manager | `git credential fill` (auto-used by git push) |
| SUPABASE_AUTH_URL | Render env vars ONLY | Lovable frontend Supabase (drpuexbgdfdnhabctfhi) |

### Service URLs:

| Service | URL | Dashboard |
|---------|-----|-----------|
| Backend (Render) | https://tribunal-saas.onrender.com | https://dashboard.render.com |
| Supabase (backend DB) | https://vtwskjvabruebaxilxli.supabase.co | https://supabase.com/dashboard |
| Supabase (Lovable frontend) | https://drpuexbgdfdnhabctfhi.supabase.co | https://supabase.com/dashboard |
| GitHub repo | https://github.com/jg1970jg/Tribunal-SaaS | User: jg1970jg |
| Frontend (Lovable) | https://lexportal.lovable.app | https://lovable.dev |

### Admin account:
- Email: jgsena1970@gmail.com
- Password: stored in Supabase Auth (user must remember it, never store here)

### Render service:
- Service ID: srv-d64ej6mr433s73e8pkig
- Service name: Tribunal-SaaS
- API access: use RENDER_API_KEY from .env

### Credential rotation log (2026-02-19):
- GitHub PAT: rotated (old "claude" token deleted, new "claude-code-2026" created, expires 2026-12-31)
- Render API Key: new key created (rnd_ prefix)
- Admin password (Supabase Auth): changed
- ADMIN_PASSWORD (Render env): rotated to new secure value
- SUPABASE_SERVICE_ROLE_KEY: migrated from legacy JWT to sb_secret_ format
- SUPABASE_KEY: migrated from legacy JWT to sb_publishable_ format
- OPENROUTER_API_KEY: rotated (sk-or-v1_ prefix)
- Legacy HS256 JWT signing key: REVOKED in Supabase
- Legacy JWT-based API keys: DISABLED in Supabase
- SUPABASE_JWT_SECRET: DELETED from Render (HS256 key was revoked)

### Security rules:
- NEVER put secrets in git-tracked files (use .env or Render env vars)
- .env is in .gitignore - safe for local secrets
- .claude/settings.local.json is in .gitignore - never track it
- GitHub PAT is in Windows Credential Manager (not in any file)
- When rotating keys: update BOTH the source dashboard AND the Render env vars
- Supabase uses ES256 JWKS for JWT verification (HS256 was revoked)
- All Supabase keys use new format: sb_secret_ (service role) and sb_publishable_ (anon)
