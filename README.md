# Workout Coach AI

A personal trainer and nutrition coach AI that runs as a web app and Discord bot. Tracks workouts, progressive overload, weekly weigh-ins, and Indian vegetarian nutrition — all persisted in MongoDB.

## Features

- **4-day body-part split**: Chest+Triceps / Back+Biceps / Shoulders+Arms / Legs+Core
- **Progressive overload**: Suggests weight increases from your exact dumbbell set when you hit the top of your rep range
- **Weekly weigh-in**: Asks your weight once per week (first session of each week) and auto-adjusts calorie targets based on trend
- **Missed workout detection**: If 7+ days since last session, suggests a 10-15% deload to ease back in safely
- **Indian vegetarian nutrition**: Tracks calories and protein with Indian food estimates; suggests meals to close protein gaps
- **Persistent memory**: Remembers PRs, soreness, form cues, and observations across sessions
- **Onboarding**: Gathers your full profile on first run — no hardcoded values
- **Web UI + Discord bot**: Both interfaces share the same MongoDB backend

## Equipment

- Adjustable dumbbells: 4.5, 8, 9, 10, 11.5, 13.5, 16, 18, 20, 22, 24 kg
- Incline-decline bench
- Treadmill
- Resistance bands

## Stack

- **Backend**: Python, Flask, Gunicorn
- **AI**: Groq API (`llama-3.3-70b-versatile`) via OpenAI-compatible SDK
- **Database**: MongoDB Atlas (free M0 cluster)
- **Bot**: discord.py
- **Hosting**: Render (free tier)

## Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd workout-coach
pip install -r requirements.txt
```

### 2. Environment variables

Set these in Render dashboard (or a local `.env` file):

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Get free at [console.groq.com](https://console.groq.com) |
| `MONGODB_URI` | MongoDB Atlas connection string |
| `WEB_PASSWORD` | Password for the web UI lock screen |
| `FLASK_SECRET` | Random secret key for Flask sessions |
| `DISCORD_BOT_TOKEN` | From Discord Developer Portal (optional) |
| `DISCORD_USER_ID` | Your Discord user ID — bot only responds to you (optional) |

### 3. Deploy to Render

Push to GitHub and connect the repo to Render. The `render.yaml` configures everything automatically.

Or use the start command directly:

```bash
gunicorn bot:flask_app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

## First Run

On first open, the coach asks you ~10 onboarding questions:
- Name, age, weight, height
- Goal, fitness level, days per week, diet
- Injuries to avoid
- Recent training history and weights used

Your profile is saved to MongoDB and never asked again.

## Web UI

Open your Render app URL → enter your `WEB_PASSWORD` → start chatting.

Quick action buttons:
- **Today's Workout** — get the day's exercises with suggested weights
- **Log Session** — log exercises, sets, reps after finishing
- **Meal Suggestion** — get Indian food suggestions to hit protein target
- **Log Weight** — record your weekly weigh-in
- **Last Session** — recap of previous workout

## Discord Bot

Invite your bot to a server, then use these commands:

| Command | Action |
|---|---|
| `!workout` | Today's workout |
| `!done` | Log session + nutrition |
| `!weight 97.5` | Log your weight |
| `!summary` | Last session recap |
| `!reset` | Fresh conversation |
| `!help` | Command list |

Or just type anything to chat with your coach.

## MongoDB Collections

| Collection | Contents |
|---|---|
| `profile` | Your onboarding profile (goal, weight, height, etc.) |
| `workout_log` | All logged sessions with exercises and nutrition |
| `memory` | Persistent notes: PRs, soreness, form cues, weight log |
| `history` | Recent conversation history (last 20 messages) |
