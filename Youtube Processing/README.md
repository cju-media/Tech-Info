# YouTube OSC Server (Node.js)

The OSC timestamp server has been rewritten in Node.js for improved reliability and the inclusion of a real-time web UI.

## Local Setup Instructions

If you are running the `osc_server.js` script on your local machine (like your MacBook), you need to install the Node.js packages first.

1. **Install Node.js**: Ensure you have Node installed on your system.
2. **Install Dependencies**: Open your terminal, navigate to this directory, and run `npm install` to download all the necessary modules (like `osc`, `express`, etc.):
   ```bash
   cd "Youtube Processing"
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
- **Timing Offset (seconds):** Added to every computed elapsed time to compensate for the fixed lag between placing a timing and it actually appearing in the recorded/broadcast video (encoder + ingest + transcode latency). Defaults to `7`, calibrated empirically -- increase it if timings land too early in the video, decrease (or use a negative value) if they land too late.

These values are saved locally to your machine (`osc_config.json`) and will automatically reload every time you start the server.

## OBS WebSocket Integration

The server can automatically trigger OBS to start and stop recording when the service enters the "Sermon" section of the service.
To enable this feature:
1. Ensure your local OBS Studio has its built-in WebSocket server enabled (Tools > WebSocket Server Settings). Ensure authentication is disabled (passwordless).
2. Open the **Settings** modal in the web UI.
3. Enter the IP address or hostname of the computer running OBS in the **OBS Hostname/IP** field (e.g., `localhost` or `192.168.1.5`).
4. Enter the corresponding WebSocket port in the **OBS Port** field (default is usually `4455`).

The system checks the active stream segment on every timing iteration. If the item contains the word "Sermon", it automatically connects to OBS via WebSocket and triggers `StartRecord`. When the next section begins, or if the stream ends, it connects and triggers `StopRecord`. This automation only runs during an active live stream and is disabled when using Sample Mode.

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

Additionally, the server constantly queries the YouTube API. When it detects that the live stream has ended, it will automatically stop the timer and use your configured GitHub PAT to push the `timings.txt` file back to the repository. Note that pushes to GitHub are only permitted for a stream **scheduled within the last day** (a real service: a Sunday, or Christmas Eve on a weekday) to safeguard the system from testing during the week.

## Description Updates Are Applied Exactly Once

`update_youtube_stream.py` also runs on an hourly chain (via "Generate YouTube Description"), not just when `timings.txt` is pushed. To keep that hourly poll from clobbering a manual fix made directly on YouTube (e.g. correcting a mistimed chapter in the description), the script records the hash of each generated description it actually applies to a stream in `description_push_state.json`. Once a given piece of generated content has been applied, later runs leave the live description alone -- even if it now differs from the generated version -- until the underlying content changes again (a fresh `timings.txt` push, an updated `Description.txt`, etc.), at which point it's cleared to update once more.

## Several Services on One Day (Christmas Eve)

Every OW gets its own generated folder, `Worship Scripts/services/<key>/` (title files, `Description.txt`, `chapters.txt`, and the `timings.txt` pushed after its stream), keyed by date, plus time when the OW's name has one: `2026-12-27` for a Sunday, `2026-12-24_1730` for `12.24.26 5:30pm OW.pdf`. OWs are processed as soon as they're uploaded, up to 30 days ahead, so all of Christmas Eve's and the Sunday after's can go up early. See `Worship Scripts/worship workflows/service_keys.py`.

- **Naming:** `12.24.26 5:30pm OW.pdf`, `12.24.26 8pm OW.pdf`, `12.24.26 11pm OW.pdf` (`530pm`, `_` separators, Draft/Final all work). The program link becomes `fccla.org/ows/12-24-26-530pm`.
- **Thumbnails:** upload each one on the dashboard with its date and **time** set. Each gets its own Drive folder (`12-24-2026 5:30pm`) and its own stream, created as soon as that service's OW has been processed. Title graphics are Sunday-only, so these are made by hand.
- **Live set:** `service-titles/`, `Description.txt` and `chapters.txt` hold the next service, switching an hour before the next one starts (or 3 hours after the last one started), never mid-stream. On Christmas Eve that's 5:30 → 8pm at 7pm → 11pm at 10pm → the Sunday at 2am.
- **Timings:** each push is filed under the stream that ended most recently, so the server works the same on any day a service streams; it follows the soonest upcoming stream and stays on one that just ended until its timings are pushed.
