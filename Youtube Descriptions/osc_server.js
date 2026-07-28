const osc = require("osc");
const { google } = require("googleapis");
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const path = require("path");
const fetch = require("node-fetch");
const fs = require("fs");

const PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs";
const OSC_IP = "0.0.0.0";
const WEB_PORT = 3671;
const CONFIG_FILE = path.join(__dirname, "osc_config.json");

const CHAPTERS_URL = "https://raw.githubusercontent.com/TheCathedralFCCLA/tech-schedule/main/Youtube%20Descriptions/chapters.txt";

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, "public")));

let currentService = null;
let currentVideo = null;
let overrideVideoId = null;

// State maintained locally
let past = [];
let upcoming = [];
let isStateInitialized = false;

// Mock mode
let isMockMode = false;
let mockTitle = "";
let mockActualStartTime = null;

let oscPort = 8000;
let udpPortInstance = null;

function loadConfig() {
    try {
        if (fs.existsSync(CONFIG_FILE)) {
            const data = fs.readFileSync(CONFIG_FILE, "utf-8");
            const config = JSON.parse(data);
            if (config.oscPort && !isNaN(config.oscPort)) {
                oscPort = parseInt(config.oscPort);
            }
        }
    } catch (e) {
        console.error("Error reading config:", e);
    }
}

function saveConfig() {
    try {
        const config = { oscPort: oscPort };
        fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), "utf-8");
    } catch (e) {
        console.error("Error writing config:", e);
    }
}

