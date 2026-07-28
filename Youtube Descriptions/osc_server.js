const osc = require("osc");
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const path = require("path");
const fetch = require("node-fetch");
const fs = require("fs");

const OSC_IP = "0.0.0.0";
const WEB_PORT = 3671;
const CONFIG_FILE = path.join(__dirname, "osc_config.json");

const CHAPTERS_URL = "https://raw.githubusercontent.com/TheCathedralFCCLA/tech-schedule/main/Youtube%20Descriptions/chapters.txt";

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, "public")));

let overrideVideoId = null;

// State maintained locally
let past = [];
let upcoming = [];
let isStateInitialized = false;

// We no longer query the YouTube API directly, so we just track a start time
let activeStartTime = null;

let isMockMode = false;
let mockTitle = "Worship Service (Pending Start)";

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
    if (!isStateInitialized) {
        const chapters = await fetchChaptersFromGithub();
        if (chapters.length > 0) {
            upcoming = chapters;
            past = [];
            isStateInitialized = true;
        }
    }

    io.emit('stateUpdate', {
        title: mockTitle, // Defaulting to generic title since YouTube API is removed
        actualStartTime: activeStartTime,
        past: past,
        upcoming: upcoming,
        isMockMode: isMockMode,
        oscPort: oscPort
    });
}

async function pushTimingsToGithub() {
    if (isMockMode) {
        console.log("Mock Mode: Would push timings.txt to GitHub here.");
        return;
    }

    console.log("Pushing final timings to GitHub...");

    const pat = process.env.GITHUB_PAT;
    if (!pat) {
        console.error("Error: GITHUB_PAT environment variable not found. Cannot push to GitHub.");
        return;
    }

    if (past.length === 0) {
        console.log("No timings to push.");
        return;
    }

    const timingsText = past.join('\n');
    const b64Content = Buffer.from(timingsText).toString('base64');

    const apiUrl = "https://api.github.com/repos/TheCathedralFCCLA/tech-schedule/contents/Youtube%20Descriptions/timings.txt";

    try {
        // Try to get the existing file to grab its SHA
        let sha = null;
        const getRes = await fetch(apiUrl, {
            headers: {
                "Authorization": `Bearer ${pat}`,
                "Accept": "application/vnd.github.v3+json"
            }
        });

        if (getRes.ok) {
            const fileData = await getRes.json();
            sha = fileData.sha;
        }

        const bodyPayload = {
            message: "auto: push completed stream timings",
            content: b64Content,
            branch: "main"
        };

        if (sha) {
            bodyPayload.sha = sha;
        }

        const putRes = await fetch(apiUrl, {
            method: "PUT",
            headers: {
                "Authorization": `Bearer ${pat}`,
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json"
            },
            body: JSON.stringify(bodyPayload)
        });

        if (!putRes.ok) {
            const errText = await putRes.text();
            console.error(`Failed to push timings to GitHub: ${putRes.status} - ${errText}`);
        } else {
            console.log("Successfully pushed timings.txt to GitHub repository!");
        }

    } catch (e) {
        console.error(`Failed to push to GitHub: ${e}`);
    }
}

// Without the YouTube API we don't automatically know when the stream ends.
// We expose the functionality to the web UI push button.

async function handleNextTiming() {
    // If we haven't set a start time yet, the first trigger SETS the start time.
    if (!activeStartTime) {
        activeStartTime = new Date().toISOString();
        console.log(`Stream timer started at: ${activeStartTime}`);
        await fetchAndBroadcastState();
        return; // Don't burn the first chapter, just start the clock.
    }

    const startTime = new Date(activeStartTime).getTime();
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

async function handlePrevTiming() {
    if (past.length > 0) {
        const lastItem = past.pop();
        console.log(`Reverting timestamp: ${lastItem}`);

        // Strip the timestamp (e.g. "1:05:22 Sermon" -> "Sermon")
        const match = lastItem.match(/^(\d{1,2}:)?\d{1,2}:\d{2}\s+/);
        if (match) {
            const timeStr = match[0];
            const cleanTitle = lastItem.substring(timeStr.length).trim();
            upcoming.unshift(cleanTitle);
        } else {
            upcoming.unshift(lastItem);
        }

        await fetchAndBroadcastState();
    } else if (activeStartTime && past.length === 0) {
        // If we revert to the beginning, reset the timer
        console.log("Reverting timer start.");
        activeStartTime = null;
        await fetchAndBroadcastState();
    } else {
        console.log("Nothing to revert.");
    }
}

async function handleOscMessage(oscMsg) {
    console.log(`Received OSC message at address: ${oscMsg.address}`);
    await handleNextTiming();
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
        console.log(`(Ignored) Stream override via link: ${link}`);
        fetchAndBroadcastState();
    });

    socket.on("clearOverrideLink", () => {
        isMockMode = false;
        fetchAndBroadcastState();
    });

    socket.on("pushTimestamps", () => {
        console.log("Manual push triggered from UI.");
        pushTimingsToGithub();
    });

    socket.on("resetState", async () => {
        console.log("Resetting local state from GitHub chapters.");
        isMockMode = false;
        isStateInitialized = false;
        activeStartTime = null;
        await fetchAndBroadcastState();
    });

    socket.on("loadSampleData", () => {
        console.log("Loading Sample Data mode.");
        isMockMode = true;

        activeStartTime = new Date(Date.now() - 15 * 60000).toISOString();
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

    socket.on("nextTiming", () => {
        handleNextTiming();
    });

    socket.on("prevTiming", () => {
        handlePrevTiming();
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
