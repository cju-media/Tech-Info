# YouTube API Setup Instructions

To allow the GitHub Actions workflow to automatically update YouTube stream descriptions, you need to set up a Google Cloud Project with the YouTube Data API enabled and generate OAuth 2.0 credentials.

Follow these steps carefully:

## Step 1: Create a Google Cloud Project and Enable the API
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Go to **APIs & Services > Library**.
4. Search for "YouTube Data API v3" and click **Enable**.

## Step 2: Configure the OAuth Consent Screen
1. Go to **APIs & Services > OAuth consent screen**.
2. Choose **External** (if your account isn't in a Google Workspace) and click **Create**.
3. Fill in the required App information (App name, support email, developer contact).
4. Click **Save and Continue**.
5. On the Scopes page, click **Add or Remove Scopes**. Add the following scope:
   - `https://www.googleapis.com/auth/youtube.force-ssl` (Allows managing your YouTube account)
6. Click **Update** and then **Save and Continue**.
7. On the Test users page, add the Google Account that manages the YouTube channel as a test user. Click **Save and Continue**.

## Step 3: Create OAuth 2.0 Client IDs
1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials** and select **OAuth client ID**.
3. For Application type, select **Desktop app**.
4. Give it a name (e.g., "YouTube Description Updater") and click **Create**.
5. You will see a dialog with your Client ID and Client Secret. Click **Download JSON** and save the file to your computer as `client_secrets.json`. Keep this file safe!

## Step 4: Generate the Refresh Token JSON
Because GitHub Actions runs in a headless environment, it cannot perform the interactive Google Login flow. You must generate the credentials locally first.

1. Make sure you have python installed locally.
2. Install the necessary google authentication libraries:
   ```bash
   pip install google-auth-oauthlib
   ```
3. Place the `client_secrets.json` you downloaded in the same directory as the `get_youtube_credentials.py` script provided in this repository (`Youtube Descriptions/get_youtube_credentials.py`).
4. Run the script:
   ```bash
   python get_youtube_credentials.py
   ```
5. A browser window will open asking you to log in to your Google Account. Make sure you log in with the account that manages the YouTube channel.
6. Grant the application the requested permissions.
7. After successful authentication, the script will create a file named `YOUTUBE_CREDENTIALS.json` and print its contents to your terminal.

## Step 5: Add the Credentials to GitHub Secrets
1. Go to your repository on GitHub.
2. Navigate to **Settings > Secrets and variables > Actions**.
3. Click **New repository secret**.
4. Name the secret: `YOUTUBE_CREDENTIALS_JSON`
5. Paste the **entire contents** of the `YOUTUBE_CREDENTIALS.json` file (generated in Step 4) into the Secret value field.
6. Click **Add secret**.

The automated workflow will now have the necessary permissions to update the upcoming live stream descriptions!
