# CAS-AICP-FP — Mirror Mirror

Final project for the CAS in AI & Creative Practice.

## Project: Mirror Interface

An interactive art installation where visitors stand in front of a screen shaped like a mirror and have an intimate AI-driven conversation with **Mira** — a reflective presence that listens, observes, and generates a portrait of the visitor.

---

## Folder Structure

```
mirror-interface/
├── app.py                  # Flask server, SocketIO events, session orchestration
├── config.py               # All settings, band definitions, API key placeholders
├── camera_worker.py        # Background camera capture thread
├── pi_camera.py            # Raspberry Pi camera interface (picamera2 / OpenCV)
├── audio_capture.py        # Microphone input + silence detection
├── avatar_client.py        # Runway Realtime Avatar WebSocket client
├── runway_connect.py       # Runway ML session management
│
├── api_clients/
│   ├── crt_effect.py                          # CRT post-processing effect
│   ├── gender.py                              # Gender detection helper
│   ├── instax.py                              # Instax BLE printer client
│   ├── portrait.py                            # Portrait generation router
│   ├── replicate_instantid_photorealistic.py  # Replicate InstantID face-preserving portrait
│   ├── replicate_nano_banana.py               # Replicate Nano Banana model client
│   ├── replicate_portrait.py                  # Replicate portrait generation
│   └── runway.py                              # Runway ML image/video generation
│
├── fer_engine/
│   └── detector.py         # Facial Expression Recognition (FER) — real-time emotion detection
│
├── band_styles/             # Style reference images for portrait generation
│   ├── bioluminescence/
│   ├── botanique/
│   ├── geometric/
│   ├── glitch/
│   ├── renaissance/
│   └── surveillance/
│
├── static/
│   ├── index.html           # Single-page frontend
│   ├── css/mirror.css       # Mirror UI styles
│   └── js/
│       ├── main.js              # App entry point
│       ├── camera.js            # Browser camera feed
│       ├── avatar.js            # Runway avatar display
│       ├── socket-client.js     # SocketIO event handling
│       ├── conversation-ui.js   # Chat / subtitles UI
│       ├── emotion-display.js   # Live emotion overlay
│       ├── portrait-reveal.js   # Portrait reveal animation
│       └── transition.js        # State transition effects
│
└── utils/
    ├── conversation.py      # Conversation state machine + Claude API integration
    └── scoring.py           # FER + answer scoring → band selection
```

---

## How It Works

1. **Visitor arrives** — the camera captures their face; FER detects live emotion.
2. **Mira speaks** — a Runway Realtime Avatar delivers the conversation via WebSocket.
3. **Conversation** — Mira asks 8 probes across themes of identity, intimacy, and technology. Claude scores each answer in real time.
4. **Band selection** — FER history + answer scoring produce a style band (renaissance → surveillance spectrum).
5. **Portrait generation** — Replicate InstantID generates a face-preserving portrait in the selected style.
6. **Reveal** — the portrait is displayed on the mirror screen and optionally printed via Instax BLE printer.

---

## Hardware

- Raspberry Pi 5
- Pi Camera Module 3
- USB headset / microphone
- Instax Mini Link 3 BLE printer

---

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set required environment variables:

```bash
export RUNWAY_API_KEY=your_runway_key
export REPLICATE_API_KEY=your_replicate_key
export NEXTLEG_API_KEY=your_nextleg_key
```

Run:

```bash
python app.py
```

---

## Tech Stack

- **Backend:** Python, Flask, Flask-SocketIO
- **AI conversation:** Anthropic Claude API
- **Avatar:** Runway Realtime Avatar
- **Portrait generation:** Replicate (InstantID)
- **Emotion detection:** FER (Facial Expression Recognition)
- **Speech-to-text:** Vosk (offline, Pi-optimised)
- **Frontend:** Vanilla JS, WebSockets
- **Printing:** Instax BLE
