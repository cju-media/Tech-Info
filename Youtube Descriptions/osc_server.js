const osc = require("osc");
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const path = require("path");
const fetch = require("node-fetch");
const fs = require("fs");
const { google } = require("googleapis");
const child_process = require("child_process");

const OSC_IP = "0.0.0.0";
const WEB_PORT = 3671;
const CONFIG_FILE = path.join(__dirname, "osc_config.json");
const PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs";

function getChaptersUrl() {
    let branch = "main";
    try {
        branch = child_process.execSync("git rev-parse --abbrev-ref HEAD", { encoding: "utf8" }).trim();
    } catch (e) {
        console.warn("Could not determine git branch, defaulting to main.");
    }
    return `https://raw.githubusercontent.com/TheCathedralFCCLA/tech-schedule/${branch}/Youtube%20Descriptions/chapters.txt`;
}

const app = express();
const server = http.createServer(app);
const io = new Server(server);

app.use(express.static(path.join(__dirname, "public")));

app.get('/api/readme', (req, res) => {
    const readmePath = path.join(__dirname, "README.md");
    if (fs.existsSync(readmePath)) {
        res.sendFile(readmePath);
    } else {
        res.status(404).send("README.md not found.");
    }
});

let overrideVideoId = null;
let currentService = null;
let currentVideo = null;

// State maintained locally
let past = [];
let upcoming = [];
let isStateInitialized = false;

// We track start time, allowing the YT API to seed it if possible
let activeStartTime = null;

// Track stream lifecycle
let currentStreamId = null;
let hasGoneLive = false;
let hasPushedThisStream = false;

let isMockMode = false;
let mockTitle = "Worship Service (Pending Start)";

let oscPort = 8000;
let youtubeApiKey = "";
let githubPat = "";
let udpPortInstance = null;
let lastResetWeek = null;

function getPacificWeekKey() {
    // Get current time in America/Los_Angeles
    const laTime = new Date().toLocaleString("en-US", { timeZone: "America/Los_Angeles" });
    const d = new Date(laTime);

    // We want to reset on Sunday at 12:00am.
    // We can define the "week key" as the Sunday's date.
    // Sunday is 0 in getDay().
    const diffToSunday = d.getDay(); // 0 on Sunday, 1 on Monday...
    const lastSunday = new Date(d);
    lastSunday.setDate(d.getDate() - diffToSunday);

    // Create a string like "2023-10-22" representing the start of the week (Sunday)
    return `${lastSunday.getFullYear()}-${(lastSunday.getMonth() + 1).toString().padStart(2, '0')}-${lastSunday.getDate().toString().padStart(2, '0')}`;
}

function checkAndAutoReset() {
    const currentWeekKey = getPacificWeekKey();
    if (lastResetWeek === null) {
        // First run, just initialize it
        lastResetWeek = currentWeekKey;
    } else if (lastResetWeek !== currentWeekKey) {
        console.log(`Auto-resetting state for new week: ${currentWeekKey}`);

        isMockMode = false;
        overrideVideoId = null;
        isStateInitialized = false;
        activeStartTime = null;
        currentStreamId = null;
        hasGoneLive = false;
        hasPushedThisStream = false;
        lastResetWeek = currentWeekKey;

        // This will fetch fresh chapters and default stream
        fetchAndBroadcastState();
    }
}

function loadConfig() {
    try {
        if (fs.existsSync(CONFIG_FILE)) {
            const data = fs.readFileSync(CONFIG_FILE, "utf-8");
            const config = JSON.parse(data);
            if (config.oscPort && !isNaN(config.oscPort)) {
                oscPort = parseInt(config.oscPort);
            }
            if (config.youtubeApiKey) {
                youtubeApiKey = config.youtubeApiKey;
            }
            if (config.githubPat) {
                githubPat = config.githubPat;
            }
        }
    } catch (e) {
        console.error("Error reading config:", e);
    }
}

