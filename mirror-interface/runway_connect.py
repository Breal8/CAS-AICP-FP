#!/usr/bin/env python3
"""
Standalone Runway realtime session connector.
Called as a subprocess from app.py so it runs in a fresh Python interpreter,
completely free of eventlet's monkey-patched sockets/DNS.
Prints a single JSON line to stdout: either the credentials or an error.
"""
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import config
import requests
import runwayml

POLL_TIMEOUT = config.RUNWAY_POLL_TIMEOUT
POLL_INTERVAL = config.RUNWAY_POLL_INTERVAL


def main():
    client = runwayml.RunwayML(api_key=config.RUNWAY_API_KEY)
    avatar = {"type": "custom", "avatarId": config.RUNWAY_AVATAR_ID}

    try:
        resp = client.realtime_sessions.create(
            model="gwm1_avatars",
            avatar=avatar,
            max_duration=config.RUNWAY_AVATAR_MAX_DURATION,
            start_script=config.MIRROR_START_SCRIPT,
            personality=config.MIRROR_PERSONALITY,
            tools=[
                {
                    "type": "client_event",
                    "name": "capture_face",
                    "description": (
                        "Call this during the calibration stillness beat, after asking "
                        "the visitor to hold still. Captures their face as a portrait "
                        "reference. No parameters — call it immediately."
                    ),
                    "parameters": [],
                },
                {
                    "type": "client_event",
                    "name": "interview_complete",
                    "description": (
                        "Call this ONLY after the Release — after you have walked at least "
                        "four of the eight currents AND spoken your closing line. "
                        "Pass the visitor's name and their most meaningful answers."
                    ),
                    "parameters": [
                        {"name": "answer_name", "type": "string", "required": True,
                         "description": "Visitor's name from the calibration beat"},
                        {"name": "answer_1", "type": "string", "required": True,
                         "description": "Visitor's answer or sentiment from the first current touched"},
                        {"name": "answer_2", "type": "string", "required": True,
                         "description": "Visitor's answer or sentiment from the second current touched"},
                        {"name": "answer_3", "type": "string", "required": True,
                         "description": "Visitor's answer or sentiment from the third current touched"},
                        {"name": "answer_4", "type": "string", "required": True,
                         "description": "Visitor's answer or sentiment from the fourth current touched"},
                        {"name": "answer_5", "type": "string", "required": False,
                         "description": "Visitor's answer or sentiment from the fifth current, if touched"},
                    ],
                },
            ],
        )
        session_id = resp.id
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)

    # Poll until READY
    deadline = time.time() + POLL_TIMEOUT
    session = None
    while time.time() < deadline:
        session = client.realtime_sessions.retrieve(session_id)
        status = session.status
        if status == "READY":
            break
        if status == "FAILED":
            failure = getattr(session, "failure", "unknown")
            code = getattr(session, "failureCode", "unknown")
            print(json.dumps({"error": f"FAILED: {failure} (code: {code})"}))
            sys.exit(1)
        if status in ("COMPLETED", "CANCELLED"):
            print(json.dumps({"error": f"Session {status} before READY"}))
            sys.exit(1)
        time.sleep(POLL_INTERVAL)

    if not session or session.status != "READY":
        print(json.dumps({"error": f"Session timed out after {POLL_TIMEOUT}s"}))
        sys.exit(1)

    # Consume → LiveKit credentials
    try:
        consume_resp = requests.post(
            f"{config.RUNWAY_BASE_URL}/v1/realtime_sessions/{session_id}/consume",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {session.session_key}",
            },
            timeout=15,
        )
        consume_resp.raise_for_status()
        creds = consume_resp.json()
    except Exception as exc:
        print(json.dumps({"error": f"consume failed: {exc}"}))
        sys.exit(1)

    print(json.dumps({
        "sessionId": session_id,
        "serverUrl": creds["url"],
        "token": creds["token"],
        "roomName": creds["roomName"],
    }))


if __name__ == "__main__":
    main()
