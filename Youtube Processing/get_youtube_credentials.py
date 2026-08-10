import os
from google_auth_oauthlib.flow import InstalledAppFlow

# The scope for the YouTube Data API to manage your account
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

def main():
    print("Welcome to the YouTube Credentials Generator.")
    print("Please make sure you have 'client_secrets.json' in this directory.")

    if not os.path.exists('client_secrets.json'):
        print("Error: 'client_secrets.json' not found.")
        print("Please download it from the Google Cloud Console and place it in this directory.")
        return

    # Run the OAuth flow locally
    flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', SCOPES)
    credentials = flow.run_local_server(port=0)

    # Save the credentials locally as a JSON file
    output_filename = 'YOUTUBE_CREDENTIALS.json'
    with open(output_filename, 'w') as f:
        f.write(credentials.to_json())

    print("\n" + "="*50)
    print(f"SUCCESS! Credentials saved to {output_filename}")
    print("="*50)
    print("Please copy the ENTIRE contents of the file and paste it into")
    print("a GitHub Repository Secret named: YOUTUBE_CREDENTIALS_JSON")
    print("\nHere is the content of the file for your convenience:\n")
    print(credentials.to_json())
    print("\n" + "="*50)

if __name__ == '__main__':
    main()
