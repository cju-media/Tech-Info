import os
import sys
import json
import time
from datetime import datetime
import dateutil.parser
import pypdf
from google import genai
from weasyprint import HTML

PROMPT = """
Create a Youtube Description
    - Add all of these into a single combined list, always starting with announcements. Only list the musical piece performed, all other parts of the service condense to “Sunday Service”. Do not list the lyrics. For the organ prelude do not list the performer for every piece unless the performer is different. Include Gloria Deo and Doxology in the Sunday Service sections. Have a line break in-between each section (Not the Organ Prelude). Do not write anything before announcements. Include the sermon as its own section. Format as TITLE OF SECTION, TITLE OF WORK - ETC. LVF should be written as Laura Vail Fregin. ML should be written as Michael Lehman
    - Example:
        - Announcements
        - Organ-Prelude Concert, NAME, Organ-In-Residence
            - Piece 1
            - Piece 2
            - Etc
        - Worship Service
        - Hymn of Gathering, NAME OF HYMN
        - Worship Service
        - Introit, NAME OF PIECE, COMPOSER - GROUP PERFORMING
        - ETC.
"""

def generate_youtube_description(text, date_str):
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("No GEMINI_API_KEY found, skipping YouTube description generation.")
        return None

    client = genai.Client(api_key=api_key)
    model_name = 'gemini-3.5-flash'

    full_prompt = f"{PROMPT}\n\nText:\n{text}"

    print(f"[Gemini] Requesting YouTube description for {date_str}...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=full_prompt
        )
        if response and response.text:
            print(f"[Gemini] Successfully generated description for {date_str}")
            return response.text.strip()
    except Exception as e:
        print(f"[Gemini] API error: {str(e)}")

    return None

def main():
    if not os.path.exists('worship_scripts.json'):
        print("worship_scripts.json not found.")
        return

    with open('worship_scripts.json', 'r') as f:
        worship_scripts = json.load(f)

    now = datetime.now()
    changes_made = False

    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            # Process upcoming Sundays (or today)
            if doc_dt.date() >= now.date():
                script_path = data.get('path')
                if not script_path or not os.path.exists(script_path):
                    continue

                modified_time = data.get('modifiedTime')
                youtube_modified_time = data.get('youtubeDescriptionModifiedTime')

                # Only process if the script has been updated since we last generated the description
                if youtube_modified_time == modified_time and youtube_modified_time is not None:
                    print(f"Skipping {date_str} (YouTube description is up to date).")
                    continue

                print(f"Processing script for {date_str}...")

                # Extract text from PDF
                full_text = ""
                try:
                    reader = pypdf.PdfReader(script_path)
                    for page in reader.pages:
                        page_text = page.extract_text()
                        if page_text:
                            full_text += page_text + "\n"
                except Exception as e:
                    print(f"Error reading PDF {script_path}: {e}")
                    continue

                if not full_text:
                    print(f"No text extracted from {script_path}")
                    continue

                # Generate Description
                description = generate_youtube_description(full_text, date_str)
                if not description:
                    continue

                # Save to PDF
                output_dir = os.path.join("Processed Scripts", date_str)
                os.makedirs(output_dir, exist_ok=True)
                pdf_output_path = os.path.join(output_dir, "youtube_description.pdf")

                html_content = f"""
                <html>
                    <head>
                        <style>
                            body {{ font-family: sans-serif; padding: 40px; white-space: pre-wrap; }}
                            h1 {{ text-align: center; color: #333; }}
                        </style>
                    </head>
                    <body>
                        <h1>YouTube Description - {date_str}</h1>
                        <div>{description}</div>
                    </body>
                </html>
                """

                HTML(string=html_content).write_pdf(pdf_output_path)
                print(f"Saved YouTube description PDF to {pdf_output_path}")

                # Update JSON
                worship_scripts[date_str]['youtubeDescriptionModifiedTime'] = modified_time
                changes_made = True

                # Sleep to avoid rate limits if there are multiple to process
                time.sleep(15)

        except Exception as e:
            print(f"Error processing {date_str}: {e}")

    if changes_made:
        with open('worship_scripts.json', 'w') as out:
            json.dump(worship_scripts, out, indent=2)
        print("Updated worship_scripts.json with new YouTube description timestamps.")

if __name__ == '__main__':
    main()