function saveConfig() {
    try {
        const config = {
            oscPort: oscPort,
            youtubeApiKey: youtubeApiKey,
            githubPat: githubPat
        };
        fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), "utf-8");
    } catch (e) {
        console.error("Error writing config:", e);
    }
}

function getYouTubeService() {
    if (isMockMode) return null;

    // Clear out the cached service if we change the key
    const apiKey = youtubeApiKey || process.env.YOUTUBE_API_KEY;
    if (!apiKey) {
        console.error("Error: YouTube API Key not configured. UI title/timer sync will not work.");
        return null;
    }

    try {
        currentService = google.youtube({
            version: 'v3',
            auth: apiKey
        });
        return currentService;
    } catch (e) {
        console.error(`Error authenticating with YouTube API Key: ${e}`);
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

        let foundLiveOrUpcoming = null;
        let lastLiveEnded = null;

        for (const video of (videoResponse.data.items || [])) {
            const snippet = video.snippet || {};

            // Check for actively live stream first
            if (snippet.liveBroadcastContent === 'live' && video.liveStreamingDetails) {
                if (video.liveStreamingDetails.actualStartTime) {
                    return video;
                }
            } else if (snippet.liveBroadcastContent === 'upcoming' && !foundLiveOrUpcoming) {
                foundLiveOrUpcoming = video;
            } else if (snippet.liveBroadcastContent === 'none' && video.liveStreamingDetails && video.liveStreamingDetails.actualEndTime) {
                // Keep track of the most recently ended stream in case we just transitioned from live -> none
                if (!lastLiveEnded || new Date(video.liveStreamingDetails.actualEndTime) > new Date(lastLiveEnded.liveStreamingDetails.actualEndTime)) {
                    lastLiveEnded = video;
                }
            }
        }

        // If we didn't find live or upcoming, return the recently ended stream so we can detect the transition
        return foundLiveOrUpcoming || lastLiveEnded;
    } catch (e) {
        console.error(`An error occurred getting streams from playlist: ${e}`);
    }

    return null;
}

function processChapters(rawChapters) {
    let initialPast = [];
    let initialUpcoming = [];

    // Pass 1: find where "Announcements" is (typically index 0 or 1, maybe after prelude)
    for (const c of rawChapters) {
        initialUpcoming.push(c);
    }

    // We want 00:00 Announcements to be the VERY FIRST item in initialPast
    let announcementsIndex = initialUpcoming.findIndex(c => c.toLowerCase().includes("announcements"));
    if (announcementsIndex !== -1) {
        const item = initialUpcoming.splice(announcementsIndex, 1)[0];
        initialPast.push(`00:00 ${item}`);
    }

    // Auto-shift the prelude out of upcoming so the arrow never rests on it
    let preludeIndex = initialUpcoming.findIndex(c => c.startsWith("Organ Prelude-Concert,"));
    if (preludeIndex !== -1) {
        const item = initialUpcoming.splice(preludeIndex, 1)[0];
        initialPast.push(item);
    }

    return { past: initialPast, upcoming: initialUpcoming };
}

async function fetchChaptersFromGithub() {
    try {
        let text = "";
        const pat = githubPat || process.env.GITHUB_PAT;

        let headers = {};
        if (pat) {
            headers["Authorization"] = `Bearer ${pat}`;
        }

        const url = getChaptersUrl();
        console.log(`Fetching chapters from: ${url}`);
        const response = await fetch(url, { headers: headers });
        if (!response.ok) {
            console.error(`Failed to fetch chapters from GitHub: ${response.statusText} (Status: ${response.status})`);
            // Fallback to local chapters.txt for local testing on unmerged branches
            const localPath = path.join(__dirname, "chapters.txt");
            if (fs.existsSync(localPath)) {
                console.log("Falling back to local chapters.txt...");
                text = fs.readFileSync(localPath, "utf-8");
            } else {
                return [];
            }
        } else {
            text = await response.text();
        }
        return text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    } catch (e) {
        console.error(`Error fetching chapters from GitHub: ${e}`);
        return [];
    }
}

