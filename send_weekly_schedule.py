import urllib.request
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

    for row in rows:
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
            events.append({
                'date_obj': date_obj,
                'Date': date_str,
                'Event': str(cells[1] or ''),
                'Venue': str(cells[2] or ''),
                'Call Time': str(cells[3] or ''),
                'Type': str(cells[4] or ''),
                'Assignment': str(cells[7] or ''),
                'Schedule': str(cells[8] or ''),
                'Tech': str(cells[9] or '')
            })

    return events

def get_weekly_events_by_member(events, start_date):
    end_date = start_date + timedelta(days=6) # 7 days inclusive

    weekly_events = [e for e in events if start_date <= e['date_obj'] <= end_date]
    weekly_events.sort(key=lambda x: x['date_obj'])

    events_by_member = {member: [] for member in TEAM_MEMBERS}

    for e in weekly_events:
        assignment = str(e['Assignment']).lower()
        for member in TEAM_MEMBERS:
            if member.lower() in assignment:
                events_by_member[member].append(e)

    return events_by_member

def generate_pdf(member, events, start_date, output_filename):
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, sans-serif; margin: 40px; color: #333; }}
            h1 {{ color: #007bff; text-align: center; border-bottom: 2px solid #007bff; padding-bottom: 10px; }}
            h2 {{ color: #555; text-align: center; font-weight: normal; margin-top: -10px; margin-bottom: 30px; }}
            .event {{ margin-bottom: 30px; border: 1px solid #ddd; border-radius: 8px; padding: 20px; page-break-inside: avoid; }}
            .date {{ font-size: 1.2em; font-weight: bold; color: #007bff; margin-bottom: 10px; }}
            .title {{ font-size: 1.4em; font-weight: bold; margin-bottom: 15px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
            th {{ width: 120px; color: #666; font-weight: bold; }}
            .no-events {{ text-align: center; font-size: 1.2em; color: #666; margin-top: 50px; font-style: italic; }}
        </style>
    </head>
    <body>
        <h1>Upcoming Tech Schedule</h1>
        <h2>{member} | Week of {start_date.strftime('%B %d, %Y')}</h2>
    """

    if not events:
        html_content += "<div class='no-events'>You have no scheduled events this week.</div>"
    else:
        for e in events:
            html_content += f"""
            <div class='event'>
                <div class='date'>{e['Date']}</div>
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

            html_content += """
                </table>
            </div>
            """

    html_content += "</body></html>"
    HTML(string=html_content).write_pdf(output_filename)

def send_email(to_email, member, start_date, pdf_filename, is_dry_run=False):
    smtp_email = os.environ.get('SMTP_EMAIL')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))

    subject = f"Your Tech Schedule - Week of {start_date.strftime('%b %d')}"
    body = f"Hi {member},\n\nPlease find your upcoming schedule for the week of {start_date.strftime('%B %d, %Y')} attached.\n\nBest,\nTech Team"

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = smtp_email or "dry-run@example.com"
    msg['To'] = to_email
    msg.set_content(body)

    with open(pdf_filename, 'rb') as f:
        pdf_data = f.read()
    msg.add_attachment(pdf_data, maintype='application', subtype='pdf', filename=f"{member}_schedule.pdf")

    if is_dry_run or not smtp_email or not smtp_password:
        print(f"DRY RUN: Would send email to {to_email}")
        print(f"Subject: {subject}")
        print(f"Attachment: {pdf_filename}")
        return

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
        print(f"Successfully sent email to {to_email}")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")

if __name__ == "__main__":
    is_dry_run = os.environ.get('DRY_RUN', '1') == '1'

    print("Fetching events...")
    events = fetch_events_from_sheet()

    with open('team_emails.json', 'r') as f:
        team_emails = json.load(f)

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    events_by_member = get_weekly_events_by_member(events, today)

    os.makedirs('pdfs', exist_ok=True)

    for member, member_events in events_by_member.items():
        if member in team_emails:
            filename = f"pdfs/{member}_schedule.pdf"
            generate_pdf(member, member_events, today, filename)
            send_email(team_emails[member], member, today, filename, is_dry_run)
        else:
            print(f"No email configured for {member}, skipping.")