function getYouTubeService() {
    if (isMockMode) return null;

    if (currentService) return currentService;

    const credsJson = process.env.YOUTUBE_CREDENTIALS_JSON;
    if (!credsJson) {
        console.error("Error: YOUTUBE_CREDENTIALS_JSON environment variable not found. Use 'Load Sample List' in UI to test without credentials.");
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
    if (isMockMode) return null;
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

async function fetchChaptersFromGithub() {
    try {
        const response = await fetch(CHAPTERS_URL);
        if (!response.ok) {
            console.error(`Failed to fetch chapters: ${response.statusText}`);
            return [];
        }
        const text = await response.text();
        return text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    } catch (e) {
        console.error(`Error fetching chapters from GitHub: ${e}`);
        return [];
    }
}

async function fetchAndBroadcastState() {
    if (isMockMode) {
        io.emit('stateUpdate', {
            title: mockTitle,
            actualStartTime: mockActualStartTime,
            past: past,
            upcoming: upcoming,
            isMockMode: true,
            oscPort: oscPort
        });
        return;
    }

    const service = getYouTubeService();
    if (!service) {
        io.emit('stateUpdate', { error: "Missing YouTube Credentials. Use 'Load Sample List' to test without an API key.", oscPort: oscPort });
        return;
    }

    currentVideo = await getLiveStream(service);
    if (!currentVideo) {
        io.emit('stateUpdate', { error: "No active or upcoming live stream found.", oscPort: oscPort });
        return;
    }

    const actualStartTime = currentVideo.liveStreamingDetails?.actualStartTime || null;
    const snippet = currentVideo.snippet || {};
    const title = snippet.title || 'Untitled Stream';

    if (!isStateInitialized) {
        const chapters = await fetchChaptersFromGithub();
        if (chapters.length > 0) {
            upcoming = chapters;
            past = [];
            isStateInitialized = true;
        }
    }

    io.emit('stateUpdate', {
        title: title,
        actualStartTime: actualStartTime,
        past: past,
        upcoming: upcoming,
        isMockMode: false,
        oscPort: oscPort
    });
}

async function pushTimestampsToYouTube() {
    if (isMockMode) {
        console.log("Mock Mode: Would push timestamps to YouTube here.");
        return;
    }

    console.log("Pushing final timestamps to YouTube...");
    const service = getYouTubeService();
    if (!service) return;

    const video = await getLiveStream(service);
    if (!video) {
        console.log("Could not find video to push timestamps to.");
        return;
    }

    const snippet = video.snippet || {};
    let currentDesc = snippet.description || '';

    if (past.length === 0) {
        console.log("No timestamps to push.");
        return;
    }

    const timestampsText = past.join('\n');

    if (!currentDesc.includes(timestampsText.split('\n')[0])) {
        snippet.description = currentDesc + '\n\n' + timestampsText;

        try {
            await service.videos.update({
                part: 'snippet',
                requestBody: {
                    id: video.id,
                    snippet: snippet
                }
            });
            console.log("Successfully pushed timestamps to YouTube description.");
        } catch (e) {
            console.error(`Failed to update description: ${e}`);
        }
    } else {
        console.log("Timestamps appear to already be in the description.");
    }
}

let lastPollStatus = null;
async function pollStreamStatus() {
    if (isMockMode) return;

    const service = getYouTubeService();
    if (!service) return;

    const video = await getLiveStream(service);
    if (!video) return;

    const currentStatus = video.snippet?.liveBroadcastContent;

    if (lastPollStatus === 'live' && currentStatus === 'none') {
        console.log("Stream has ended! Triggering automatic push of timestamps.");
        await pushTimestampsToYouTube();
    }

    lastPollStatus = currentStatus;
}
setInterval(pollStreamStatus, 60000);

async function handleOscMessage(oscMsg) {
    console.log(`Received OSC message at address: ${oscMsg.address}`);

    let actualStartTimeStr = null;

    if (isMockMode) {
        actualStartTimeStr = mockActualStartTime;
    } else {
        const service = getYouTubeService();
        if (!service) {
            console.log("Could not get YouTube service. Are credentials set?");
            return;
        }

        const video = await getLiveStream(service);
        if (!video) {
            console.log("No active live stream found.");
            return;
        }

        actualStartTimeStr = video.liveStreamingDetails?.actualStartTime;
    }

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

    if (upcoming.length > 0) {
        const nextItem = upcoming.shift();
        const timestampedItem = `${elapsedStr} ${nextItem}`;
        past.push(timestampedItem);
        console.log(`Added timestamp: ${timestampedItem}`);

        await fetchAndBroadcastState();
    } else {
        console.log("No upcoming sections left to timestamp.");
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
            return url.pathname.slice(1);
        }
    } catch (e) {
        return null;
    }
    return urlStr;
}

function startOscServer(port) {
    if (udpPortInstance) {
        console.log("Closing existing OSC server...");
        try {
            udpPortInstance.close();
        } catch (e) {}
    }

    udpPortInstance = new osc.UDPPort({
        localAddress: OSC_IP,
        localPort: port,
        metadata: true
    });

    udpPortInstance.on("message", (oscMsg) => {
        handleOscMessage(oscMsg).catch(console.error);
    });

    udpPortInstance.on("error", (err) => {
        console.error("OSC Server Error:", err);
    });

    udpPortInstance.on("ready", () => {
        console.log(`Started OSC server on ${OSC_IP}:${port}...`);
    });

    udpPortInstance.open();
}

io.on("connection", (socket) => {
    console.log("Client connected to UI");
    fetchAndBroadcastState();

    socket.on("requestState", () => {
        fetchAndBroadcastState();
    });

    socket.on("setOverrideLink", (link) => {
        isMockMode = false;
        const vId = extractVideoId(link);
        if (vId) {
            console.log(`Setting override video ID to: ${vId}`);
            overrideVideoId = vId;
            fetchAndBroadcastState();
        }
    });

    socket.on("clearOverrideLink", () => {
        console.log("Clearing override video ID.");
        isMockMode = false;
        overrideVideoId = null;
        fetchAndBroadcastState();
    });

    socket.on("pushTimestamps", () => {
        console.log("Manual push triggered from UI.");
        pushTimestampsToYouTube();
    });

    socket.on("resetState", async () => {
        console.log("Resetting local state from GitHub chapters.");
        isMockMode = false;
        isStateInitialized = false;
        await fetchAndBroadcastState();
    });

    socket.on("loadSampleData", () => {
        console.log("Loading Sample Data mode.");
        isMockMode = true;

        mockActualStartTime = new Date(Date.now() - 15 * 60000).toISOString();
        mockTitle = "[SAMPLE MODE] Sunday Worship Service";

        past = [];
        upcoming = [
            "Announcements",
            "Organ Prelude-Concert",
            "Welcome",
            "Prayers of Awareness",
            "Sermon",
            "Communion"
        ];

        fetchAndBroadcastState();
    });

    socket.on("setOscPort", (portStr) => {
        const port = parseInt(portStr);
        if (!isNaN(port) && port > 0 && port < 65536) {
            console.log(`Setting OSC port to ${port}`);
            oscPort = port;
            saveConfig();
            startOscServer(oscPort);
            fetchAndBroadcastState();
        }
    });
});

function main() {
    loadConfig();
    startOscServer(oscPort);

    server.listen(WEB_PORT, () => {
        console.log(`Web UI listening on http://localhost:${WEB_PORT}`);
    });
}

if (require.main === module) {
    main();
}
