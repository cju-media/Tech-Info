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
                if pdf_path and os.path.exists(pdf_path):
                    print(f"Reprocessing local PDF: {pdf_path}")
                    is_communion, speaker_info = extract_pdf_info(pdf_path, date_str)

                    worship_scripts[date_str]['isCommunion'] = is_communion
                    worship_scripts[date_str]['speakerInfo'] = speaker_info
                    print(f"[Record] Updated repo JSON for {date_str}: speakerInfo='{speaker_info}'")
                    processed_count += 1
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
