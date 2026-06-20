import urllib.request
import urllib.parse
import sys
import json
import re
import pandas as pd
from datetime import datetime, timedelta
import os
from weasyprint import HTML
import smtplib
from email.message import EmailMessage
import subprocess

TEAM_MEMBERS = ["Aria", "Cameron", "Danny", "Jaffe", "Kaspar", "Marc", "Saad"]

def send_imessage(to_email, message, is_dry_run=False):
    """
    Sends an iMessage using AppleScript (via osascript).
    """
    if is_dry_run:
        print(f"[DRY RUN] Would send iMessage to {to_email}:\n{message}\n")
        return

    print(f"Sending iMessage to {to_email}...")
    # Properly escape quotes and backslashes for AppleScript string
    escaped_message = message.replace('\\', '\\\\').replace('"', '\\"')

    applescript = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{to_email}" of targetService
        send "{escaped_message}" to targetBuddy
    end tell
    '''

    try:
        subprocess.run(['osascript', '-e', applescript], check=True, capture_output=True, text=True)
        print(f"Successfully sent iMessage to {to_email}.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to send iMessage to {to_email}. Error: {e.stderr}")

def parse_time(time_str):
    if not time_str or time_str == 'None':
        return 9999

    match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?', time_str, re.IGNORECASE)
    if not match:
        return 9999

    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    modifier = match.group(3).lower() if match.group(3) else None

    if modifier:
        if modifier in ['pm', 'p'] and hour < 12:
            hour += 12
        if modifier in ['am', 'a'] and hour == 12:
            hour = 0
    else:
        if 7 <= hour <= 11:
            pass # assume AM
        elif 1 <= hour <= 6:
            hour += 12 # assume PM
        elif hour == 12:
            pass # assume PM

    return hour * 60 + minute

def fetch_events_from_sheet():
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?gid={gid}&headers=0'

    response = urllib.request.urlopen(url)
    data = response.read().decode('utf-8')

    match = re.search(r'google\.visualization\.Query\.setResponse\((.*)\);', data)
    if not match:
        raise ValueError("Could not find JSON data in Google Sheet response")

    json_data = json.loads(match.group(1))
    rows = json_data['table']['rows']

    events = []

    for index, row in enumerate(rows):
        cells = [cell['v'] if cell else None for cell in row['c']]
        cells += [None] * (10 - len(cells))

        date_str = cells[0]
        if not date_str:
            continue

        date_obj = None
        if isinstance(date_str, str):
            match_date = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', date_str)
            if match_date:
                try:
                    date_obj = pd.to_datetime(match_date.group(1)).to_pydatetime()
                except:
                    pass

        if date_obj:
            event_name = str(cells[1] or '')
            sanitized_name = re.sub(r'[^a-z0-9]+', '-', event_name.lower()).strip('-')
            sanitized_date = str(date_str).replace('/', '-')
            element_id = f"{sanitized_date}-{sanitized_name}"
            sheet_row = index + 1

            events.append({
                'date_obj': date_obj,
                'Date': date_str,
                'Event': event_name,
                'Venue': str(cells[2] or ''),
                'Call Time': str(cells[3] or ''),
                'Type': str(cells[4] or ''),
                'Availability': str(cells[6] or ''),
                'Assignment': str(cells[7] or ''),
                'Schedule': str(cells[8] or ''),
                'Tech': str(cells[9] or ''),
                'element_id': element_id,
                'sheet_row': sheet_row
            })

    return events

def get_events_by_member(events, start_date, days_ahead=None):
    if days_ahead is not None:
        end_date = start_date + timedelta(days=days_ahead - 1)
        valid_events = [e for e in events if start_date <= e['date_obj'] <= end_date]
    else:
        valid_events = [e for e in events if start_date <= e['date_obj']]

    valid_events.sort(key=lambda x: x['date_obj'])

    events_by_member = {member: [] for member in TEAM_MEMBERS}

    for e in valid_events:
        assignment = str(e['Assignment']).lower()
        for member in TEAM_MEMBERS:
            if member.lower() in assignment:
                events_by_member[member].append(e)

    return events_by_member

def generate_pdf(member, events, start_date, output_filename, run_mode, new_event_ids=None):
    website_base_url = "https://fccla.org/tech-info"
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'

    if run_mode == 'update':
        subtitle = "All Upcoming Events"
        empty_msg = "You have no upcoming events."
    elif run_mode == 'daily_reminder':
        subtitle = f"Today's Events ({start_date.strftime('%B %d, %Y')})"
        empty_msg = "You have no events today."
    else:
        subtitle = f"Two Weeks starting {start_date.strftime('%B %d, %Y')}"
        empty_msg = "You have no events in the next two weeks"

    new_event_ids = new_event_ids or set()

    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #333; }}
            h1 {{ color: #007bff; text-align: center; border-bottom: 2px solid #007bff; padding-bottom: 10px; }}
            h2 {{ color: #555; text-align: center; font-weight: normal; margin-top: -10px; margin-bottom: 30px; }}
            .event {{ margin-bottom: 30px; border: 1px solid #ddd; border-radius: 8px; padding: 20px; page-break-inside: avoid; }}
            .date {{ font-size: 1.2em; font-weight: bold; color: #007bff; margin-bottom: 10px; display: inline-block; }}
            .new-badge {{ background-color: #ffc107; color: #000; font-size: 0.8em; padding: 3px 8px; border-radius: 4px; font-weight: bold; margin-left: 10px; vertical-align: middle; }}
            .title {{ font-size: 1.4em; font-weight: bold; margin-bottom: 15px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; vertical-align: top; }}
            th {{ width: 120px; color: #666; font-weight: bold; }}
            .no-events {{ text-align: center; font-size: 1.2em; color: #666; margin-top: 50px; font-style: italic; }}

            .button-container {{ margin-top: 20px; display: flex; gap: 15px; }}
            .btn {{ display: inline-block; padding: 10px 15px; border-radius: 5px; text-decoration: none; font-weight: bold; font-size: 0.9em; text-align: center; }}
            .btn-primary {{ background-color: #007bff; color: white; }}
            .btn-success {{ background-color: #28a745; color: white; }}
        </style>
    </head>
    <body>
        <h1>Upcoming Tech Schedule</h1>
        <h2>{member} | {subtitle}</h2>
    """

    if not events:
        html_content += f"<div class='no-events'>{empty_msg}</div>"
    else:
        for e in events:
            website_url = f"{website_base_url}#{e['element_id']}"
            sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?gid={gid}&range={e['sheet_row']}:{e['sheet_row']}"

            badge_html = "<span class='new-badge'>*NEW*</span>" if e['element_id'] in new_event_ids else ""

            html_content += f"""
            <div class='event'>
                <div><div class='date'>{e['Date']}</div>{badge_html}</div>
                <div class='title'>{e['Event']}</div>
                <table>
                    <tr><th>Call Time</th><td>{e['Call Time']}</td></tr>
                    <tr><th>Venue</th><td>{e['Venue']}</td></tr>
                    <tr><th>Assignment</th><td>{e['Assignment'].replace(chr(10), '<br>')}</td></tr>
            """
            if e['Schedule']:
                html_content += f"<tr><th>Schedule</th><td>{e['Schedule'].replace(chr(10), '<br>')}</td></tr>"
            if e['Tech']:
                html_content += f"<tr><th>Tech</th><td>{e['Tech'].replace(chr(10), '<br>')}</td></tr>"

            html_content += f"""
                </table>
                <div class="button-container">
                    <a href="{website_url}" class="btn btn-primary">View on Website</a>
                    <a href="{sheet_url}" class="btn btn-success">Open in Google Sheet</a>
                </div>
            </div>
            """

    html_content += "</body></html>"
    HTML(string=html_content).write_pdf(output_filename)

def get_smtp_credentials():
    return {
        'email': os.environ.get('SMTP_EMAIL'),
        'password': os.environ.get('SMTP_PASSWORD'),
        'server': os.environ.get('SMTP_SERVER', 'smtp.gmail.com'),
        'port': int(os.environ.get('SMTP_PORT', 587))
    }

def send_email(to_email, member, start_date, pdf_filename, run_mode, is_dry_run=False, canceled_events=None, new_assignments=None, time_changed_events=None):
    creds = get_smtp_credentials()
    cc_email = "cjohnston@fccla.org"

    if run_mode == 'update':
        has_new = bool(new_assignments)
        has_canceled = bool(canceled_events)
        has_time_change = bool(time_changed_events)

        updates_list = []
        if has_new: updates_list.append("New Assignments")
        if has_canceled: updates_list.append("Cancellations")
        if has_time_change: updates_list.append("Time Changes")

        subject_parts = " & ".join(updates_list)
        subject = f"Schedule Update: {subject_parts}"

        body = f"Hi {member},\n\nThere have been updates to your schedule. Your fully updated schedule is attached. (Modified or newly added events are marked with *NEW* in the PDF).\n\n"

        if has_time_change:
            body += "Time Changes:\n"
            for te in time_changed_events:
                body += f"- {te['name']} on {te['date']}\n"
                body += f"  New Time: {te['new_time']} (was: {te['old_time']})\n"
            body += "\n"

        if has_canceled:
            body += "Canceled Events:\n"
            for ce in canceled_events:
                body += f"- {ce['name']} on {ce['date']}\n"
            body += "\n"

        body += "Best,\nCam-Bot"
    elif run_mode == 'daily_reminder':
        subject = f"Reminder: Your Tech Schedule for Today ({start_date.strftime('%b %d')})"
        body = f"Hi {member},\n\nThis is a reminder for your assigned event(s) happening today. Please find the details attached.\n\nBest,\nCam-Bot"
    else:
        subject = f"Your Tech Schedule - Next Two Weeks ({start_date.strftime('%b %d')})"
        body = f"Hi {member},\n\nPlease find your upcoming schedule for the next two weeks starting {start_date.strftime('%B %d, %Y')} attached.\n\nBest,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg['Cc'] = cc_email
    msg.set_content(body)

    with open(pdf_filename, 'rb') as f:
        pdf_data = f.read()
    msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=f"{member}_schedule.pdf")

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send email to {to_email} (CC: {cc_email})")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg, to_addrs=[to_email, cc_email])
        print(f"Successfully sent email to {to_email} (CC: {cc_email})")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        sys.exit(1)

def send_notification_email(start_date, is_dry_run=False):
    creds = get_smtp_credentials()
    to_email = "cjohnston@fccla.org"

    subject = f"System Notification: Tech Schedules Sent ({start_date.strftime('%b %d')})"
    body = f"Hello,\n\nThis is an automated notification. The weekly schedule workflow has successfully completed. Individual schedule PDFs for the next two weeks starting {start_date.strftime('%B %d, %Y')} have been generated and sent to the team members.\n\nBest,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send notification email to {to_email}")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg)
        print(f"Successfully sent notification email to {to_email}")
    except Exception as e:
        print(f"Failed to send notification email to {to_email}: {e}")
        sys.exit(1)

def send_broadcast_email(team_emails, global_new_events, pdf_filename, is_dry_run=False):
    creds = get_smtp_credentials()
    cc_email = "cjohnston@fccla.org"
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?gid={gid}"

    # Extract all valid recipient emails into a list
    to_emails = [email for email in team_emails.values()]

    subject = "ALERT: New Events Added to the Schedule"

    body = "Hi Team,\n\nNew events have been added to the master calendar. Please review the attached PDF for a list of all upcoming events (the newly added events are marked with *NEW*).\n\n"
    body += "New Events:\n"
    for ne in global_new_events:
        body += f"- {ne['name']} on {ne['date']}\n"

    body += f"\nYou can view the full live schedule in the Google Sheet here: {sheet_url}\n\nBest,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = ", ".join(to_emails)
    msg['Cc'] = cc_email
    msg.set_content(body)

    with open(pdf_filename, 'rb') as f:
        pdf_data = f.read()
    msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename="Master_Upcoming_Schedule.pdf")

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send broadcast email to {msg['To']} (CC: {cc_email})")
        print(f"Subject: {subject}")
        print(f"Attachment: {pdf_filename}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg, to_addrs=to_emails + [cc_email])
        print(f"Successfully sent broadcast email to {len(to_emails)} members (CC: {cc_email})")
    except Exception as e:
        print(f"Failed to send broadcast email: {e}")
        sys.exit(1)

def send_admin_email(start_date, pdf_filenames, is_dry_run=False):
    creds = get_smtp_credentials()
    to_email = "cameron@cju.media"
    cc_email = "cjohnston@fccla.org"

    subject = f"All Team Tech Schedules - Next Two Weeks ({start_date.strftime('%b %d')})"
    body = f"Hi Cameron,\n\nPlease find the generated team schedules for the next two weeks starting {start_date.strftime('%B %d, %Y')} attached.\n\nBest,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg['Cc'] = cc_email
    msg.set_content(body)

    for filename in pdf_filenames:
        with open(filename, 'rb') as f:
            pdf_data = f.read()
        basename = os.path.basename(filename)
        msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=basename)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send ADMIN email to {to_email}")
        print(f"CC: {cc_email}")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg, to_addrs=[to_email, cc_email])
        print(f"Successfully sent admin email to {to_email} (CC: {cc_email})")
    except Exception as e:
        print(f"Failed to send admin email to {to_email}: {e}")
        sys.exit(1)

def send_availability_email(changes, is_dry_run=False):
    creds = get_smtp_credentials()
    to_email = "cjohnston@fccla.org"
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'

    subject = "New Tech Availability Added"

    body = "Hi,\n\nThe following team members have added their availability:\n\n"

    for change in changes:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?gid={gid}&range=G{change['sheet_row']}"
        added_names = ", ".join(change['added_names'])
        body += f"• {added_names} added to '{change['event_name']}' on {change['date']} ({change['call_time']})\n"
        body += f"  Link: {sheet_url}\n\n"

    body += "Best,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send availability email to {to_email}")
        print(f"Subject: {subject}\nBody:\n{body}")
        print(f"Body:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg)
        print(f"Successfully sent availability email to {to_email}")
    except Exception as e:
        print(f"Failed to send availability email to {to_email}: {e}")
        sys.exit(1)

def send_test_email(start_date, pdf_filenames, is_dry_run=False):
    creds = get_smtp_credentials()
    to_email = "cjohnston@fccla.org"

    subject = f"TEST: All Upcoming Events Marked as NEW ({start_date.strftime('%b %d')})"
    body = f"Hi,\n\nThis is a manual test run. Attached are PDFs for each team member showing ALL upcoming events, with every event marked as *NEW*.\n\nBest,\nCam-Bot"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    for filename in pdf_filenames:
        with open(filename, 'rb') as f:
            pdf_data = f.read()
        basename = os.path.basename(filename)
        msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=basename)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send TEST email to {to_email}")
        print(f"Subject: {subject}\nBody:\n{body}")
        return

    try:
        with smtplib.SMTP(creds['server'], creds['port']) as server:
            server.starttls()
            server.login(creds['email'], creds['password'])
            server.send_message(msg)
        print(f"Successfully sent test email to {to_email}")
    except Exception as e:
        print(f"Failed to send test email to {to_email}: {e}")
        sys.exit(1)

def get_assignment_state():
    if os.path.exists('state.json'):
        with open('state.json', 'r') as f:
            data = json.load(f)
            # Migration check: if it's the old format (just mapping members to lists), wrap it.
            if "event_details" not in data and "assignments" not in data:
                return {"assignments": data, "event_details": {}}
            return data
    return {"assignments": {member: [] for member in TEAM_MEMBERS}, "event_details": {}}

def save_assignment_state(state):
    with open('state.json', 'w') as f:
        json.dump(state, f, indent=4)

def get_avail_state():
    if os.path.exists('avail_state.json'):
        with open('avail_state.json', 'r') as f:
            return json.load(f)
    return {}

def save_avail_state(state):
    with open('avail_state.json', 'w') as f:
        json.dump(state, f, indent=4)

if __name__ == "__main__":
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'
    run_mode = os.environ.get('RUN_MODE', 'weekly') # 'weekly', 'admin', 'update', 'test', 'avail_check', 'daily_reminder'

    print("Fetching events...")
    events = fetch_events_from_sheet()

    with open('team_emails.json', 'r') as f:
        team_emails = json.load(f)

    try:
        with open('team_phones.json', 'r') as f:
            team_phones = json.load(f)
    except FileNotFoundError:
        team_phones = {}

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    os.makedirs('pdfs', exist_ok=True)

    if run_mode != 'avail_check':
        # Update state logic (skip on avail_check to prevent unstaged git state issues)
        current_state = get_assignment_state()
        current_assignments = current_state.get("assignments", {})
        current_details = current_state.get("event_details", {})

        all_upcoming_events = get_events_by_member(events, today, days_ahead=None)

        new_assignments = {member: [e['element_id'] for e in all_upcoming_events[member]] for member in TEAM_MEMBERS}
        new_details = {e['element_id']: {'date': e['date_obj'].strftime("%Y-%m-%d"), 'name': e['Event'], 'call_time': e['Call Time']} for e in events if e['date_obj'] and e['date_obj'] >= today}

        new_state = {"assignments": new_assignments, "event_details": new_details}
        state_changed = False
        members_with_updates = {}

        new_event_ids_globally = set(new_details.keys()) - set(current_details.keys())
        global_new_events = []
        if new_event_ids_globally:
            global_new_events = [new_details[eid] for eid in new_event_ids_globally]
            state_changed = True

        for member in TEAM_MEMBERS:
            old_ids = set(current_assignments.get(member, []))
            new_ids = set(new_assignments[member])
            added_ids = new_ids - old_ids
            removed_ids_raw = old_ids - new_ids
            retained_ids = old_ids.intersection(new_ids)

            # Filter out removals that are just naturally passing events (date < today)
            canceled_events = []
            for rid in removed_ids_raw:
                if rid in current_details:
                    event_date_str = current_details[rid]['date']
                    event_date = datetime.strptime(event_date_str, "%Y-%m-%d")
                    if event_date >= today:
                        canceled_events.append(current_details[rid])

            # Check for time changes on retained events
            time_changed_events = []
            time_changed_ids = set()
            for eid in retained_ids:
                if eid in current_details and eid in new_details:
                    old_time = current_details[eid].get('call_time', '')
                    new_time = new_details[eid].get('call_time', '')
                    if old_time != new_time:
                        time_changed_ids.add(eid)
                        time_changed_events.append({
                            'name': new_details[eid]['name'],
                            'date': new_details[eid]['date'],
                            'old_time': old_time,
                            'new_time': new_time
                        })

            if added_ids or canceled_events or time_changed_events:
                members_with_updates[member] = {
                    'added': added_ids,
                    'canceled': canceled_events,
                    'time_changed_ids': time_changed_ids,
                    'time_changed': time_changed_events
                }
                state_changed = True

        save_assignment_state(new_state)
    generated_pdfs = []

    if run_mode == 'update':
        if not state_changed:
            print("No schedule updates detected. Exiting.")
            sys.exit(0)

        if new_event_ids_globally:
            print("Globally new events detected. Sending broadcast to team.")
            master_filename = "pdfs/master_schedule.pdf"
            # Generate a master PDF showing all events for the team
            all_upcoming = [e for e in events if e['date_obj'] >= today]
            all_upcoming.sort(key=lambda x: x['date_obj'])
            # We can leverage generate_pdf and just pass "All Team" as the member name
            generate_pdf("All Team", all_upcoming, today, master_filename, run_mode, new_event_ids=new_event_ids_globally)
            send_broadcast_email(team_emails, global_new_events, master_filename, is_dry_run)

        if members_with_updates:
            print("Schedule assignment updates detected. Sending specific updates to affected members.")
            for member, updates in members_with_updates.items():
                if member in team_emails:
                    filename = f"pdfs/{member}_schedule.pdf"
                    # For update mode, we show ALL upcoming events (days_ahead=None)
                    all_highlight_ids = updates['added'].union(updates['time_changed_ids'])
                    generate_pdf(member, all_upcoming_events[member], today, filename, run_mode, new_event_ids=all_highlight_ids)
                    send_email(team_emails[member], member, today, filename, run_mode, is_dry_run, canceled_events=updates['canceled'], new_assignments=updates['added'], time_changed_events=updates['time_changed'])
                else:
                    print(f"No email configured for {member}, skipping.")

    elif run_mode == 'avail_check':
        print("Running in Availability Check Mode.")
        current_avail_state = get_avail_state()
        new_avail_state = {}
        changes = []

        upcoming_events = [e for e in events if e['date_obj'] >= today]

        for e in upcoming_events:
            if not e['element_id']:
                continue

            # Parse names string into a set of normalized names
            avail_str = e['Availability']
            if avail_str:
                names = set([n.strip() for n in re.split(r'[,&]', avail_str) if n.strip()])
            else:
                names = set()

            new_avail_state[e['element_id']] = list(names)

            old_names = set(current_avail_state.get(e['element_id'], []))
            added_names = names - old_names
            if added_names:
                changes.append({
                    'event_name': e['Event'],
                    'date': e['Date'],
                    'call_time': e['Call Time'],
                    'sheet_row': e['sheet_row'],
                    'added_names': list(added_names)
                })

        save_avail_state(new_avail_state)

        if changes:
            print(f"Found {len(changes)} new availability additions.")
            send_availability_email(changes, is_dry_run)
        else:
            print("No new availability found.")

    elif run_mode == 'admin':
        print("Running in Admin Mode: Sending batched email.")
        # Admin gets a 2-week view
        two_week_events = get_events_by_member(events, today, days_ahead=14)
        for member in TEAM_MEMBERS:
            filename = f"pdfs/{member}_schedule.pdf"
            generate_pdf(member, two_week_events[member], today, filename, run_mode)
            generated_pdfs.append(filename)
        send_admin_email(today, generated_pdfs, is_dry_run)

    elif run_mode == 'test':
        print("Running in Test Mode: Simulating 'update' mode for all members and sending batched to cjohnston@fccla.org")
        for member in TEAM_MEMBERS:
            filename = f"pdfs/{member}_schedule.pdf"
            # For test mode, we want ALL events marked as new
            all_ids_for_member = {e['element_id'] for e in all_upcoming_events[member]}
            # we use 'update' string for the inner function so it formats the title as "All Upcoming Events"
            generate_pdf(member, all_upcoming_events[member], today, filename, 'update', new_event_ids=all_ids_for_member)
            generated_pdfs.append(filename)
        send_test_email(today, generated_pdfs, is_dry_run)

    elif run_mode == 'daily_reminder':
        print("Running in Daily Reminder Mode.")
        # Filter strictly for today's events (1 day ahead = today only)
        todays_events = get_events_by_member(events, today, days_ahead=1)

        sent_any = False
        for member in TEAM_MEMBERS:
            if member in team_emails and todays_events[member]:
                filename = f"pdfs/{member}_schedule.pdf"
                generate_pdf(member, todays_events[member], today, filename, run_mode)
                send_email(team_emails[member], member, today, filename, run_mode, is_dry_run)
                sent_any = True
            elif member not in team_emails and todays_events[member]:
                print(f"No email configured for {member}, skipping today's reminder.")

        if not sent_any:
            print("No events scheduled for today. Exiting.")

    elif run_mode == 'imessage_test':
        print("Running in iMessage Test Mode.")
        if "Cameron" not in team_phones:
            print("Cameron's phone not configured. Cannot send test message.")
        else:
            # Look ahead to find the next day with any events to generate a sample digest
            upcoming_events = get_events_by_member(events, today, days_ahead=30)

            # Find the earliest date among all upcoming events
            earliest_date = None
            for member in TEAM_MEMBERS:
                for ev in upcoming_events[member]:
                    if earliest_date is None or ev['date_obj'] < earliest_date:
                        earliest_date = ev['date_obj']

            if not earliest_date:
                msg_body = "This is a test message from Cam-Bot!\n\nThere are no upcoming events in the next 30 days to generate a sample digest."
                send_imessage(team_phones["Cameron"], msg_body, is_dry_run)
            else:
                # Filter events strictly for that earliest date
                target_date_events = get_events_by_member(events, earliest_date, days_ahead=1)

                summary_lines = []
                for member in TEAM_MEMBERS:
                    if member in team_phones and target_date_events[member]:
                        summary_lines.append(f"{member}:")
                        for ev in target_date_events[member]:
                            summary_lines.append(f"- {ev['Event']} @ {ev['Call Time']}")

                formatted_date = earliest_date.strftime('%B %d')
                msg_body = f"Test Message - Sample Digest for next active day ({formatted_date}):\n\nThe following team members are working:\n" + "\n".join(summary_lines)
                send_imessage(team_phones["Cameron"], msg_body, is_dry_run)
                send_imessage(team_phones["Cameron"], "https://www.fccla.org/tech-info", is_dry_run)

    elif run_mode == 'imessage_reminder':
        print("Running in iMessage Reminder Mode.")

        current_hour = datetime.now().hour
        is_night_cron = current_hour >= 18  # Assuming run around 8pm local (>= 18 for safety margin)

        if is_night_cron:
            print("Night Cron Detected. Checking for tomorrow's early shifts (<= 7:00 AM)...")
            target_date = today + timedelta(days=1)
            target_events_raw = get_events_by_member(events, target_date, days_ahead=1)

            # Filter for early shifts only (<= 7 AM = <= 420 minutes)
            target_events = {member: [e for e in evs if parse_time(e['Call Time']) <= 420]
                             for member, evs in target_events_raw.items()}
            date_word = "tomorrow"
        else:
            print("Morning Cron Detected. Checking for today's regular shifts (> 7:00 AM)...")
            target_date = today
            target_events_raw = get_events_by_member(events, target_date, days_ahead=1)

            # Filter for regular shifts only (> 7 AM = > 420 minutes)
            target_events = {member: [e for e in evs if parse_time(e['Call Time']) > 420]
                             for member, evs in target_events_raw.items()}
            date_word = "today"

        sent_any = False
        summary_lines = []
        for member in TEAM_MEMBERS:
            if member in team_phones and target_events.get(member):
                member_events = target_events[member]
                msg_body = f"Hi {member},\n\nJust a quick reminder you have an event {date_word}:\n"
                summary_lines.append(f"{member}:")
                for ev in member_events:
                    msg_body += f"- {ev['Event']} @ {ev['Call Time']}\n"
                    summary_lines.append(f"- {ev['Event']} @ {ev['Call Time']}")
                msg_body += "\nHave a great shift!\nBest,\nCam-Bot"

                send_imessage(team_phones[member], msg_body, is_dry_run)
                send_imessage(team_phones[member], "https://www.fccla.org/tech-info", is_dry_run)
                sent_any = True
            elif member not in team_phones and target_events.get(member):
                print(f"No phone configured for {member}, skipping {date_word}'s iMessage.")

        if not sent_any:
            print(f"No targeted events scheduled for {date_word}. Exiting.")
        else:
            summary_msg = f"Summary ({date_word.capitalize()}):\nThe following team members are working:\n" + "\n".join(summary_lines)
            if "Cameron" in team_phones:
                send_imessage(team_phones["Cameron"], summary_msg, is_dry_run)
                send_imessage(team_phones["Cameron"], "https://www.fccla.org/tech-info", is_dry_run)
            else:
                print("Cameron's phone not configured. Skipping summary message.")

    else: # weekly mode
        print("Running in Weekly Mode.")
        # Weekly gets a 2-week view
        two_week_events = get_events_by_member(events, today, days_ahead=14)
        for member in TEAM_MEMBERS:
            if member in team_emails:
                filename = f"pdfs/{member}_schedule.pdf"
                generate_pdf(member, two_week_events[member], today, filename, run_mode)
                send_email(team_emails[member], member, today, filename, run_mode, is_dry_run)
            else:
                print(f"No email configured for {member}, skipping.")

        print("Sending admin notification email.")
        send_notification_email(today, is_dry_run)
