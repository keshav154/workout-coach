# CoachxKeshav — Workout Coach AI

A personal trainer, nutrition coach, and expense tracker AI that runs as a web app (installable PWA) with Telegram, WhatsApp, and Discord transports. Tracks workouts, progressive overload, weigh-ins, Indian vegetarian nutrition, and spending — all persisted in MongoDB, all driven by natural language (no command syntax).

## Features

- **6-day Push/Pull/Legs x2 split**: every muscle trained twice a week (A: Push-chest, B: Pull-back, C: Legs-quad, D: Push-shoulders, E: Pull-width+arms, F: Legs-posterior)
- **Agentic coach**: the LLM reads real data and writes through validated tools (log sessions, weight, meals, expenses, goals, budgets, check-ins, undo) — never just "says" it logged something
- **Progressive overload (double progression)**: the Workout tab pre-fills each set with the suggested weight and shows a hint — add reps at the same weight until you hit the top of the range, then it bumps you to the next dumbbell and resets reps. Adding reps or sets counts as progress, not a plateau
- **Rep/volume-aware plateau detection**: a lift is only flagged as plateaued when neither estimated 1RM (weight *or* reps) nor volume (sets) improved over the last 3 sessions — so more reps or more sets clears it. Auto-scheduled deloads for genuine plateaus
- **Workout mode**: per-set logging UI with last-session weights pre-filled, PR detection and confetti, a rest timer (60/90/120s with vibration + beep), the day's warm-up routine plus computed warm-up sets (~55% of working weight), one-tap exercise swaps to equipment-appropriate alternatives, a session stopwatch that logs workout duration, and an A–F rotation strip
- **Record wall**: best weight x reps per exercise on the Progress tab
- **Instant Progress/Goals**: the Coach tab's "My Progress" and "My Goals" quick actions jump straight to the Progress tab's real numbers (streaks, plateaus, goal % bars) instead of asking the LLM and waiting on a reply
- **Rest days**: mark today a rest day (button in Workout mode, or just tell the coach "rest day today") — suppresses the nudge and bridges your streak instead of breaking it; a rest is also suggested after 6 straight training days
- **Deload surfacing**: the Workout tab shows a deload-week banner (every ~6 weeks) and per-exercise "deload" tags for lifts the plateau system has auto-flagged
- **Per-exercise notes**: jot a note on any exercise while logging ("left shoulder tight") — shown in the session history and available to the coach
- **Edit / delete past sessions**: expand any session on the Progress tab to fix a weight/reps typo or remove it — per-set detail is preserved when the top set is unchanged
- **Proactive protein**: the Fuel tab flags when you're short on protein and suggests 1-2 Indian veg options to close the gap
- **On-demand backup + cancel deload**: download a full JSON backup from the menu (Telegram-independent), and cancel a scheduled auto-deload from the Workout tab if it was a false alarm
- **Printable weekly summary**: a menu → Weekly Summary view with workouts, volume, weight change, nutrition adherence, PRs, plateaus and spending for any week, with a Print button
- **Body measurements**: tell the coach "waist is 92 cm" — tracked per part (waist, chest, arm, ...) with trend charts on the Progress tab
- **Goal progress bars & achievements**: live % progress toward weight/lift goals with projections, plus milestone badges (sessions, streaks, PRs, weigh-ins, total kg lifted)
- **Per-exercise history**: tap an exercise name in Workout mode to see your last 5 performances
- **CSV export**: download workouts/expenses/meals/weight as CSV from the menu
- **Week-ahead preview**: tap any A–F rotation chip to see that day's exercises and your last weights
- **Progress photos**: upload from the app (auto-downscaled client-side) or send a Telegram photo captioned "progress" — first-vs-latest comparison on the Progress tab
- **Water tracking**: one-tap +Glass (250ml) / +Bottle (500ml) buttons on the Fuel tab with a live progress bar toward a bodyweight-derived daily goal (~35 ml/kg); also loggable by chat ("drank 3 glasses"), nudged in the evening check-in, and summarised weekly
- **Daily habits**: "track a habit: stretch every morning" — tap-to-toggle checklist with streaks on the Progress tab, pending habits nudged in the evening check-in
- **Offline gym mode**: the Workout tab falls back to the last cached program with no signal, and saved workouts queue on-device and sync automatically when back online
- **Dropdown set logging**: weights come from your actual dumbbell set and reps from a list — no typing mid-set
- **Quick logging without chat**: inline expense form on the Money tab; one-tap frequent-meal chips and "repeat yesterday's meals" on the Fuel tab
- **Estimated 1RM overlay** on the per-exercise progress chart (Epley), plus a weekly-consistency stat that doesn't punish rest days
- **Fuel & Money tabs**: today's calories/protein vs target with a 7-day chart and meal list; monthly spending with category budget bars and recent transactions
- **Voice input everywhere**: mic button in the web app (plus Telegram voice notes), transcribed via Whisper and routed through the normal coach
- **Weekly calorie auto-tuning**: the Sunday cron compares your weigh-in trend to your goal and adjusts your daily calorie target (±200/week, capped ±600), telling you what changed and why
- **Weekly weigh-in + goal projection**: trend-based calorie adjustments and ETA projections toward weight/lift goals
- **Indian vegetarian nutrition**: calorie/protein estimates for Indian portions, meal photo analysis (vision), voice notes (Whisper)
- **Autonomous loops** (cron): morning nudge, evening check-in, weekly recap, smart alerts (skipped workouts, overspend, streaks), memory consolidation, data self-heal, JSON backup to Telegram
- **Memory**: PRs, soreness, form cues, episodic daily summaries, and lessons learned from your corrections
- **Trust layer**: every write validated + audited, universal undo, full data export, and a restore path (`python restore_backup.py <backup.json>`)

