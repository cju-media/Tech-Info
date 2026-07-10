import sys
import os
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime
import re
import urllib.request
import math

# Coordinates for 540 Commonwealth Ave
LAT_540 = 34.0645671
LON_540 = -118.2855647

def haversine(lat1, lon1, lat2, lon2):
    R = 3959.87433
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2) * math.sin(dlat/2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon/2) * math.sin(dlon/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def fetch_events_from_sheet():
    sheet_id = '1UC8vgy89W14bVEWROqdUc9VgkMTGykC5ZZJqSDmi2-A'
    gid = '251348517'
    url = f'https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?gid={gid}&headers=0'

    try:
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
            events.append(cells)

        return events
    except Exception as e:
        print(f"Failed to fetch FCCLA events: {e}")
        return []

def get_upcoming_fccla_events(events):
    upcoming = []
    current_date = datetime.now()

    for row in events:
        if not row[0] or not isinstance(row[0], str):
            continue

        date_str = row[0].strip()
        match = re.search(r'\d{1,2}/\d{1,2}/\d{2,4}', date_str)
        if match:
            date_part = match.group(0)
            try:
                dt = datetime.strptime(date_part, '%m/%d/%Y')
                if dt >= current_date:
                    upcoming.append({
                        "date": date_part,
                        "name": row[1] if row[1] else "Unknown Event",
                        "spaces": row[2] if row[2] else "",
                        "times": row[3] if row[3] else "",
                    })
            except ValueError:
                try:
                    dt = datetime.strptime(date_part, '%m/%d/%y')
                    if dt >= current_date:
                        upcoming.append({
                            "date": date_part,
                            "name": row[1] if row[1] else "Unknown Event",
                            "spaces": row[2] if row[2] else "",
                            "times": row[3] if row[3] else "",
                        })
                except ValueError:
                    pass

    return upcoming

def get_public_events():
    """
    Fetches real upcoming public street closures, special events, and carnivals from the
    LA City Open Data Portal for Temporary Special Event (TSE) Permits.
    Filters for events near 540 Commonwealth Ave (Koreatown/Westlake/MacArthur Park area).
    """
    public_events = []
    current_date = datetime.now()
    url = "https://data.lacity.org/resource/8spw-3fhx.json?$limit=50000"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req)
        data = json.loads(response.read().decode('utf-8'))

        target_keywords = [
            'commonwealth', 'lafayette', 'macarthur', 'koreatown',
            'westlake', 'wilshire', '7th', '8th', '6th', 'hoover', 'vermont', 'shatto'
        ]

        for e in data:
            try:
                date_str = str(e.get('event_start_date', ''))
                end_str = str(e.get('event_end_date', ''))

                # location and address
                addr_start = str(e.get('address_start', ''))
                addr_name = str(e.get('addr_name', ''))
                loc_val = e.get('location')
                loc = str(loc_val) if loc_val else f"{addr_start} {addr_name}".strip()
                loc_lower = loc.lower()

                lat_lon = e.get('lat_lon')
                lat_str = str(lat_lon.get('latitude')) if isinstance(lat_lon, dict) and 'latitude' in lat_lon else None
                lon_str = str(lat_lon.get('longitude')) if isinstance(lat_lon, dict) and 'longitude' in lat_lon else None

                is_near = False

                # Check by distance if lat/lon is available (within 1 mile radius for broader street closures)
                if lat_str and lon_str and lat_str != 'None' and lon_str != 'None':
                    try:
                        dist = haversine(LAT_540, LON_540, float(lat_str), float(lon_str))
                        if dist <= 1.0:
                            is_near = True
                    except ValueError:
                        pass

                # Fallback to keyword matching nearby major streets/parks
                if not is_near:
                    if any(keyword in loc_lower for keyword in target_keywords):
                        is_near = True

                if is_near:
                    name_val = e.get('event_name')
                    name = str(name_val) if name_val else 'LA City Special Event'
                    name_lower = name.lower()

                    # Exclude unrelated wide-net catches
                    if 'usc' in loc_lower or 'usc' in name_lower:
                        continue

                    # We specifically want street fairs, carnivals, block parties, large closures
                    desc_val = e.get('work_desc')
                    desc_lower = str(desc_val).lower() if desc_val else ''

                    is_target_event = any(kw in name_lower or kw in desc_lower for kw in ['carnival', 'festival', 'fair', 'street', 'block party', 'closure', 'market', 'parade', 'park'])

                    if not is_target_event:
                         # Also include anything clearly big at Lafayette or MacArthur park
                         if 'lafayette' in loc_lower or 'macarthur' in loc_lower:
                             is_target_event = True

                    if is_target_event:
                        # Check dates: is it future relative to today, OR currently ongoing
                        evt_date = None
                        end_date = None
                        if date_str:
                            try:
                                evt_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S.%f")
                            except Exception:
                                pass
                        if end_str:
                            try:
                                end_date = datetime.strptime(end_str, "%Y-%m-%dT%H:%M:%S.%f")
                            except Exception:
                                pass

                        is_future = False
                        if evt_date and evt_date >= current_date:
                            is_future = True
                        elif end_date and end_date >= current_date:
                            is_future = True

                        if is_future:
                            public_events.append({
                                "name": name,
                                "date": date_str.split('T')[0] if 'T' in date_str else date_str,
                                "location": loc.replace('\n', ', '),
                                "type": "Street Closure / Festival (LA City Permit)",
                                "source": "https://data.lacity.org/resource/8spw-3fhx"
                            })

            except Exception as loop_error:
                # Catch any individual event parsing error so the rest of the list succeeds
                # print(f"Error parsing event: {loop_error}")
                continue

    except Exception as e:
        print(f"Error fetching LA City data: {e}")

    return public_events

