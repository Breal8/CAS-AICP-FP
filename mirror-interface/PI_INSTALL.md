# Mirror Mirror — Raspberry Pi 5 Installation Guide

## Hardware Requirements

- Raspberry Pi 5 (4 GB RAM minimum, 8 GB recommended)
- Raspberry Pi HAT 2 (or compatible audio HAT for headset)
- Pi Camera Module 3 (or compatible) mounted behind one-way mirror
- USB headset with microphone (or HAT audio + separate mic)
- Bluetooth-enabled Instax printer (SP-3, Link, or Mini Link 2)
- Display connected to Pi (behind the one-way mirror)

## Raspberry Pi OS Setup

1. Install Raspberry Pi OS 64-bit (Bookworm or newer)
2. Enable Camera:
   ```bash
   sudo raspi-config
   # Interface Options → Camera → Enable
   ```
3. Enable Bluetooth:
   ```bash
   sudo systemctl enable bluetooth
   sudo systemctl start bluetooth
   ```
4. Update system:
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

## Software Installation

### 1. System Dependencies

```bash
sudo apt install -y \
    python3-pip python3-venv \
    libcamera-dev libjpeg-dev libopenjp2-7 \
    portaudio19-dev libasound2-dev \
    libbluetooth-dev \
    chromium-browser
```

### 2. Python Environment

```bash
cd ~/mirror-interface
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Vosk Model (for offline STT)

```bash
# Download Vosk small English model (~40 MB)
mkdir -p /usr/local/share
wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip -d /usr/local/share/
rm vosk-model-small-en-us-0.15.zip
```

For better accuracy with accents/noise, download the larger models:
- `vosk-model-en-us-0.22` (~1.8 GB) — best accuracy
- `vosk-model-en-us-0.22-lgraph` (~500 MB) — good balance

### 4. Bluetooth Pairing — Instax Printer

```bash
# Install bluetoothctl if not present
sudo apt install -y bluez

# Enter interactive mode
bluetoothctl

# Scan for printer
[bluetooth]# scan on

# Wait for "INSTAX-XXXXXXXX" to appear
[bluetooth]# pair XX:XX:XX:XX:XX:XX
[bluetooth]# trust XX:XX:XX:XX:XX:XX
[bluetooth]# quit
```

Note the printer's MAC address and add it to environment variables (see Configuration below).

## Configuration

Set environment variables in `~/.bashrc` or a `.env` file:

```bash
# API Keys
export RUNWAY_API_KEY="your_runway_api_key"
export RUNWAY_GMW_ENDPOINT="https://api.runwayml.com/v1"
export RUNWAY_CHARACTER_ID="your_preset_character_id"

export PORTRAIT_API_KEY="your_replicate_key"
export PORTRAIT_API_URL="https://api.replicate.com/v1/predictions"

# Pi Hardware
export AUDIO_DEVICE="default"           # or "plughw:1,0" for USB headset
export VOSK_MODEL_PATH="/usr/local/share/vosk-model-small-en-us-0.15"

# Instax Printer
export INSTAX_BLE_NAME="XX:XX:XX:XX:XX:XX"   # MAC address from bluetoothctl
export INSTAX_PRINTER_MODEL="SP-3"

# Mode flags (set to "false" for production)
export MOCK_RUNWAY="false"
export MOCK_PORTRAIT="false"
export MOCK_INSTAX="false"
```

After editing, reload:
```bash
source ~/.bashrc
```

## Running the Installation

### Development / Testing (macOS or Pi without kiosk)

```bash
cd ~/mirror-interface
source venv/bin/activate
python app.py
```

Open browser to `http://localhost:5050`

### Production Kiosk Mode (Pi only)

Create a systemd service for auto-start:

```ini
# /etc/systemd/system/mirror-mirror.service
[Unit]
Description=Mirror Mirror Installation
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/mirror-interface
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/pi/.Xauthority
ExecStart=/home/pi/mirror-interface/venv/bin/python /home/pi/mirror-interface/app.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable mirror-mirror
sudo systemctl start mirror-mirror
```

### Launch Browser in Kiosk Mode

Add to `~/.config/wayland-sessions/` or autostart:

```bash
#!/bin/bash
# Wait for server to be ready
sleep 8

# Launch Chromium in kiosk mode
chromium-browser \
  --kiosk \
  --app=http://localhost:5050 \
  --no-first-run \
  --no-default-browser-check \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  --enable-features=VaapiVideoDecoder
```