## Equipment assumed

- Adjustable dumbbells: 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20.5, 22, 24 kg
- Incline-decline bench, treadmill

## Stack

- **Backend**: Python, Flask, Gunicorn
- **AI**: any OpenAI-compatible provider (default Groq `llama-3.3-70b-versatile`); Groq Whisper for voice, Llama 4 Scout for vision
- **Database**: MongoDB Atlas (free M0 cluster)
- **Hosting**: Render (free tier) + external cron pings

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

Runs against an in-memory fake Mongo layer — no database or API keys needed. CI runs the suite on every push (`.github/workflows/tests.yml`).

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

### Free-tier sleep and missed crons

Render's free tier spins the app down after ~15 minutes idle; a sleeping app takes ~1 minute to cold-start, which can exceed the cron service's request timeout — the ping "fails" and the message never sends. The fix (no always-on keep-alive needed, so free hours aren't burned):

**Schedule each cron job 2–3 times, 5 minutes apart** (e.g. daily nudge at 7:00, 7:05, 7:10). The first ping wakes the app (even if the request itself times out), a later one does the work. The daily/evening/weekly/backup endpoints are deduplicated server-side — once a job has completed for its period, extra pings return `{"skipped": ...}` and nothing is double-sent. The app then goes back to sleep until the next slot, so total awake time stays around 15–20 minutes per cron slot.

The web app is resilient to the same cold-start behavior: every request the frontend makes is time-bounded and retried once, and the chat input shows "the server may be waking up" instead of going silent, with a one-tap Retry if it still can't connect after that. This doesn't eliminate cold-start latency (nothing client-side can) — it just means a sleepy first request reads as "a bit slow" instead of "broken".

### Diagnosing a message that never arrived

If a scheduled Telegram message doesn't show up, open these in a browser (append `?secret=<CRON_SECRET>` if you set one):

- **`/cron/test`** — sends a fixed test message immediately and returns JSON with `sent` and a `notify_config` block. This is the fastest check: if `sent` is false, `notify_config.hint` tells you exactly which env var is missing.
- **`/cron/daily?force=1`** — re-runs the real daily nudge, bypassing the once-a-day dedup. Returns `{"sent": true, ...}`, or a `reason` explaining why not (e.g. `no nudge needed today` if you already logged a workout, or `notify failed` with the config).
- **`/status`** (or ask the coach "system status") — now shows the active notification channel and the last time each cron actually executed.

**The most common cause:** outbound notifications need **both** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set in Render. The bot can reply to messages you send it using only the token (it reuses your incoming chat id), so two-way chat can work fine while scheduled nudges silently fail — the missing piece is almost always `TELEGRAM_CHAT_ID`. Get your chat id by messaging the bot and opening `https://api.telegram.org/bot<TOKEN>/getUpdates`, then set it in Render and hit `/cron/test`.

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
