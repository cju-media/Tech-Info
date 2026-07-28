const osc = require("osc");
const { google } = require("googleapis");
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const path = require("path");

const PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs";
const OSC_IP = "0.0.0.0";
const OSC_PORT = 8000;
const WEB_PORT = 3000;

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, "public")));

let currentService = null;
let currentVideo = null;
let overrideVideoId = null;

function getYouTubeService() {
    if (currentService) return currentService;

    const credsJson = process.env.YOUTUBE_CREDENTIALS_JSON;
    if (!credsJson) {
        console.error("Error: YOUTUBE_CREDENTIALS_JSON environment variable not found.");
        return null;
    }

    try {
        const credsInfo = JSON.parse(credsJson);
        const oauth2Client = new google.auth.OAuth2(
            credsInfo.client_id,
            credsInfo.client_secret
        );
        oauth2Client.setCredentials(credsInfo);

        currentService = google.youtube({
            version: 'v3',
            auth: oauth2Client
        });
        return currentService;
    } catch (e) {
        console.error(`Error authenticating with YouTube: ${e}`);
        return null;
    }
}

async function getLiveStream(service) {
    try {
        if (overrideVideoId) {
            const videoResponse = await service.videos.list({
                part: 'snippet,liveStreamingDetails',
                id: overrideVideoId
            });
            if (videoResponse.data.items && videoResponse.data.items.length > 0) {
                return videoResponse.data.items[0];
            }
            console.log(`Override video ID ${overrideVideoId} not found.`);
            return null;
        }

        const playlistResponse = await service.playlistItems.list({
            part: 'snippet',
            playlistId: PLAYLIST_ID,
            maxResults: 50
        });

        const videoIds = (playlistResponse.data.items || []).map(item => item.snippet.resourceId.videoId);
        if (!videoIds.length) {
            return null;
        }

        const videoResponse = await service.videos.list({
            part: 'snippet,liveStreamingDetails',
            id: videoIds.join(',')
        });

        // First look for 'live', fallback to 'upcoming' if no live streams found
        let foundUpcoming = null;
        for (const video of (videoResponse.data.items || [])) {
            const snippet = video.snippet || {};
            if (snippet.liveBroadcastContent === 'live' && video.liveStreamingDetails) {
                if (video.liveStreamingDetails.actualStartTime) {
                    return video;
                }
            } else if (snippet.liveBroadcastContent === 'upcoming' && !foundUpcoming) {
                foundUpcoming = video;
            }
        }

        return foundUpcoming;
    } catch (e) {
        console.error(`An error occurred getting streams from playlist: ${e}`);
    }

    return null;
}

function parseStateFromDescription(description, actualStartTime) {
    const lines = description.split('\n');
    const timestampPattern = /^(\d{1,2}:)?\d{1,2}:\d{2}\s+/;

    let past = [];
    let upcoming = [];

    let lastLinkIdx = -1;
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].includes('http://') || lines[i].includes('https://')) {
            lastLinkIdx = i;
        }
    }

    const startIdx = lastLinkIdx !== -1 ? lastLinkIdx + 1 : 0;

    for (let i = startIdx; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;

        if (timestampPattern.test(line)) {
            past.push(line);
        } else {
            upcoming.push(line);
        }
    }

    return {
        actualStartTime: actualStartTime,
        past: past,
        upcoming: upcoming
    };
}

async function fetchAndBroadcastState() {
    const service = getYouTubeService();
    if (!service) return;

    currentVideo = await getLiveStream(service);
    if (!currentVideo) {
        io.emit('stateUpdate', { error: "No active or upcoming live stream found." });
        return;
    }

    const actualStartTime = currentVideo.liveStreamingDetails?.actualStartTime || null;
    const snippet = currentVideo.snippet || {};
    const description = snippet.description || '';
    const title = snippet.title || 'Untitled Stream';

    const state = parseStateFromDescription(description, actualStartTime);
    state.title = title;

    io.emit('stateUpdate', state);
}

