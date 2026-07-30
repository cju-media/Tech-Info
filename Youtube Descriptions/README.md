# YouTube OSC Server (Node.js)

The OSC timestamp server has been rewritten in Node.js for improved reliability and the inclusion of a real-time web UI.

## Local Setup Instructions

If you are running the `osc_server.js` script on your local machine (like your MacBook), you need to install the Node.js packages first.

1. **Install Node.js**: Ensure you have Node installed on your system.
2. **Install Dependencies**: Open your terminal, navigate to this directory, and run `npm install` to download all the necessary modules (like `osc`, `express`, etc.):
   ```bash
   cd "Youtube Descriptions"
   npm install
   ```
3. **Run the server**: You no longer need to pass environment variables in your terminal. You can simply run:
   ```bash
   node osc_server.js
   ```

## Web UI & Configuration

Once running, the web UI will be accessible at:
[http://localhost:3671](http://localhost:3671)

This UI will show the active stream title, calculate the elapsed time, and show the remaining `chapters.txt` sections waiting to be timestamped by an OSC command.

**Important:** Open the hamburger menu (☰) in the top right corner to set up your configuration. You can configure:
- **OSC Port:** The local UDP port to listen for OSC commands (defaults to 8000).
- **YouTube API Key:** Used to securely read the public title and start time of the active YouTube live stream.
- **GitHub PAT:** Used to push the final timestamps securely to GitHub when the stream concludes.

These values are saved locally to your machine (`osc_config.json`) and will automatically reload every time you start the server.

## OBS Webhook Integration

The server can automatically trigger OBS to start and stop recording when the service enters the "Sermon" section of the service.
To enable this feature:
1. Ensure your local OBS Studio has a websocket/webhook integration active (e.g., using a plugin or middleware that exposes a local REST API endpoint on `/start_recording` and `/stop_recording` to control recording).
2. Open the **Settings** modal in the web UI.
3. Enter the IP address or hostname of the computer running OBS in the **OBS Hostname/IP** field (e.g., `localhost` or `192.168.1.5`).
4. Enter the corresponding port in the **OBS Port** field.

The system checks the active stream segment on every timing iteration. If the item contains the word "Sermon", it automatically triggers a GET request to `http://<obs-host>:<obs-port>/start_recording`. When the next section begins, or if the stream ends, it triggers `http://<obs-host>:<obs-port>/stop_recording`. This automation only runs during an active live stream and is disabled when using Sample Mode.

### Generating a GitHub PAT
To push the timings file to the repository, you must configure a classic GitHub PAT (Personal Access Token).
1. Go to your GitHub account settings > **Developer settings** > **Personal access tokens** > **Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Give it a descriptive note (e.g., "YouTube OSC Server").
4. Select an expiration (or set it to 'No expiration').
5. Check the `repo` scope (this grants full control of private repositories, which is required to push files to `tech-schedule`).
6. Generate the token, copy it, and paste it into the "GitHub PAT" field in the server's Configuration modal.

## Adding Timestamps via OSC

The server listens for incoming OSC messages on the port you configured (default `8000`).
To prevent crosstalk with other local OSC integrations, the server **only** responds to the following specific OSC addresses:

- `/timings/forward` : Adds a timestamp to the current item and advances the arrow forward.
- `/timings/back` : Reverts the most recent timestamp and moves the arrow backward.

You can also manually click the "Next Timing" or "Previous Timing" arrows on the Web UI if you missed a cue.

## Auto-Reset and End of Service

To ensure a clean slate every week and prevent accidental testing pushes from overwriting a real service, the server features a weekly automatic reset logic:
- Every **Sunday at 12:00 AM Pacific Time**, the server will automatically drop any sample mode data or manual stream overrides.
- It will then automatically fetch the fresh `chapters.txt` for the upcoming Sunday service from GitHub.

Additionally, the server constantly queries the YouTube API. When it detects that the live stream has ended, it will automatically stop the timer and use your configured GitHub PAT to push the `timings.txt` file back to the repository. Note that pushes to GitHub are strictly permitted **only on Sundays** to safeguard the system from testing during the week.
