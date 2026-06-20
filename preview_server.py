"""Minimal preview server — no Groq/Discord needed, just shows the UI."""
import json
from pathlib import Path
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

MOCK_HISTORY = []

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    msg = (request.json or {}).get("message", "")
    MOCK_HISTORY.append({"role": "user", "content": msg})
    reply = (
        "Day A - Chest + Triceps\n\n"
        "Warm-up: 5 min treadmill\n\n"
        "1. Dumbbell Bench Press  |  4 sets x 8-12 reps\n"
        "2. Dumbbell Incline Bench Press  |  3 sets x 8-12 reps\n"
        "3. Dumbbell Chest Fly  |  3 sets x 10-12 reps\n"
        "4. Tricep Overhead Extension  |  3 sets x 10-12 reps\n"
        "5. Resistance Band Tricep Pushdown  |  3 sets x 12-15 reps\n\n"
        "Also, what's your weight today?"
    )
    MOCK_HISTORY.append({"role": "assistant", "content": reply})
    return jsonify({"reply": reply})

@app.route("/reset", methods=["POST"])
def reset():
    MOCK_HISTORY.clear()
    return jsonify({"ok": True})

@app.route("/chat_history")
def chat_history():
    return jsonify({"history": MOCK_HISTORY})

@app.route("/day_info")
def day_info():
    return jsonify({"day": "A", "name": "Chest + Triceps", "focus": "chest, triceps"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