async function fetchAndBroadcastState() {
    checkAndAutoReset();

    if (!isStateInitialized) {
        const rawChapters = await fetchChaptersFromGithub();
        if (rawChapters.length > 0) {
            const processed = processChapters(rawChapters);
            upcoming = processed.upcoming;
            past = processed.past;
            isStateInitialized = true;
        }
    }

    let broadcastTitle = "Worship Service (Pending Start)";
    let apiError = null;

    if (isMockMode) {
        broadcastTitle = mockTitle;
    } else {
        const service = getYouTubeService();
        if (service) {
            currentVideo = await getLiveStream(service);
            if (currentVideo) {
                broadcastTitle = currentVideo.snippet?.title || "Untitled Stream";

                // Keep track of the specific stream so we can reset cleanly when testing different ones
                if (currentStreamId !== currentVideo.id) {
                    console.log(`Tracking new stream: ${currentVideo.id}`);
                    currentStreamId = currentVideo.id;
                    hasGoneLive = false;
                    hasPushedThisStream = false;
                    // Note: we do not wipe `isStateInitialized` here, because we want to preserve the pre-loaded chapters
                }

                const ytActualStart = currentVideo.liveStreamingDetails?.actualStartTime;
                const ytActualEnd = currentVideo.liveStreamingDetails?.actualEndTime;
                const isStreamEnded = currentVideo.snippet?.liveBroadcastContent === 'none' && ytActualEnd;

                // Sync live state
                if (ytActualStart && !ytActualEnd && currentVideo.snippet?.liveBroadcastContent === 'live') {
                    if (!hasGoneLive) {
                        console.log(`Stream has just gone live! Auto-resetting timings to the top.`);
                        hasGoneLive = true;

                        // Force a clean reset of chapters so it's fresh for the live stream
                        isStateInitialized = false;
                        const rawChapters = await fetchChaptersFromGithub();
                        if (rawChapters.length > 0) {
                            const processed = processChapters(rawChapters);
                            upcoming = processed.upcoming;
                            past = processed.past;
                            isStateInitialized = true;
                        }
                    }
                }

                if (isStreamEnded) {
                    broadcastTitle = "[Ended] " + broadcastTitle;

                    if (hasGoneLive && !hasPushedThisStream) {
                        console.log(`Detected stream end. Stopping timer and initiating auto-push.`);
                        activeStartTime = null; // Pause timer
                        hasPushedThisStream = true;

                        io.emit('stateUpdate', {
                            title: broadcastTitle,
                            actualStartTime: null,
                            past: past,
                            upcoming: upcoming,
                            isMockMode: isMockMode,
                            oscPort: oscPort,
                            hasYoutubeApiKey: !!youtubeApiKey,
                            hasGithubPat: !!githubPat,
                            error: apiError
                        });

                        await pushTimingsToGithub();
                        return; // stateUpdate already emitted above before push block
                    }
                } else if (ytActualStart && !activeStartTime) {
                    // If YouTube says the stream has started, seed the timer!
                    console.log(`Seeded start time from YouTube API: ${ytActualStart}`);
                    activeStartTime = ytActualStart;
                }
            } else {
                broadcastTitle = "No active or upcoming live stream found.";
            }
        } else {
            apiError = "Missing YouTube API Key. Configure it in the menu.";
        }
    }

    io.emit('stateUpdate', {
        title: broadcastTitle,
        actualStartTime: activeStartTime,
        past: past,
        upcoming: upcoming,
        isMockMode: isMockMode,
        oscPort: oscPort,
        hasYoutubeApiKey: !!youtubeApiKey,
        hasGithubPat: !!githubPat,
        error: apiError
    });
}

