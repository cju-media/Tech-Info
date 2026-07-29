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
    days_to_subtract = now.weekday() + 1
    previous_sunday_date = now.date() - timedelta(days=days_to_subtract)
    previous_sunday_dt = tz.localize(datetime.combine(previous_sunday_date, datetime.min.time()))
    previous_sunday_ts = previous_sunday_dt.timestamp()

    template_path = "DescriptionTemplate.txt"
    if not os.path.exists(template_path):
        print(f"Template not found at {template_path}")
        return
    with open(template_path, "r") as f:
        template_lines = f.read().splitlines()

    try:
        import requests
        ow_index_resp = requests.get('https://raw.githubusercontent.com/TheCathedralFCCLA/OW/refs/heads/main/OWs/index.json')
        ow_index = ow_index_resp.json()
    except Exception as e:
        print(f"Error fetching OW index: {e}")
        ow_index = {}

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

    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            doc_dt_aware = doc_dt
            if doc_dt_aware.tzinfo is None:
                doc_dt_aware = tz.localize(doc_dt_aware)

            days_until = (doc_dt_aware.date() - now.date()).days
            if 0 <= days_until <= 7:
                desc_output_path = "Description.txt"
                chapters_output_path = "chapters.txt"

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

                api_url = f"https://api.github.com/repos/TheCathedralFCCLA/OW/contents/OWs/{filename}"
                try:
                    api_response = requests.get(api_url, headers=headers)
                    if api_response.status_code == 200:
                        current_sha = api_response.json().get("sha")
                        if last_processed_sha != current_sha:
                            print(f"Service titles not yet updated for {date_str} (current SHA: {current_sha}, last processed: {last_processed_sha}). Waiting for service_titles_checker to run.")
                            continue
                except Exception as e:
                    print(f"Error fetching SHA for {filename}: {e}")
                    continue

                print(f"Processing script for {date_str} (happening in {days_until} days)...")

                boilerplate_lines = []
                chapter_lines = []

                current_section_title = None
                current_section_performer = None

                for line in template_lines:
                    txt_files = re.findall(r'[\w-]+\.txt', line)
                    if not txt_files:
                        chapter_lines.append(line)
                        continue

                    is_boiler = "DescriptionBoiler.txt" in txt_files

                    if not is_boiler:
                        max_index = 0
                        has_numbered = False
                        for txt_file in txt_files:
                            base_name = txt_file[:-4]
                            idx = 1
                            while os.path.exists(os.path.join("../Worship Scripts/service-titles", f"{base_name}{idx}.txt")):
                                idx += 1
                            if idx > 1:
                                max_index = max(max_index, idx - 1)
                                has_numbered = True

                        if has_numbered:
                            for i in range(0, max_index + 1):
                                line_valid = True
                                modified_line = line
                                for txt_file in txt_files:
                                    base_name = txt_file[:-4]
                                    if i == 0:
                                        num_filepath = os.path.join("../Worship Scripts/service-titles", txt_file)
                                    else:
                                        num_filepath = os.path.join("../Worship Scripts/service-titles", f"{base_name}{i}.txt")
                                        if not os.path.exists(num_filepath):
                                            num_filepath = os.path.join("../Worship Scripts/service-titles", txt_file)

                                    if not os.path.exists(num_filepath):
                                        line_valid = False
                                        break

                                    with open(num_filepath, 'r') as f:
                                        content = f.read().strip()

                                    if not content:
                                        line_valid = False
                                        break

                                    mtime = get_mtime(num_filepath)
                                    if mtime < previous_sunday_ts:
                                        print(f"File {os.path.basename(num_filepath)} is older than previous Sunday. Skipping line.")
                                        line_valid = False
                                        break

                                    modified_line = modified_line.replace(txt_file, content)

                                if line_valid:
                                    chapter_lines.append(modified_line)
                            continue

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

                        if not is_boiler:
                            mtime = get_mtime(filepath)
                            if mtime < previous_sunday_ts:
                                print(f"File {txt_file} is older than previous Sunday. Skipping line.")
                                line_valid = False
                                break

                        if is_boiler:
                            ows_date_str = format_date_for_ows(doc_dt_aware)
                            content = re.sub(
                                r'(https://www\.fccla\.org/ows/)(?:\[DATE OF SERVICE\]|[\w-]+)',
                                rf'\g<1>{ows_date_str}',
                                content
                            )

                        modified_line = modified_line.replace(txt_file, content)

                    if line_valid:
                        if is_boiler:
                            boilerplate_lines.append(modified_line)
                        else:
                            # Handling grouped items specifically (where a section has multiple numbered pieces)
                            # First, check if this line is setting up a section header (e.g., "Organ Prelude-Concert, prelude-performer.txt")
                            # We can identify headers that setup groups if the line doesn't resolve to a piece but has a comma separator.
                            # Since `DescriptionTemplate.txt` explicitly lists `prelude1.txt`, `prelude2.txt`, etc., we will track ANY section header.

                            # If the line contains a template file that wasn't found (and thus skipped), we wouldn't be here.
                            # Let's detect a section header by checking if the next template lines are numbered pieces of the same type.
                            # To keep it generic, if a line has a comma, we save it as a potential section title.
                            # "Organ Prelude-Concert, Sabrina Tyas" -> title: "Organ Prelude-Concert"

                            # Is this line a section setup? (It doesn't contain digits right before .txt in the original template line)
                            is_numbered_piece = bool(re.search(r'\d+\.txt', line))

                            # If we held a group setup header, but THIS line is not a numbered piece,
                            # it means the previous line was just a standalone comma line. We must flush it!
                            if isinstance(current_section_title, str) and not is_numbered_piece:
                                chapter_lines.append(previous_modified_line_held)
                                current_section_title = None
                                current_section_performer = None

                            if not is_numbered_piece and "," in modified_line:
                                parts = modified_line.split(',', 1)
                                potential_title = parts[0].strip()

                                current_section_title = potential_title
                                current_section_performer = None # Reset performer state for the new section
                                previous_modified_line_held = modified_line # Hold the full string just in case it doesn't get consumed
                                continue # Skip appending the header itself, wait for the first piece

                            if is_numbered_piece and current_section_title:
                                piece_content = modified_line.strip()

                                # Extract performer from piece_content if it exists (Format: PIECE NAME - COMPOSER, PERFORMER)
                                piece_parts = piece_content.rsplit(", ", 1)
                                piece_base = piece_parts[0]
                                piece_performer = piece_parts[1] if len(piece_parts) > 1 else None

                                # Is this the first item in the section?
                                if current_section_title is not True: # It's a string title
                                    formatted = f"{current_section_title} - {piece_base}"
                                    if piece_performer:
                                        formatted += f", {piece_performer}"
                                        current_section_performer = piece_performer
                                    chapter_lines.append(formatted)

                                    # Set to True so we know we are inside a section but past the first item
                                    current_section_title = True
                                else:
                                    # It's a subsequent item
                                    formatted = piece_base
                                    if piece_performer and piece_performer != current_section_performer:
                                        formatted += f", {piece_performer}"
                                        current_section_performer = piece_performer

                                    chapter_lines.append(formatted)

                                continue

                            # For any other normal chapter line, clear the grouping state
                            current_section_title = None
                            current_section_performer = None
                            chapter_lines.append(modified_line)

                if isinstance(current_section_title, str):
                    chapter_lines.append(previous_modified_line_held)

                # Save Description.txt (boilerplate only)
                with open(desc_output_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(boilerplate_lines))
                print(f"Saved boilerplate to {desc_output_path}")

                # Save chapters.txt (sections only)
                with open(chapters_output_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(chapter_lines))
                print(f"Saved chapters to {chapters_output_path}")

        except Exception as e:
            print(f"Error processing {date_str}: {e}")

if __name__ == '__main__':
    main()
