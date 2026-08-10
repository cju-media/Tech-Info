import os
import json
import urllib.request
import datetime
import subprocess

REPO = "cju-media/Tech-Info"
STATE_FILE = "disabled_workflows_state.json"

def get_cameron_phone():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    phones_file = os.path.join(os.path.dirname(script_dir), "Team Data", "team_phones.json")
    try:
        with open(phones_file, "r") as f:
            phones = json.load(f)
        return phones.get("Cameron")
    except Exception as e:
        print(f"Could not load team_phones.json: {e}")
        return None

def send_imessage(phone, message):
    escaped_message = message.replace("\\", "\\\\").replace("\"", "\\\"")
    applescript = f"""
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{phone}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    """
    if os.environ.get("DRY_RUN") == "1":
        print(f"[DRY RUN] Would send iMessage to {phone}:\n{message}")
        return True

    try:
        subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True, text=True)
        print(f"Successfully sent iMessage to {phone}")
        return True
    except Exception as e:
        print(f"Error sending iMessage: {e}")
        return False

def check_disabled_workflows():
    pat = os.environ.get("GITHUB_PAT")
    if not pat:
        print("GITHUB_PAT environment variable not found. Skipping disabled workflow check.")
        return

    url = f"https://api.github.com/repos/{REPO}/actions/workflows"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json"
    })

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
    except Exception as e:
        print(f"Failed to fetch workflows: {e}")
        return

    # Load existing state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
        except Exception:
            state = {}
    else:
        state = {}

    now = datetime.datetime.now(datetime.timezone.utc)
    workflows_to_report = []

    new_state = {}

    for wf in data.get("workflows", []):
        if wf["state"] != "active":
            # Disabled workflow
            try:
                # updated_at looks like "2023-10-27T12:34:56.000Z" or "2023-10-27T12:34:56Z"
                updated_str = wf["updated_at"].replace("Z", "+00:00")
                updated_at = datetime.datetime.fromisoformat(updated_str)

                # Check if it's been > 24 hours
                if (now - updated_at).total_seconds() > 24 * 3600:
                    wf_id = str(wf["id"])

                    # Store current disabled status and timestamp
                    new_state[wf_id] = {"name": wf["name"], "updated_at": wf["updated_at"]}

                    # If this specific disabled timestamp hasn't been reported yet, report it
                    if wf_id not in state or state[wf_id].get("updated_at") != wf["updated_at"]:
                        workflows_to_report.append(wf["name"])
            except Exception as e:
                print(f"Error parsing workflow dates: {e}")
                continue

    if workflows_to_report:
        cameron_phone = get_cameron_phone()
        if cameron_phone:
            names = ", ".join(workflows_to_report)
            msg = f"Reminder: The following workflows have been disabled for over 24 hours: {names}. You can re-enable them at https://www.fccla.org/tech-info/dashboard"
            if send_imessage(cameron_phone, msg):
                # Update state if message sent successfully
                print(f"Sent reminder for: {names}")
            else:
                print("Failed to send reminder, will try again next time.")
                # Do not write these to state so they trigger again
                for wf_id, wf_info in list(new_state.items()):
                    if wf_info["name"] in workflows_to_report:
                        if wf_id in state:
                            new_state[wf_id] = state[wf_id] # Revert to previous known state or omit
                        else:
                            del new_state[wf_id]

    # Clean up state (remove active workflows)
    # Save the new state
    with open(STATE_FILE, "w") as f:
        json.dump(new_state, f, indent=4)
        print("Updated disabled_workflows_state.json")

if __name__ == "__main__":
    check_disabled_workflows()
