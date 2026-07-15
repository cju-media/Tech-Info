import os
import sys
import json
import time
from datetime import datetime, timedelta
import dateutil.parser
import pypdf
from google import genai

def get_prompt():
    prompt_path = os.path.join("Processed Scripts", "YoutubePrompt.md")
    if os.path.exists(prompt_path):
        with open(prompt_path, 'r') as f:
            return f.read()
    else:
        print(f"Warning: Prompt file not found at {prompt_path}")
        return ""

def generate_youtube_description(text, date_str, base_prompt):
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("No GEMINI_API_KEY found, skipping YouTube description generation.")
        return None

    if not base_prompt:
        print("No prompt provided, skipping YouTube description generation.")
        return None

    client = genai.Client(api_key=api_key)
    model_name = 'gemini-3.5-flash'

    full_prompt = f"{base_prompt}\n\nText:\n{text}"

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

    base_prompt = get_prompt()

    for date_str, data in worship_scripts.items():
        try:
            doc_dt = dateutil.parser.parse(date_str)
            # Process upcoming Sundays (or today) that are within the current week (7 days)
            days_until = (doc_dt.date() - now.date()).days
            if 0 <= days_until <= 7:
                script_path = data.get('path')
                if not script_path or not os.path.exists(script_path):
                    continue

                modified_time = data.get('modifiedTime')
                youtube_modified_time = data.get('youtubeDescriptionModifiedTime')

                # Only process if the script has been updated since we last generated the description
                if youtube_modified_time == modified_time and youtube_modified_time is not None:
                    print(f"Skipping {date_str} (YouTube description is up to date).")
                    continue

                print(f"Processing script for {date_str} (happening in {days_until} days)...")

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
                description = generate_youtube_description(full_text, date_str, base_prompt)
                if not description:
                    continue

                # Save to txt
                output_dir = os.path.join("Processed Scripts", date_str)
                os.makedirs(output_dir, exist_ok=True)
                txt_output_path = os.path.join(output_dir, f"Description {date_str}.txt")

                with open(txt_output_path, 'w', encoding='utf-8') as f:
                    f.write(description)

                print(f"Saved YouTube description text to {txt_output_path}")

                # Update JSON
                worship_scripts[date_str]['youtubeDescriptionModifiedTime'] = modified_time
                changes_made = True

                # Sleep to avoid rate limits if there are multiple to process
                time.sleep(15)
            elif days_until > 7:
                 print(f"Skipping {date_str} (Service is more than a week away: {days_until} days).")

        except Exception as e:
            print(f"Error processing {date_str}: {e}")

    if changes_made:
        with open('worship_scripts.json', 'w') as out:
            json.dump(worship_scripts, out, indent=2)
        print("Updated worship_scripts.json with new YouTube description timestamps.")

if __name__ == '__main__':
    main()
