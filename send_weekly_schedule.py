import urllib.request
import sys
import json
import re
import pandas as pd
from datetime import datetime, timedelta
import os
from weasyprint import HTML
import smtplib
from email.message import EmailMessage

TEAM_MEMBERS = ["Aria", "Danny", "Jaffe", "Kaspar", "Marc", "Saad"]

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

def send_email(to_email, member, start_date, pdf_filename, run_mode, is_dry_run=False):
    creds = get_smtp_credentials()
    cc_email = "cjohnston@fccla.org"

    if run_mode == 'update':
        subject = "New Schedule Assignment Notification"
        body = f"Hi {member},\n\nYou have been assigned to new upcoming events. Please find your complete upcoming schedule attached (new assignments are marked with *NEW*).\n\nBest,\nTech Team"
    else:
        subject = f"Your Tech Schedule - Next Two Weeks ({start_date.strftime('%b %d')})"
        body = f"Hi {member},\n\nPlease find your upcoming schedule for the next two weeks starting {start_date.strftime('%B %d, %Y')} attached.\n\nBest,\nTech Team"

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
        print(f"Subject: {subject}")
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
    body = f"Hello,\n\nThis is an automated notification. The weekly schedule workflow has successfully completed. Individual schedule PDFs for the next two weeks starting {start_date.strftime('%B %d, %Y')} have been generated and sent to the team members.\n\nBest,\nAutomated System"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send notification email to {to_email}")
        print(f"Subject: {subject}")
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

def send_admin_email(start_date, pdf_filenames, is_dry_run=False):
    creds = get_smtp_credentials()
    to_email = "cameron@cju.media"
    cc_email = "cjohnston@fccla.org"

    subject = f"All Team Tech Schedules - Next Two Weeks ({start_date.strftime('%b %d')})"
    body = f"Hi Cameron,\n\nPlease find the generated team schedules for the next two weeks starting {start_date.strftime('%B %d, %Y')} attached.\n\nBest,\nAutomated System"

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
        print(f"Subject: {subject}")
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

    body += "Best,\nAutomated System"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = creds['email'] or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    if is_dry_run or not creds['email'] or not creds['password']:
        print(f"DRY RUN: Would send availability email to {to_email}")
        print(f"Subject: {subject}")
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
    body = f"Hi,\n\nThis is a manual test run. Attached are PDFs for each team member showing ALL upcoming events, with every event marked as *NEW*.\n\nBest,\nAutomated System"

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
        print(f"Subject: {subject}")
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
            return json.load(f)
    return {member: [] for member in TEAM_MEMBERS}

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
    run_mode = os.environ.get('RUN_MODE', 'weekly') # 'weekly', 'admin', 'update', 'test', 'avail_check'

    print("Fetching events...")
    events = fetch_events_from_sheet()

    with open('team_emails.json', 'r') as f:
        team_emails = json.load(f)

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Update state logic
    current_state = get_assignment_state()
    all_upcoming_events = get_events_by_member(events, today, days_ahead=None)

    new_state = {member: [e['element_id'] for e in all_upcoming_events[member]] for member in TEAM_MEMBERS}
    state_changed = False
    members_with_new_assignments = {}

    for member in TEAM_MEMBERS:
        old_ids = set(current_state.get(member, []))
        new_ids = set(new_state[member])
        added_ids = new_ids - old_ids

        if added_ids:
            members_with_new_assignments[member] = added_ids
            state_changed = True

    # Always save the latest state
    save_assignment_state(new_state)

    os.makedirs('pdfs', exist_ok=True)
    generated_pdfs = []

    if run_mode == 'update':
        if not state_changed:
            print("No new assignments detected. Exiting.")
            sys.exit(0)

        print("New assignments detected. Sending updates to affected members.")
        for member, added_ids in members_with_new_assignments.items():
            if member in team_emails:
                filename = f"pdfs/{member}_schedule.pdf"
                # For update mode, we show ALL upcoming events (days_ahead=None)
                generate_pdf(member, all_upcoming_events[member], today, filename, run_mode, new_event_ids=added_ids)
                send_email(team_emails[member], member, today, filename, run_mode, is_dry_run)
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
