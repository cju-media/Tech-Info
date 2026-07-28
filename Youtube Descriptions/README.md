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
3. **Set your Credentials**: The server requires two environment variables.
   - `YOUTUBE_API_KEY`: Used to securely read the public title and start time of the active YouTube live stream.
   - `GITHUB_PAT`: Used to push the final timestamps securely to GitHub when the stream concludes.

   You can run the server like this:
   ```bash
   YOUTUBE_API_KEY='your_api_key' GITHUB_PAT='your_github_pat' node osc_server.js
   ```

## Web UI

Once running, the web UI will be accessible at:
[http://localhost:3671](http://localhost:3671)

This UI will show the active stream title, calculate the elapsed time, and show the remaining `chapters.txt` sections waiting to be timestamped by an OSC command. You can also manually configure the active OSC port from the UI's configuration menu.