async function pushTimingsToGithub() {
    if (isMockMode) {
        console.log("Mock Mode: Would push timings.txt to GitHub here.");
        io.emit('pushStatus', { status: 'info', message: 'Mock mode: Push skipped.' });
        return;
    }

    const laTime = new Date().toLocaleString("en-US", { timeZone: "America/Los_Angeles" });
    const currentDay = new Date(laTime).getDay();

    // 0 is Sunday
    if (currentDay !== 0) {
        console.log("Not Sunday. Skipping push to GitHub.");
        io.emit('pushStatus', { status: 'info', message: 'Test Mode: Push skipped because today is not Sunday.' });
        return;
    }

    console.log("Pushing final timings to GitHub...");
    io.emit('pushStatus', { status: 'info', message: 'Stream ended. Pushing timings to GitHub...' });

    const pat = githubPat || process.env.GITHUB_PAT;
    if (!pat) {
        console.error("Error: GitHub PAT not configured. Cannot push to GitHub.");
        io.emit('pushStatus', { status: 'error', message: 'Failed: GitHub PAT not configured.' });
        return;
    }

    if (past.length === 0) {
        console.log("No timings to push.");
        io.emit('pushStatus', { status: 'info', message: 'No timings to push.' });
        return;
    }

    const timingsText = past.join('\n');
    const b64Content = Buffer.from(timingsText).toString('base64');

    const apiUrl = "https://api.github.com/repos/TheCathedralFCCLA/tech-schedule/contents/Youtube%20Descriptions/timings.txt";

    try {
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
            io.emit('pushStatus', { status: 'error', message: `Push failed: ${putRes.status}` });
        } else {
            console.log("Successfully pushed timings.txt to GitHub repository!");
            io.emit('pushStatus', { status: 'info', message: 'Push successful! Waiting for GitHub Action to start...' });

            // Poll for GitHub Action status
            setTimeout(() => checkGitHubActionStatus(pat), 10000); // Start checking after 10 seconds
        }

    } catch (e) {
        console.error(`Failed to push to GitHub: ${e}`);
        io.emit('pushStatus', { status: 'error', message: `Push failed: ${e.message}` });
    }
}

async function checkGitHubActionStatus(pat, attempts = 0) {
    if (attempts > 30) { // Timeout after ~5 minutes
        io.emit('pushStatus', { status: 'error', message: 'Timed out waiting for GitHub Action to finish.' });
        return;
    }

    try {
        const url = "https://api.github.com/repos/TheCathedralFCCLA/tech-schedule/actions/runs?event=push&per_page=5";
        const res = await fetch(url, {
            headers: {
                "Authorization": `Bearer ${pat}`,
                "Accept": "application/vnd.github.v3+json"
            }
        });

        if (res.ok) {
            const data = await res.json();
            if (data.workflow_runs && data.workflow_runs.length > 0) {
                // Find the most recent run that matches our push
                // Specifically looking for the "Update YouTube Stream" workflow
                const run = data.workflow_runs.find(r => r.name.includes("Update YouTube Stream"));

                if (run) {
                    if (run.status === 'in_progress' || run.status === 'queued') {
                        io.emit('pushStatus', { status: 'info', message: 'GitHub Action is updating YouTube description...' });
                        setTimeout(() => checkGitHubActionStatus(pat, attempts + 1), 10000); // Check again in 10s
                        return;
                    } else if (run.status === 'completed') {
                        if (run.conclusion === 'success') {
                            io.emit('pushStatus', { status: 'success', message: 'Success! YouTube description has been updated.' });
                        } else {
                            io.emit('pushStatus', { status: 'error', message: `GitHub Action failed with conclusion: ${run.conclusion}` });
                        }
                        return;
                    }
                }
            }
        }
    } catch (e) {
        console.error("Error checking action status:", e);
    }

    // If not found or error, retry
    setTimeout(() => checkGitHubActionStatus(pat, attempts + 1), 10000);
}

// Ensure title updates periodically
setInterval(() => {
    if (!isMockMode) fetchAndBroadcastState();
}, 60000);

async function handleNextTiming() {
    if (!isMockMode && !hasGoneLive) {
        console.log("Stream is not live yet. Cannot iterate timings.");
        return;
    }

    if (!activeStartTime) {
        // Fallback safety: If we somehow don't have a start time, force it locally.
        activeStartTime = new Date().toISOString();
        console.log(`Stream timer locally started at: ${activeStartTime}`);
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

        // Peek ahead to auto-skip prelude if it somehow got back into upcoming
        while (upcoming.length > 0 && upcoming[0].startsWith("Organ Prelude-Concert,")) {
            const skipItem = upcoming.shift();
            past.push(skipItem);
            console.log(`Auto-skipped prelude: ${skipItem}`);
        }

        await fetchAndBroadcastState();
    } else {
        console.log("No upcoming sections left to timestamp.");
    }
}

