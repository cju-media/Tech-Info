# Tech Info Dashboard & Tools

This repository contains various tools, scripts, and static frontends used for tech coordination and data management.

## Google Drive Downloader (`/downloader`)

The Google Drive Downloader is a serverless, static frontend application that allows users to paste a link to a public Google Drive folder, browse its contents, and download selected files or folders.

### Downloading Behavior
When a user selects a folder for download, the application recursively traverses the folder and fetches **every individual file** inside it. To provide a seamless user experience and prevent modern web browsers from blocking multiple simultaneous file downloads, the application reconstructs the exact folder hierarchy client-side and packages all the individual files into a single `.zip` archive before prompting the final download.

### Configuration: Setting up the Google Drive API Key

The downloader requires a Google Drive API Key to fetch files. This key is securely injected into the application during the GitHub Pages deployment process. You do not need to expose this key to end-users.

**How to obtain and configure the API Key:**

1. **Go to Google Cloud Console:** Visit [console.cloud.google.com](https://console.cloud.google.com/).
2. **Create a Project:** Create a new project or select an existing one.
3. **Enable Google Drive API:** Navigate to **APIs & Services > Library**, search for "Google Drive API", and click **Enable**.
4. **Create Credentials:**
   - Navigate to **APIs & Services > Credentials**.
   - Click **Create Credentials** and select **API key**.
5. **Secure Your API Key:**
   - Edit the newly created API key.
   - Under **Application restrictions**, select **HTTP referrers (web sites)** and add your GitHub Pages URL (e.g., `https://your-org.github.io/*`). This ensures the key can only be used from your hosted tool.
   - Under **API restrictions**, select **Restrict key** and check **Google Drive API**.
6. **Add to GitHub Secrets:**
   - Copy the API Key.
   - Go to this repository on GitHub.
   - Navigate to **Settings > Secrets and variables > Actions**.
   - Click **New repository secret**.
   - Name it `GDRIVE_API_KEY` and paste your API key as the value.

Once saved, the next time the `Deploy to GitHub Pages` workflow runs, the key will be securely compiled into the downloader app.