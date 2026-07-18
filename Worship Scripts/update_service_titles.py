import os
import sys
import json
import requests
import datetime
import zoneinfo
import pypdf
import io
from google import genai
from google.genai import types

def main():
    # 1. Check if today is Sunday
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now_pt = datetime.datetime.now(tz)

    if now_pt.weekday() == 6:
        print("Today is Sunday. Do not update text files. Exiting.")
        return

    # 2. Calculate the coming Sunday's date to check if PDF is titled for it
    days_ahead = 6 - now_pt.weekday()
    sunday = now_pt + datetime.timedelta(days=days_ahead)

    # PDF naming convention is like "3.1.26 OW.pdf"
    # So we construct possible filename prefixes
    month = sunday.month
    day = sunday.day
    year = sunday.year % 100

    target_prefix = f"{month}.{day}.{year} OW"

    print(f"Looking for PDF starting with: {target_prefix}")

    # 5. Fetch GitHub API for OWs
    api_url = "https://api.github.com/repos/TheCathedralFCCLA/OW/contents/OWs"

    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        api_response = requests.get(api_url, headers=headers)
        api_response.raise_for_status()
        ows_contents = api_response.json()
    except Exception as e:
        print(f"Error fetching OWs contents: {e}")
        return

    # 6. Find the target file
    target_file_info = None
    for item in ows_contents:
        if item["name"].startswith(target_prefix) and item["name"].endswith(".pdf"):
            target_file_info = item
            break

    if not target_file_info:
        print(f"No PDF found for coming Sunday ({target_prefix}). Exiting.")
        return

    target_filename = target_file_info["name"]
    print(f"Found target filename: {target_filename}")

    current_sha = target_file_info["sha"]
    download_url = target_file_info["download_url"]

    # 8. Compare with local state
    state_file = "service_titles_state.json"
    state_data = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state_data = json.load(f)
        except json.JSONDecodeError:
            pass

    last_processed_sha = state_data.get("last_processed_sha")
    if last_processed_sha == current_sha:
        print("PDF SHA matches last processed SHA. No updates needed. Exiting.")
        return

    # 9. Download the PDF
    print(f"Downloading PDF from {download_url}...")
    try:
        pdf_response = requests.get(download_url, headers=headers)
        pdf_response.raise_for_status()
        pdf_content = pdf_response.content
    except Exception as e:
        print(f"Error downloading PDF: {e}")
        return

    # 10. Extract text from PDF
    print("Extracting text from PDF...")
    pdf_text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_content))
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pdf_text += text + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return

    # 11. Read worship-prompt.txt
    prompt_file = "worship-prompt.txt"
    try:
        with open(prompt_file, "r") as f:
            prompt_template = f.read()
    except Exception as e:
        print(f"Error reading prompt file {prompt_file}: {e}")
        return

    # 12. Send to Gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY environment variable is missing.")
        return

    prompt = f"{prompt_template}\n\nPDF Text:\n{pdf_text}"
    print("Sending prompt to Gemini...")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=30000)
    )

    model_name = 'gemini-1.5-flash'

    try:
        gemini_response = client.models.generate_content(
            model=model_name,
            contents=prompt
        )
        if not gemini_response or not gemini_response.text:
            print("No response from Gemini.")
            return

        result_text = gemini_response.text.strip()
    except Exception as e:
        print(f"Error querying Gemini: {e}")
        return

    # 13, 14, 15. Parse output and write to text files
    print("Parsing Gemini output and updating text files...")

    titles_dir = "service-titles"
    if not os.path.exists(titles_dir):
        os.makedirs(titles_dir)

    # Clear all .txt files in the directory first
    for filename in os.listdir(titles_dir):
        if filename.endswith(".txt"):
            filepath = os.path.join(titles_dir, filename)
            with open(filepath, "w") as f:
                f.write("")

    # Parse Gemini output
    lines = result_text.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        parts = line.split(maxsplit=1)
        if len(parts) >= 1:
            file_key = parts[0]
            content = parts[1] if len(parts) > 1 else ""

            filename = f"{file_key}.txt"
            filepath = os.path.join(titles_dir, filename)

            if os.path.exists(filepath):
                with open(filepath, "w") as f:
                    f.write(content.strip())
            else:
                print(f"Warning: File {filename} does not exist in {titles_dir}. Skipping.")

    # Update state file
    state_data["last_processed_sha"] = current_sha
    try:
        with open(state_file, "w") as f:
            json.dump(state_data, f)
        print("Updated state file successfully.")
    except Exception as e:
        print(f"Error writing state file: {e}")
        return

    print("Update complete.")

if __name__ == "__main__":
    main()