async function handlePrevTiming() {
    if (!isMockMode && !hasGoneLive) {
        console.log("Stream is not live yet. Cannot iterate timings.");
        return;
    }

    let revertedTimestamp = false;

    while (past.length > 0 && !revertedTimestamp) {
        const lastItem = past.pop();
        console.log(`Reverting item: ${lastItem}`);

        const match = lastItem.match(/^(\d{1,2}:)?\d{1,2}:\d{2}\s+/);
        if (match) {
            const timeStr = match[0];
            const cleanTitle = lastItem.substring(timeStr.length).trim();
            upcoming.unshift(cleanTitle);

            // We successfully found and reverted a real timestamp, so stop looping
            revertedTimestamp = true;
        } else {
            // It's an untimestamped item (like Organ Prelude). Push it back to upcoming,
            // but keep looping because we haven't actually reverted a *timing* yet.
            upcoming.unshift(lastItem);
        }
    }

    if (revertedTimestamp || past.length > 0) {
        await fetchAndBroadcastState();
    } else if (activeStartTime && past.length === 0) {
        console.log("Reverting timer start.");
        activeStartTime = null;
        await fetchAndBroadcastState();
    } else {
        console.log("Nothing to revert.");
    }
}

async function handleOscMessage(oscMsg) {
    if (oscMsg.address === "/timings/forward") {
        console.log(`Received OSC forward command.`);
        io.emit('oscIndicator');
        await handleNextTiming();
    } else if (oscMsg.address === "/timings/back") {
        console.log(`Received OSC back command.`);
        io.emit('oscIndicator');
        await handlePrevTiming();
    } else {
        console.log(`Ignored unrelated OSC message at address: ${oscMsg.address}`);
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
            // Clear current service so we re-fetch with API
            currentService = null;
            currentStreamId = null;
            hasGoneLive = false;
            hasPushedThisStream = false;
            fetchAndBroadcastState();
        }
    });

    socket.on("clearOverrideLink", () => {
        console.log("Clearing override video ID.");
        isMockMode = false;
        overrideVideoId = null;
        currentService = null;
        currentStreamId = null;
        hasGoneLive = false;
        hasPushedThisStream = false;
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
        currentStreamId = null;
        hasGoneLive = false;
        hasPushedThisStream = false;
        await fetchAndBroadcastState();
    });

    socket.on("loadSampleData", () => {
        console.log("Loading Sample Data mode.");
        isMockMode = true;

        activeStartTime = new Date(Date.now() - 15 * 60000).toISOString();
        mockTitle = "[SAMPLE MODE] Sunday Worship Service";

        const sampleRaw = [
            "Announcements",
            "Organ Prelude-Concert,",
            "Welcome",
            "Prayers of Awareness",
            "Sermon",
            "Communion"
        ];

        const processed = processChapters(sampleRaw);
        upcoming = processed.upcoming;
        past = processed.past;

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

    socket.on("setYoutubeApiKey", (key) => {
        console.log(`Setting YouTube API Key`);
        youtubeApiKey = key;
        currentService = null; // force re-init
        saveConfig();
        fetchAndBroadcastState();
    });

    socket.on("setGithubPat", (key) => {
        console.log(`Setting GitHub PAT`);
        githubPat = key;
        saveConfig();
        fetchAndBroadcastState();
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

    // Seed initial state
    fetchAndBroadcastState();

    // Check state (and auto-reset if new week) every minute
    setInterval(fetchAndBroadcastState, 60000);

    server.listen(WEB_PORT, () => {
        console.log(`Web UI listening on http://localhost:${WEB_PORT}`);
    });
}

if (require.main === module) {
    main();
}