def export_public_events(public_events, filepath="public_events.json"):
    with open(filepath, 'w') as f:
        json.dump(public_events, f, indent=2)
    print(f"Exported public events to {filepath}")

def send_rf_email(public_events, fccla_events, to_email="cameron@cju.media"):
    smtp_email = os.environ.get('SMTP_EMAIL')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.mail.me.com')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))

    msg = EmailMessage()
    msg['Subject'] = 'Upcoming RF Coordination Events (Local Area)'
    msg['From'] = smtp_email or 'rf-bot@example.com'
    msg['To'] = to_email

    body = "Hi Cameron,\n\nHere are the upcoming public street closures, special events, and carnivals in the Koreatown/Westlake area (~1 mile radius of 540 Commonwealth Ave) for RF coordination awareness:\n\n"

    if public_events:
        for event in public_events:
            body += f"- {event['name']}\n"
            body += f"  Date: {event['date']}\n"
            body += f"  Location: {event['location']}\n"
            body += f"  Type: {event['type']}\n\n"
    else:
        body += "No major public events/street closures found matching criteria in the active permit database at this time.\n\n"

    body += "---\n\nHere are the upcoming FCCLA events from the Google Sheet:\n\n"
    for event in fccla_events:
        body += f"- {event['date']}: {event['name']} ({event['times']}) in {event['spaces']}\n"

    body += "\nBest,\nCam-Bot\n"

    msg.set_content(body)

    if not smtp_email or not smtp_password:
        print("DRY RUN: Missing SMTP credentials. Would send email with content:")
        print(body)
        return

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_email, smtp_password)
        server.send_message(msg, to_addrs=[to_email])
        server.quit()
        print(f"Successfully sent RF coordination email to {to_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")
        sys.exit(1)

def main():
    public_events = get_public_events()
    export_public_events(public_events)

    raw_events = fetch_events_from_sheet()
    fccla_events = get_upcoming_fccla_events(raw_events)

    send_rf_email(public_events, fccla_events)

if __name__ == "__main__":
    main()
