# CoachxKeshav — Workout Coach AI

A personal trainer, nutrition coach, and expense tracker AI that runs as a web app (installable PWA) with Telegram, WhatsApp, and Discord transports. Tracks workouts, progressive overload, weigh-ins, Indian vegetarian nutrition, and spending — all persisted in MongoDB, all driven by natural language (no command syntax).

## Features

- **6-day Push/Pull/Legs x2 split**: every muscle trained twice a week (A: Push-chest, B: Pull-back, C: Legs-quad, D: Push-shoulders, E: Pull-width+arms, F: Legs-posterior)
- **Agentic coach**: the LLM reads real data and writes through validated tools (log sessions, weight, meals, expenses, goals, budgets, check-ins, undo) — never just "says" it logged something
- **Progressive overload**: suggests the next dumbbell up from your exact set when you hit the top of a rep range; plateau detection with automatic scheduled deloads
- **Workout mode**: per-set logging UI with last-session weights pre-filled, PR detection and confetti, a rest timer (60/90/120s with vibration + beep), the day's warm-up routine plus computed warm-up sets (~55% of working weight), one-tap exercise swaps to equipment-appropriate alternatives, a session stopwatch that logs workout duration, and an A–F rotation strip
- **Record wall**: best weight x reps per exercise on the Progress tab
- **Body measurements**: tell the coach "waist is 92 cm" — tracked per part (waist, chest, arm, ...) with trend charts on the Progress tab
- **Goal progress bars & achievements**: live % progress toward weight/lift goals with projections, plus milestone badges (sessions, streaks, PRs, weigh-ins, total kg lifted)
- **Per-exercise history**: tap an exercise name in Workout mode to see your last 5 performances
- **CSV export**: download workouts/expenses/meals/weight as CSV from the menu
- **Fuel & Money tabs**: today's calories/protein vs target with a 7-day chart and meal list; monthly spending with category budget bars and recent transactions
- **Voice input everywhere**: mic button in the web app (plus Telegram voice notes), transcribed via Whisper and routed through the normal coach
- **Weekly calorie auto-tuning**: the Sunday cron compares your weigh-in trend to your goal and adjusts your daily calorie target (±200/week, capped ±600), telling you what changed and why
- **Weekly weigh-in + goal projection**: trend-based calorie adjustments and ETA projections toward weight/lift goals
- **Indian vegetarian nutrition**: calorie/protein estimates for Indian portions, meal photo analysis (vision), voice notes (Whisper)
- **Autonomous loops** (cron): morning nudge, evening check-in, weekly recap, smart alerts (skipped workouts, overspend, streaks), memory consolidation, data self-heal, JSON backup to Telegram
- **Memory**: PRs, soreness, form cues, episodic daily summaries, and lessons learned from your corrections
- **Trust layer**: every write validated + audited, universal undo, full data export, and a restore path (`python restore_backup.py <backup.json>`)

## Equipment assumed

- Adjustable dumbbells: 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg
- Incline-decline bench, treadmill

## Stack

- **Backend**: Python, Flask, Gunicorn
- **AI**: any OpenAI-compatible provider (default Groq `llama-3.3-70b-versatile`); Groq Whisper for voice, Llama 4 Scout for vision
- **Database**: MongoDB Atlas (free M0 cluster)
- **Hosting**: Render (free tier) + external cron pings

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd workout-coach
pip install -r requirements.txt
```

### 2. Environment variables

| Variable | Description |
|---|---|
| `MONGODB_URI` | MongoDB Atlas connection string |
| `GROQ_API_KEY` | Free at [console.groq.com](https://console.groq.com) — used for chat (default), Whisper, vision |
| `WEB_PASSWORD` | Password for the web UI lock screen |
| `FLASK_SECRET` | Random secret key for Flask sessions |
| `CRON_SECRET` | Shared secret for `/cron/*` endpoints (`?secret=...`) — **without it they're open** |
| `TELEGRAM_BOT_TOKEN` | From @BotFather (optional — enables Telegram + notifications) |
| `TELEGRAM_CHAT_ID` | Your chat ID — bot only responds to you |
| `TELEGRAM_WEBHOOK_SECRET` | Secret passed to `setWebhook`, verified on each update |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | Optional: point the reasoning brain at another OpenAI-compatible provider (Moonshot/Kimi, NVIDIA NIM, ...) |
| `TWILIO_*` / `ALLOWED_WHATSAPP_NUMBER` | Optional: WhatsApp transport via Twilio |
| `DISCORD_BOT_TOKEN` / `DISCORD_USER_ID` | Optional: Discord bot (only runs via `python bot.py`, not under gunicorn) |
| `APP_TZ_OFFSET_MIN` | Minutes offset from UTC for "today" (default 330 = IST) |

### 3. Deploy to Render

Push to GitHub and connect the repo to Render — `render.yaml` configures the service. Then point an external cron (e.g. cron-job.org) at:

- `/cron/daily` — morning workout nudge
- `/cron/evening` — nutrition/weight check-in + daily episode memory
- `/cron/weekly` — Sunday recap + memory consolidation
- `/cron/check` — smart alerts (run a few times a day)
- `/cron/plateau` — plateau detection + auto-deload flags
- `/cron/selfheal` — workout data repair
- `/cron/backup` — JSON backup sent to Telegram

All accept `?secret=<CRON_SECRET>`.

## Using it

There is **no command syntax** — just talk:

- "what's my workout today?"
- "done — benched 18kg for 10, curls 13.5 for 12"
- "97.3" (when asked your weight)
- "had 2 rotis, dal and paneer bhurji"
- "spent 500 on groceries" / "how much did I spend on food this month?"
- "I want to reach 90kg by September"
- "undo that"

The web UI adds quick tabs: **Coach** (chat), **Workout** (per-set logging), **Progress** (charts: weight trend, weekly volume, per-exercise progress).

## First run

The coach walks you through ~10 onboarding questions (name, age, weight, height, goal, level, diet, injuries, recent training) and saves your profile — never asked again.

## MongoDB collections

`profile`, `workout_log`, `memory`, `history`, `expenses`, `budget`, `meals`, `checkin`, `goals`, `audit`, `episodes`, `lessons`, `alerts_state`, `auto_flags`, `tool_usage`, `system`