## File Structure (Updated)

```
mirror-interface/
├── app.py                    # Flask + SocketIO server (Pi-native)
├── config.py                 # Central configuration
├── pi_camera.py              # Picamera2 + OpenCV fallback
├── audio_capture.py          # ALSA + Vosk STT
├── avatar_client.py          # Runway GMW API client
├── requirements.txt          # Dependencies
├── fer_engine/
│   └── detector.py           # FER wrapper (now accepts numpy frames)
├── api_clients/
│   ├── portrait.py           # Image generation (Replicate/legnext)
│   ├── instax.py             # BLE Instax printing
│   └── runway.py             # Runway Gen-3 (legacy)
├── utils/
│   ├── conversation.py       # State machine
│   └── scoring.py            # Band calculation (60% answer + 40% FER)
├── static/
│   ├── index.html            # Kiosk UI (no camera preview)
│   ├── css/mirror.css        # Styles (+ avatar-video)
│   └── js/
│       ├── main.js           # Kiosk orchestrator (auto-start)
│       ├── socket-client.js  # WebSocket bridge (+ avatar_ready)
│       ├── avatar.js         # Video avatar playback
│       ├── conversation-ui.js# Visual subtitles + listening state
│       ├── emotion-display.js# Real-time emotion bars
│       ├── camera.js         # Browser camera fallback
│       ├── transition.js     # 4-phase dissolve effect
│       └── portrait-reveal.js# Polaroid reveal
└── PI_INSTALL.md             # This file
```

## Conversation Flow

1. **Idle** — Screen shows "approach the mirror" (auto-advances after 5s in kiosk)
2. **Arrival** — Pi camera activates, FER begins, socket connects, avatar appears
3. **Conversation** — Avatar asks 3 questions via Runway GMW video
   - Question video plays → Avatar speaks with built-in voice
   - Video ends → "Listening" indicator appears
   - Visitor speaks into headset
   - Vosk STT transcribes in real-time
   - Silence (1.2s) triggers final answer submission
   - Answer scored + next question generated
4. **Transition** — 4-phase visual dissolve while portrait generates
5. **Reveal** — Polaroid-style portrait appears with band indicator
6. **Print** — Bluetooth Instax prints the physical photo
7. **Reset** — After 60s, returns to idle

## Troubleshooting

### Camera not detected
```bash
# Verify libcamera
libcamera-still --list-cameras

# Check Picamera2 installation
python3 -c "from picamera2 import Picamera2; print('OK')"

# Check for OpenCV fallback
python3 -c "import cv2; cap = cv2.VideoCapture(0); print(cap.isOpened())"
```

### Audio not capturing
```bash
# List audio devices
arecord -l

# Test recording
arecord -D plughw:1,0 -f S16_LE -r 16000 -d 5 test.wav

# Adjust device in config: AUDIO_DEVICE="plughw:1,0"
```

### STT accuracy issues
- Increase Vosk model size (larger = more accurate but slower)
- Tune `AUDIO_SILENCE_SECONDS` in config (default 1.2s)
- Adjust `audio_capture.set_silence_threshold(rms)` per environment

### Bluetooth printer not connecting
```bash
# Verify pairing
bluetoothctl paired-devices

# Check BLE service UUIDs (may need model-specific adjustment)
# Edit `api_clients/instax.py` INSTAX_SERVICE_UUID and INSTAX_PRINT_CHAR
```

### Avatar video not playing
- Check Runway API key and character ID
- Verify `autoplay-policy=no-user-gesture-required` in Chromium flags
- Check browser console for CORS / video loading errors

## Performance Notes

- **Pi 5 can run**: Picamera2 + Vosk small model + Flask + Bluetooth simultaneously
- **CPU usage**: ~60-80% during active conversation (Pi 5 4 GB)
- **Avatar video latency**: 2-5s per question (Runway GMW generation time)
- **Portrait generation**: 10-30s depending on Replicate model and queue
- **FER frame rate**: 2 fps (one analysis every 500ms)

## Security

- Set `FLASK_DEBUG=false` and `FLASK_SECRET_KEY` in production
- Restrict CORS_ORIGINS to `localhost` only in production
- API keys stored as environment variables (never committed)
- No PII stored persistently — sessions are in-memory only
