const osc = require("osc");
const { google } = require("googleapis");
const fs = require("fs");

const PLAYLIST_ID = "PLGtiSp5WvUc_I0M_vvfSdGY9dJ43ZofXs";
const OSC_IP = "0.0.0.0";
const OSC_PORT = 8000;

function getYouTubeService() {
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

        return google.youtube({
            version: 'v3',
            auth: oauth2Client
        });
    } catch (e) {
        console.error(`Error authenticating with YouTube: ${e}`);
        return null;
    }
}

async function getLiveStream(service) {
    try {
        // Fallback to playlist search
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

        for (const video of (videoResponse.data.items || [])) {
            const snippet = video.snippet || {};
            if (snippet.liveBroadcastContent === 'live' && video.liveStreamingDetails) {
                if (video.liveStreamingDetails.actualStartTime) {
                    return video;
                }
            }
        }
    } catch (e) {
        console.error(`An error occurred getting streams from playlist: ${e}`);
    }

    return null;
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
        // Find the next non-empty line after the last timestamp
        for (let i = lastTimestampIdx + 1; i < lines.length; i++) {
            if (lines[i].trim()) {
                lines[i] = `${elapsedStr} ${lines[i].trim()}`;
                return { newDesc: lines.join('\n'), changed: true };
            }
        }
        return { newDesc: description, changed: false };
    }

    // If no timestamps exist, we need to find the start of the sections block.
    // The boilerplate ends after the social media links. Let's find the last line containing a link.
    let lastLinkIdx = -1;
    for (let i = 0; i < lines.length; i++) {
        if (lines[i].includes('http://') || lines[i].includes('https://')) {
            lastLinkIdx = i;
        }
    }

    // The first non-empty line after the last link is the first section
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

    const actualStartTimeStr = video.liveStreamingDetails.actualStartTime;
    if (!actualStartTimeStr) {
        console.log("No actual start time found on the stream.");
        return;
    }

    // Calculate elapsed time
    const startTime = new Date(actualStartTimeStr).getTime();
    const now = Date.now();
    let totalSeconds = Math.floor((now - startTime) / 1000);

    if (totalSeconds < 0) {
        totalSeconds = 0;
    }

    const hours = Math.floor(totalSeconds / 3600);
    const remainder = totalSeconds % 3600;
    const minutes = Math.floor(remainder / 60);
    const seconds = remainder % 60;

    const secondsStr = seconds.toString().padStart(2, '0');
    let elapsedStr;
    if (hours > 0) {
        const minutesStr = minutes.toString().padStart(2, '0');
        elapsedStr = `${hours}:${minutesStr}:${secondsStr}`;
    } else {
        elapsedStr = `${minutes}:${secondsStr}`;
    }

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
        } catch (e) {
            console.error(`Failed to update description: ${e}`);
        }
    } else {
        console.log("Could not find a suitable line to add a timestamp to, or description is fully timestamped.");
    }
}

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
}

if (require.main === module) {
    main();
}
