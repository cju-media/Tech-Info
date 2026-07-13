import os
import json
from datetime import datetime
import dateutil.parser
from update_worship_scripts import extract_pdf_info

def main():
    if not os.path.exists('worship_scripts.json'):
        print("worship_scripts.json not found.")
        return

    with open('worship_scripts.json', 'r') as f:
        worship_scripts = json.load(f)

    now = datetime.now()
    processed_count = 0

    print("Identifying upcoming scripts to reprocess...")
    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            if doc_dt.date() >= now.date():
                pdf_path = data.get('path')
                # In GitHub Actions, the service-scripts folder is checked in.
                if pdf_path and os.path.exists(pdf_path):
                    print(f"Reprocessing local PDF: {pdf_path}")

                    # Fetching existing data
                    existing_speaker_info = data.get('speakerInfo')

                    is_communion, speaker_info = extract_pdf_info(pdf_path, date_str)

                    # Always save isCommunion because it is extracted locally
                    worship_scripts[date_str]['isCommunion'] = is_communion
                    processed_count += 1

                    # Only overwrite speakerInfo if we successfully extracted new info, to avoid destructive nulls
                    if speaker_info is not None:
                        worship_scripts[date_str]['speakerInfo'] = speaker_info
                        print(f"[Record] Updated repo JSON for {date_str}: isCommunion={is_communion}, speakerInfo='{speaker_info}'")
                    else:
                        print(f"[Record] Gemini returned None for {date_str}. isCommunion={is_communion}. Preserving existing speaker_info: '{existing_speaker_info}'")
                else:
                    print(f"PDF missing for {date_str}, skipping.")
        except Exception as e:
            print(f"Error parsing date or reading {date_str}: {e}")

    if processed_count > 0:
        with open('worship_scripts.json', 'w') as out:
            json.dump(worship_scripts, out, indent=2)
        print(f"Successfully reprocessed and saved {processed_count} scripts.")
    else:
        print("No upcoming scripts found to reprocess.")

if __name__ == '__main__':
    main()
