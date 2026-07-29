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