function addTimestampToDescription(description, elapsedStr) {
    const lines = description.split('\n');
    const timestampPattern = /^(\d{1,2}:)?\d{1,2}:\d{2}\s+/;

    let lastTimestampIdx = -1;
    for (let i = 0; i < lines.length; i++) {
        if (timestampPattern.test(lines[i].trim())) {
            lastTimestampIdx = i;
        }
    }

    if (lastTimestampIdx !== -1) {
        for (let i = lastTimestampIdx + 1; i < lines.length; i++) {
            if (lines[i].trim()) {
                lines[i] = `${elapsedStr} ${lines[i].trim()}`;
                return { newDesc: lines.join('\n'), changed: true };
            }
        }
        return { newDesc: description, changed: false };
    }

    let lastLinkIdx = -1;
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].includes('http://') || lines[i].includes('https://')) {
            lastLinkIdx = i;
        }
    }

    const startIdx = lastLinkIdx !== -1 ? lastLinkIdx + 1 : 0;
    for (let i = startIdx; i < lines.length; i++) {
        if (lines[i].trim()) {
            lines[i] = `${elapsedStr} ${lines[i].trim()}`;
            return { newDesc: lines.join('\n'), changed: true };
        }
    }

    return { newDesc: description, changed: false };
}

async function handleOscMessage(oscMsg) {
    console.log(`Received OSC message at address: ${oscMsg.address}`);

    const service = getYouTubeService();
    if (!service) {
        console.log("Could not get YouTube service.");
        return;
    }

    const video = await getLiveStream(service);
    if (!video) {
        console.log("No active live stream found.");
        return;
    }

    const actualStartTimeStr = video.liveStreamingDetails?.actualStartTime;
    if (!actualStartTimeStr) {
        console.log("No actual start time found on the stream. Cannot calculate elapsed time.");
        return;
    }

    const startTime = new Date(actualStartTimeStr).getTime();
    const now = Date.now();
    let totalSeconds = Math.floor((now - startTime) / 1000);

    if (totalSeconds < 0) totalSeconds = 0;

    const hours = Math.floor(totalSeconds / 3600);
    const remainder = totalSeconds % 3600;
    const minutes = Math.floor(remainder / 60);
    const seconds = remainder % 60;

    const secondsStr = seconds.toString().padStart(2, '0');
    let elapsedStr = hours > 0
        ? `${hours}:${minutes.toString().padStart(2, '0')}:${secondsStr}`
        : `${minutes}:${secondsStr}`;

    console.log(`Calculated elapsed time: ${elapsedStr}`);

    const snippet = video.snippet || {};
    const currentDesc = snippet.description || '';

    const { newDesc, changed } = addTimestampToDescription(currentDesc, elapsedStr);

    if (changed) {
        console.log(`Adding timestamp '${elapsedStr}' to description...`);
        snippet.description = newDesc;

        try {
            await service.videos.update({
                part: 'snippet',
                requestBody: {
                    id: video.id,
                    snippet: snippet
                }
            });
            console.log("Successfully updated YouTube description.");

            // Re-fetch and broadcast to UI
            await fetchAndBroadcastState();
        } catch (e) {
            console.error(`Failed to update description: ${e}`);
        }
    } else {
        console.log("Could not find a suitable line to add a timestamp to, or description is fully timestamped.");
    }
}

function extractVideoId(urlStr) {
    try {
        const url = new URL(urlStr);
        if (url.hostname.includes('youtube.com')) {
            if (url.searchParams.has('v')) {
                return url.searchParams.get('v');
            }
        } else if (url.hostname.includes('youtu.be')) {
            return url.pathname.slice(1); // removes leading slash
        }
    } catch (e) {
        return null; // invalid URL
    }
    return urlStr; // might just be passing an ID directly
}

io.on("connection", (socket) => {
    console.log("Client connected to UI");
    fetchAndBroadcastState();

    socket.on("requestState", () => {
        fetchAndBroadcastState();
    });

    socket.on("setOverrideLink", (link) => {
        const vId = extractVideoId(link);
        if (vId) {
            console.log(`Setting override video ID to: ${vId}`);
            overrideVideoId = vId;
            fetchAndBroadcastState();
        }
    });

    socket.on("clearOverrideLink", () => {
        console.log("Clearing override video ID. Reverting to default playlist lookup.");
        overrideVideoId = null;
        fetchAndBroadcastState();
    });
});

function main() {
    const udpPort = new osc.UDPPort({
        localAddress: OSC_IP,
        localPort: OSC_PORT,
        metadata: true
    });

    udpPort.on("message", (oscMsg) => {
        handleOscMessage(oscMsg).catch(console.error);
    });

    udpPort.on("error", (err) => {
        console.error("OSC Server Error:", err);
    });

    udpPort.on("ready", () => {
        console.log(`Starting OSC server on ${OSC_IP}:${OSC_PORT}...`);
    });

    udpPort.open();

    server.listen(WEB_PORT, () => {
        console.log(`Web UI listening on http://localhost:${WEB_PORT}`);
    });
}

if (require.main === module) {
    main();
}
