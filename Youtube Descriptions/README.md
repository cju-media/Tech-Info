# YouTube OSC Server (Node.js)

The OSC timestamp server has been rewritten in Node.js for improved reliability and the inclusion of a real-time web UI.

## Local Setup Instructions

If you are running the `osc_server.js` script on your local machine (like your MacBook), you need to install the Node.js packages first.

1. **Install Node.js**: Ensure you have Node installed on your system.
2. **Install Dependencies**: Open your terminal, navigate to this directory, and run `npm install` to download all the necessary modules (like `osc`, `googleapis`, `express`, etc.):
   ```bash
   cd "Youtube Descriptions"
   npm install
   ```
3. **Set your Credentials**: The server requires the `YOUTUBE_CREDENTIALS_JSON` environment variable to authenticate with YouTube. You can run it like this:
   ```bash
   YOUTUBE_CREDENTIALS_JSON='{...your json string...}' node osc_server.js
   ```
   *(Note: You can follow the instructions in `YOUTUBE_API_SETUP.md` to generate this JSON if you don't have it locally).*

## Web UI

Once running, the web UI will be accessible at:
[http://localhost:3000](http://localhost:3000)

This UI will show the active stream title, calculate the elapsed time, and show the remaining `chapters.txt` sections waiting to be timestamped by an OSC command.
