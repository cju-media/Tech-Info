import os
import json
from datetime import datetime
import dateutil.parser
from update_worship_scripts import batch_get_speaker_info, extract_local_pdf_data

def main():
    if not os.path.exists('worship_scripts.json'):
        print("worship_scripts.json not found.")
        return

    with open('worship_scripts.json', 'r') as f:
        worship_scripts = json.load(f)

    now = datetime.now()
    scripts_to_query = {}

    print("Identifying upcoming scripts to reprocess...")
    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            if doc_dt.date() >= now.date():
                pdf_path = data.get('path')
                if pdf_path and os.path.exists(pdf_path):
                    print(f"Reading local PDF: {pdf_path}")
                    is_communion, full_text = extract_local_pdf_data(pdf_path)
                    if full_text:
                        scripts_to_query[date_str] = full_text
                        # Make sure communion flag is accurate too since we are reading it anyway
                        worship_scripts[date_str]['isCommunion'] = is_communion
                else:
                    print(f"PDF missing for {date_str}, skipping.")
        except Exception as e:
            print(f"Error parsing date or reading {date_str}: {e}")

    if scripts_to_query:
        print(f"Sending batch query to Gemini for {len(scripts_to_query)} scripts...")
        batch_results = batch_get_speaker_info(scripts_to_query)

        for date_str, speaker_info in batch_results.items():
            if date_str in worship_scripts:
                worship_scripts[date_str]['speakerInfo'] = speaker_info
                print(f"[Record] Updated repo JSON for {date_str}: speakerInfo='{speaker_info}'")

        with open('worship_scripts.json', 'w') as out:
            json.dump(worship_scripts, out, indent=2)
        print(f"Successfully reprocessed and saved {len(batch_results)} scripts.")
    else:
        print("No upcoming scripts found to reprocess.")

if __name__ == '__main__':
    main()
