"""
Shared helper for sending an iMessage to Cameron via osascript on the
self-hosted macOS Actions runner.

Extracted from what used to be three near-identical inline Python blocks
spread across imessage_notifications.yml and test_imessage.yml. Usage:

    python3 "Scheduling/Event Data/send_imessage.py" "message text"

Must be run from the repository root (it reads Scheduling/Team Data/team_phones.json).
"""
import os
import sys
import json
import subprocess


def send_imessage(message):
    phones_path = os.path.join("Scheduling", "Team Data", "team_phones.json")
    with open(phones_path, "r") as f:
        phones = json.load(f)

    cameron_phone = phones.get("Cameron")
    if not cameron_phone:
        print("Could not find Cameron phone number.")
        sys.exit(1)

    print(f"Sending via osascript to {cameron_phone}...")
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
    applescript = f"""
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{cameron_phone}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    """
    subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True, text=True)
    print("Successfully sent iMessage via osascript.")


if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: send_imessage.py <message>")
        sys.exit(1)

    try:
        send_imessage(sys.argv[1])
    except Exception as e:
        print(f"Error preparing iMessage: {e}")
        sys.exit(1)
