# Sahaiy — AI Call Agent Platform

Enterprise-grade AI call agent platform built with **Vite + Vanilla JS + Supabase**.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Build | Vite 5 (multi-page) |
| Auth & DB | Supabase (PostgreSQL + Realtime + Auth) |
| Frontend | Vanilla JS ES Modules |
| CSS | Custom properties + BEM methodology |
| Language | Hindi / English (Hinglish) |

---

## Project Structure

```
sahaiy/
├── .env                    ← secrets (gitignored)
├── .env.example            ← template for new devs ← START HERE
├── .env.production         ← production overrides
├── .gitignore
├── vite.config.js          ← multi-page Vite config
├── package.json
├── schema.sql              ← Run once in Supabase SQL Editor
│
├── index.html              ← Landing page
├── auth.html               ← Authentication page
├── app.html                ← Dashboard
│
├── css/                    ← Legacy CSS (works in dev; Vite will bundle from src/styles/)
│
└── src/
    ├── config/
    │   └── env.js          ← ⭐ All env vars flow through here
    │
    ├── services/           ← Data layer — all Supabase calls
    │   ├── supabaseClient.js     ← Singleton client
    │   ├── authService.js        ← Auth: signIn, signUp, OAuth, signOut
    │   ├── agentService.js       ← Agents CRUD
    │   ├── conversationService.js ← Conversations, phonenumbers, KB, tools
    │   ├── analyticsService.js   ← Stats + profile
    │   └── realtimeService.js    ← Realtime subscriptions
    │
    ├── utils/              ← Pure helpers, no side effects
    │   ├── formatters.js         ← formatDuration, timeAgo, formatBytes
    │   ├── toast.js              ← showToast()
    │   └── dom.js                ← $, $$, escHtml, setText, getInitials
    │
    ├── components/         ← UI components (pure, no data fetching)
    │   ├── Chart.js              ← drawLineChart() canvas component
    │   ├── Modal.js              ← Create Agent modal
    │   ├── Sidebar.js            ← navigate(), tab switching
    │   └── Playground.js         ← Test call panel
    │
    └── pages/              ← Page entry points (orchestrators)
        ├── landing.js            ← index.html logic
        ├── auth.js               ← auth.html logic
        └── app.js                ← app.html logic (wires everything together)
```

---

## Environment Setup

```bash
# 1. Copy the template
cp .env.example .env

# 2. Fill in your values
VITE_SUPABASE_URL=https://your-project-ref.supabase.co
VITE_SUPABASE_ANON_KEY=your-anon-key
VITE_APP_URL=http://localhost:5173
VITE_APP_NAME=Sahaiy
```

> **Security:** `VITE_SUPABASE_ANON_KEY` is safe to expose client-side — Supabase's
> Row Level Security (RLS) ensures each user can only access their own data.
> **Never** put the `service_role` key in any client-side file.

---

## Database Setup

1. Go to [supabase.com](https://supabase.com) → **New Project**
2. **SQL Editor → New Query** → paste contents of `schema.sql` → **Run**
3. Copy **Project URL** + **anon key** from Settings → API into your `.env`

### Enable Google OAuth

1. Supabase → Authentication → Providers → **Google** → Enable
2. [console.cloud.google.com](https://console.cloud.google.com) → OAuth 2.0 Client ID → Web app
3. Add redirect URI: `https://YOUR_REF.supabase.co/auth/v1/callback`
4. Paste Client ID + Secret back into Supabase

---

## Development

```bash
npm install      # install dependencies
npm run dev      # start dev server → http://localhost:5173
```

Pages:
- `http://localhost:5173/` → Landing page
- `http://localhost:5173/auth.html` → Sign in / Create account
- `http://localhost:5173/app.html` → Dashboard

## Production Build

```bash
npm run build    # outputs to dist/
npm run preview  # preview production bundle
```

---

## Adding a New Feature

1. **Service layer first** → add a function to the relevant `src/services/*.js` file
2. **Wire it in the page** → import and call from `src/pages/app.js`
3. **UI-only logic** → belongs in `src/components/` or `src/utils/`
4. **Never** put raw `supabase` calls in page files — always go through a service

---

## Onboarding Checklist (< 10 min)

- [ ] `cp .env.example .env` and fill in Supabase credentials
- [ ] Run `schema.sql` in Supabase SQL Editor
- [ ] `npm install && npm run dev`
- [ ] Sign up at `localhost:5173/auth.html` — your account will be auto-seeded with demo data
- [ ] Explore the codebase starting from `src/pages/app.js`

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| Vite over CDN scripts | `.env` support, ES modules, tree-shaking, fast HMR |
| Service layer | Keeps business logic out of UI components; easy to unit-test |
| `env.js` gateway | All env reads in one place; validation on startup |
| Supabase RLS | Database enforces access per user — no server middleware needed |
| `Object.assign(window, ...)` | Exposes handlers to HTML `onclick` without a framework |

---

*Made with ❤️ in India 🇮🇳 — © 2026 Sahaiy Technologies*
