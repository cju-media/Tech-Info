import os
import json
import time
from datetime import datetime, timedelta
import dateutil.parser
import subprocess
import pytz
import re

def get_mtime(filepath):
    try:
        cmd = ["git", "log", "-1", "--format=%ct", "--", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        if out:
            return int(out)
    except Exception as e:
        pass
    return os.path.getmtime(filepath)

def format_date_for_ows(dt):
    return f"{dt.month}-{dt.day:02d}-{dt.strftime('%y')}"

def main():
    if not os.path.exists('../Worship Scripts/worship_scripts.json'):
        print("../Worship Scripts/worship_scripts.json not found.")
        return

    with open('../Worship Scripts/worship_scripts.json', 'r') as f:
        worship_scripts = json.load(f)

    tz = pytz.timezone('America/Los_Angeles')
    now = datetime.now(tz)

    # Calculate previous Sunday 12:00 AM (midnight)
    # If today is Sunday (6), subtract 7 days to get the previous Sunday.
    # If Monday (0), subtract 1.
    days_to_subtract = now.weekday() + 1
    previous_sunday_date = now.date() - timedelta(days=days_to_subtract)
    previous_sunday_dt = tz.localize(datetime.combine(previous_sunday_date, datetime.min.time()))
    previous_sunday_ts = previous_sunday_dt.timestamp()

    # Read template
    template_path = "DescriptionTemplate.txt"
    if not os.path.exists(template_path):
        print(f"Template not found at {template_path}")
        return
    with open(template_path, "r") as f:
        template_lines = f.read().splitlines()

    # Fetch OW Index to check if OW is posted
    try:
        import requests
        ow_index_resp = requests.get('https://raw.githubusercontent.com/TheCathedralFCCLA/OW/refs/heads/main/OWs/index.json')
        ow_index = ow_index_resp.json()
    except Exception as e:
        print(f"Error fetching OW index: {e}")
        ow_index = {}

    # Read service titles state to ensure we only process if titles have been updated for this OW
    service_titles_state_path = "../Worship Scripts/service_titles_state.json"
    last_processed_sha = None
    if os.path.exists(service_titles_state_path):
        try:
            with open(service_titles_state_path, "r") as f:
                state_data = json.load(f)
                last_processed_sha = state_data.get("last_processed_sha")
        except Exception as e:
            print(f"Error reading service titles state: {e}")

    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    changes_made = False

    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            doc_dt_aware = doc_dt
            if doc_dt_aware.tzinfo is None:
                doc_dt_aware = tz.localize(doc_dt_aware)

            days_until = (doc_dt_aware.date() - now.date()).days
            if 0 <= days_until <= 7:
                modified_time = data.get('modifiedTime')
                youtube_modified_time = data.get('youtubeDescriptionModifiedTime')

                output_dir = os.path.join("Processed Scripts", date_str)
                txt_output_path = os.path.join(output_dir, f"Description {date_str}.txt")
                txt_exists = os.path.exists(txt_output_path)

                force_update = str(os.environ.get('FORCE_UPDATE', 'false')).lower() == 'true'

                if youtube_modified_time == modified_time and youtube_modified_time is not None and txt_exists and not force_update:
                    print(f"Skipping {date_str} (YouTube description is up to date and text file exists).")
                    continue

                # Find the OW URL using the index.json mapping
                ow_date_key_1 = f"{doc_dt_aware.month}-{doc_dt_aware.day}-{doc_dt_aware.strftime('%y')}"
                ow_date_key_2 = f"{doc_dt_aware.month}-{doc_dt_aware.day:02d}-{doc_dt_aware.strftime('%y')}"

                ow_info = ow_index.get(ow_date_key_1)
                if not ow_info:
                    ow_info = ow_index.get(ow_date_key_2)

                if not ow_info or not ow_info.get('url'):
                    print(f"No OW found in remote index for date {ow_date_key_1} or {ow_date_key_2}. Waiting for OW to be posted...")
                    continue

                ow_url = ow_info['url']
                filename = ow_url.split("/")[-1]

                # Check if the service titles have been processed for this specific OW
                api_url = f"https://api.github.com/repos/TheCathedralFCCLA/OW/contents/OWs/{filename}"
                try:
                    api_response = requests.get(api_url, headers=headers)
                    if api_response.status_code == 200:
                        current_sha = api_response.json().get("sha")
                        if last_processed_sha != current_sha and not force_update:
                            print(f"Service titles not yet updated for {date_str} (current SHA: {current_sha}, last processed: {last_processed_sha}). Waiting for service_titles_checker to run.")
                            continue
                except Exception as e:
                    print(f"Error fetching SHA for {filename}: {e}")
                    if not force_update:
                        continue

                print(f"Processing script for {date_str} (happening in {days_until} days)...")

                output_lines = []
                for line in template_lines:
                    txt_files = re.findall(r'[\w-]+\.txt', line)

                    line_valid = True
                    modified_line = line

                    for txt_file in txt_files:
                        if txt_file == "DescriptionBoiler.txt":
                            filepath = txt_file
                        else:
                            filepath = os.path.join("../Worship Scripts/service-titles", txt_file)

                        if not os.path.exists(filepath):
                            line_valid = False
                            break

                        with open(filepath, 'r') as f:
                            content = f.read().strip()

                        if not content:
                            line_valid = False
                            break

                        if txt_file != "DescriptionBoiler.txt":
                            mtime = get_mtime(filepath)
                            if mtime < previous_sunday_ts:
                                print(f"File {txt_file} is older than previous Sunday. Skipping line.")
                                line_valid = False
                                break

                        if txt_file == "DescriptionBoiler.txt":
                            ows_date_str = format_date_for_ows(doc_dt_aware)
                            content = re.sub(
                                r'(https://www\.fccla\.org/ows/)(?:\[DATE OF SERVICE\]|[\w-]+)',
                                rf'\g<1>{ows_date_str}',
                                content
                            )

                        modified_line = modified_line.replace(txt_file, content)

                    if line_valid:
                        output_lines.append(modified_line)

                description = "\n".join(output_lines)

                os.makedirs(output_dir, exist_ok=True)

                with open(txt_output_path, 'w', encoding='utf-8') as f:
                    f.write(description)

                print(f"Saved YouTube description text to {txt_output_path}")

                worship_scripts[date_str]['youtubeDescriptionModifiedTime'] = modified_time
                changes_made = True

        except Exception as e:
            print(f"Error processing {date_str}: {e}")

    if changes_made:
        with open('../Worship Scripts/worship_scripts.json', 'w') as out:
            json.dump(worship_scripts, out, indent=2)
        print("Updated ../Worship Scripts/worship_scripts.json with new YouTube description timestamps.")

if __name__ == '__main__':
    main()
