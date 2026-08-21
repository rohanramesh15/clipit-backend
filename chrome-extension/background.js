/**
 * ClipIt — background service worker
 * Tracks videos and pre-fetches the full vocab pipeline in the background.
 * Results cached in chrome.storage.local so the popup loads instantly.
 * Supports YouTube and Netflix.
 */

console.log('[ClipIt] Service worker starting...');
// Import subtitle fetcher
importScripts('subtitle-fetcher.js');
const API = 'https://project-deadbird-backend.fly.dev/api';
const CACHE_TTL_MS = 30 * 60 * 1000; // 30 minutes

const LANGUAGE_CONFIGS = {
  ko: {
    code: 'ko',
    name: 'Korean',
    subtitleKey: 'korean',
    statusPath: 'status',
    statusBodyKey: 'has_korean',
  },
  uk: {
    code: 'uk',
    name: 'Ukrainian',
    subtitleKey: 'ukrainian',
    statusPath: 'status/ukrainian',
    statusBodyKey: 'has_ukrainian',
  },
};

const SUPPORTED_LANGUAGES = ['ko', 'uk'];

function getLanguageConfig(lang = 'ko') {
  return LANGUAGE_CONFIGS[lang] || {
    code: lang,
    name: lang,
    subtitleKey: lang,
    statusPath: null,
    statusBodyKey: `has_${lang}`,
  };
}

function getSubtitleListForLanguage(subtitles, lang) {
  const config = getLanguageConfig(lang);
  return subtitles?.[config.subtitleKey] || [];
}

function buildSubtitleUploadFlags(lang, hasTargetLanguage) {
  const flags = {
    has_korean: false,
    has_ukrainian: false,
  };
  const key = getLanguageConfig(lang).statusBodyKey;
  if (key in flags) {
    flags[key] = hasTargetLanguage;
  }
  return flags;
}

async function getAuthToken() {
  const result = await chrome.storage.local.get('deadbird_token');
  return result.deadbird_token || null;
}

async function getPreferredLanguage() {
  const result = await chrome.storage.local.get('language');
  return SUPPORTED_LANGUAGES.includes(result.language) ? result.language : 'ko';
}

async function getActiveTrackingLanguage(requestedLang) {
  const storedLang = await getPreferredLanguage();
  return requestedLang || storedLang;
}

function authHeaders(token, extra = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

// Netflix subtitle state
let currentNetflixVideoId = null;
let netflixSubtitles = {}; // { videoId: { ko: [...], en: [...], uk: [...] } }

// Offscreen document state
let offscreenDocumentCreated = false;

// Track tabs where audio capture is activated (user clicked extension icon)
let audioActivatedTabs = new Set();

// Track tabs with active persistent loopback
let loopbackActiveTabs = new Set();

// Restore audioActivatedTabs from session storage (survives service worker restarts)
chrome.storage.session.get('audioActivatedTabs').then(result => {
  if (result.audioActivatedTabs) {
    audioActivatedTabs = new Set(result.audioActivatedTabs);
    console.log('[ClipIt] Restored audioActivatedTabs:', [...audioActivatedTabs]);
  }
}).catch(() => {});

// Helper to persist audioActivatedTabs
function persistAudioTabs() {
  chrome.storage.session.set({ audioActivatedTabs: [...audioActivatedTabs] }).catch(() => {});
}

// Clean up when tab is closed
chrome.tabs.onRemoved.addListener((tabId) => {
  audioActivatedTabs.delete(tabId);
  persistAudioTabs();
  // Stop loopback for this tab
  if (loopbackActiveTabs.has(tabId)) {
    loopbackActiveTabs.delete(tabId);
    chrome.runtime.sendMessage({ type: 'OFFSCREEN_STOP_LOOPBACK' }).catch(() => {});
  }
});

// ─── YouTube auto-tracking via tab URL change ────────────────────────────────
// The URL changes before YouTube reliably renders the title. Keep a bounded
// fallback so a broken/blocked content script cannot prevent tracking, but let
// the content script's rendered heading be the normal source of truth.
const recentlyTracked = new Map(); // videoId → timestamp
const pendingVideoTracks = new Map(); // videoId → fallback timeout ID
const TITLE_FALLBACK_DELAY_MS = 15000;
const CLIPIT_APP_URL_PATTERNS = [
  'https://clipit-sable.vercel.app/*',
  'https://theclipitapp.com/*',
  'https://www.theclipitapp.com/*',
  'http://localhost:5173/*',
  'http://localhost:5176/*',
];

async function updateTrackedVideoTitle(videoId, title) {
  const token = await getAuthToken();
  const res = await fetch(`${API}/videos/${videoId}/title`, {
    method: 'PUT',
    headers: authHeaders(token),
    body: JSON.stringify({ title }),
  });

  if (!res.ok) {
    throw new Error(`title update failed with ${res.status}`);
  }
}

async function notifyAppVideoTracked(videoId, lang) {
  const appTabs = await chrome.tabs.query({ url: CLIPIT_APP_URL_PATTERNS });
  await Promise.all(appTabs.map((tab) =>
    chrome.tabs.sendMessage(tab.id, { type: 'VIDEO_TRACKED', videoId, lang }).catch(() => {}),
  ));
}

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // Only care about URL changes on YouTube watch pages
  const url = changeInfo.url;
  if (!url || !url.includes('youtube.com/watch')) return;

  let videoId;
  try {
    videoId = new URL(url).searchParams.get('v');
  } catch { return; }
  if (!videoId) return;

  // Skip if we tracked this video recently or are already waiting for its
  // content script. This still allows a reload to be recorded after 5 seconds.
  const lastTime = recentlyTracked.get(videoId);
  if ((lastTime && Date.now() - lastTime < 5000) || pendingVideoTracks.has(videoId)) return;

  // Don't use tab.title — it is often the previous video's title during SPA
  // navigation. The content script will send the rendered heading instead.
  console.log(`[ClipIt] YouTube tab navigation: ${videoId} — waiting for rendered title`);
  const fallbackTimeout = setTimeout(async () => {
    if (!pendingVideoTracks.has(videoId)) return;
    pendingVideoTracks.delete(videoId);
    recentlyTracked.set(videoId, Date.now());
    console.warn(`[ClipIt] Timed out waiting for title: ${videoId}; tracking with fallback`);
    await trackAndPrefetch(videoId, 'Unknown', await getPreferredLanguage());
  }, TITLE_FALLBACK_DELAY_MS);
  pendingVideoTracks.set(videoId, fallbackTimeout);
});

// ─── Offscreen document management ───────────────────────────────────────────

async function ensureOffscreenDocument() {
  if (offscreenDocumentCreated) return;

  // Check if already exists
  const existingContexts = await chrome.runtime.getContexts({
    contextTypes: ['OFFSCREEN_DOCUMENT'],
    documentUrls: [chrome.runtime.getURL('offscreen.html')]
  });

  if (existingContexts.length > 0) {
    offscreenDocumentCreated = true;
    return;
  }

  // Create offscreen document
  await chrome.offscreen.createDocument({
    url: 'offscreen.html',
    reasons: ['USER_MEDIA'],
    justification: 'Recording audio from tab for language learning flashcards'
  });
  offscreenDocumentCreated = true;
  console.log('[ClipIt] Offscreen document created');
}

/**
 * Start persistent audio loopback for a tab.
 * This allows seamless audio capture without glitches.
 */
async function startPersistentLoopback(tabId) {
  try {
    await ensureOffscreenDocument();

    // Get stream ID for the tab
    const streamId = await new Promise((resolve, reject) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (streamId) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve(streamId);
        }
      });
    });

    console.log('[ClipIt] 🔊 Starting persistent loopback with stream ID:', streamId);

    // Tell offscreen document to start persistent loopback
    const response = await chrome.runtime.sendMessage({
      type: 'OFFSCREEN_START_LOOPBACK',
      streamId: streamId,
    });

    if (response.success) {
      loopbackActiveTabs.add(tabId);
      console.log('[ClipIt] 🔊 Persistent loopback active for tab:', tabId);
    } else {
      throw new Error(response.error || 'Failed to start loopback');
    }
  } catch (e) {
    console.error('[ClipIt] Failed to start persistent loopback:', e);
    throw e;
  }
}

async function captureAudio(tabId, duration = 3000) {
  console.log('[ClipIt] 🎤 Starting audio capture, tabId:', tabId, 'duration:', duration);

  // Check if audio capture is enabled for this tab
  if (!audioActivatedTabs.has(tabId)) {
    console.log('[ClipIt] 🎤 Audio not enabled for tab. User needs to click "Enable Audio" in popup.');
    throw new Error('Audio capture not enabled. Click the ClipIt extension icon and enable audio.');
  }

  try {
    // Ensure offscreen document exists
    await ensureOffscreenDocument();

    // Always try persistent loopback first (the offscreen document knows if it's active)
    // This handles service worker restarts where loopbackActiveTabs gets cleared
    console.log('[ClipIt] 🎤 Trying persistent loopback for seamless capture');
    try {
      const response = await chrome.runtime.sendMessage({
        type: 'OFFSCREEN_RECORD_CLIP',
        duration: duration
      });

      if (response.success) {
        console.log('[ClipIt] 🎤 Audio captured (seamless), size:', response.audioData?.size);
        // Re-add to loopbackActiveTabs in case service worker restarted
        loopbackActiveTabs.add(tabId);
        return response.audioData;
      }

      // If loopback not active, the error will be "Loopback not active"
      console.log('[ClipIt] 🎤 Persistent loopback not ready:', response.error);
    } catch (loopbackErr) {
      console.log('[ClipIt] 🎤 Loopback attempt failed:', loopbackErr.message);
    }

    // Fallback: Start fresh loopback (this will fail if stream already active)
    // Try to start persistent loopback first
    console.log('[ClipIt] 🎤 Attempting to start fresh loopback');
    try {
      await startPersistentLoopback(tabId);
      // Now try recording again
      const response = await chrome.runtime.sendMessage({
        type: 'OFFSCREEN_RECORD_CLIP',
        duration: duration
      });
      if (response.success) {
        console.log('[ClipIt] 🎤 Audio captured after loopback restart, size:', response.audioData?.size);
        return response.audioData;
      }
    } catch (restartErr) {
      console.log('[ClipIt] 🎤 Could not restart loopback:', restartErr.message);
    }

    // Final fallback: Legacy mode (will likely fail if stream is active)
    console.log('[ClipIt] 🎤 Final fallback: legacy recording');
    const streamId = await new Promise((resolve, reject) => {
      chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (streamId) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve(streamId);
        }
      });
    });

    const response = await chrome.runtime.sendMessage({
      type: 'OFFSCREEN_START_RECORDING',
      streamId: streamId,
      duration: duration
    });

    if (!response.success) {
      throw new Error(response.error || 'Recording failed');
    }

    console.log('[ClipIt] 🎤 Audio captured (legacy), size:', response.audioData?.size);
    return response.audioData;
  } catch (e) {
    console.error('[ClipIt] Audio capture failed:', e);
    throw e;
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log('[ClipIt] Message received:', msg.type);

  // Enable audio capture for a tab (from popup)
  if (msg.type === 'ENABLE_AUDIO_CAPTURE') {
    audioActivatedTabs.add(msg.tabId);
    persistAudioTabs();
    console.log('[ClipIt] Audio capture enabled for tab:', msg.tabId);
    chrome.action.setBadgeText({ text: '🎤', tabId: msg.tabId });
    chrome.action.setBadgeBackgroundColor({ color: '#22c55e', tabId: msg.tabId });

    // Start persistent loopback for seamless audio
    startPersistentLoopback(msg.tabId)
      .then(() => {
        console.log('[ClipIt] Persistent loopback started for tab:', msg.tabId);
        sendResponse({ success: true });
      })
      .catch(e => {
        console.error('[ClipIt] Failed to start loopback:', e);
        sendResponse({ success: true }); // Still mark as enabled, will use legacy mode
      });
    return true; // async response
  }

  // Check if audio is enabled for a tab
  if (msg.type === 'CHECK_AUDIO_ENABLED') {
    sendResponse({ enabled: audioActivatedTabs.has(msg.tabId) });
    return;
  }

  // Netflix interceptor injection
  if (msg.type === 'INJECT_NETFLIX_INTERCEPTOR' && sender.tab) {
    chrome.scripting.executeScript({
      target: { tabId: sender.tab.id },
      world: 'MAIN',
      files: ['inject.js']
    }).catch(e => console.error('[ClipIt] Failed to inject:', e));
    return;
  }

  // Screenshot capture for Netflix
  if (msg.type === 'CAPTURE_SCREENSHOT' && sender.tab) {
    console.log('[ClipIt] 📷 Capture request received, tabId:', sender.tab.id, 'windowId:', sender.tab.windowId);
    captureScreenshot(sender.tab.id, sender.tab.windowId)
      .then(dataUrl => {
        console.log('[ClipIt] 📷 Capture successful, size:', dataUrl?.length);
        sendResponse({ success: true, dataUrl });
      })
      .catch(e => {
        console.error('[ClipIt] Screenshot failed:', e);
        sendResponse({ success: false, error: e.message });
      });
    return true; // async response
  }

  // Audio capture for Netflix
  if (msg.type === 'CAPTURE_AUDIO' && sender.tab) {
    console.log('[ClipIt] 🎤 Audio capture request, tabId:', sender.tab.id, 'duration:', msg.duration);
    captureAudio(sender.tab.id, msg.duration || 3000)
      .then(audioData => {
        console.log('[ClipIt] 🎤 Audio capture successful');
        sendResponse({ success: true, audioData });
      })
      .catch(e => {
        console.error('[ClipIt] Audio capture failed:', e);
        sendResponse({ success: false, error: e.message });
      });
    return true; // async response
  }

  // Combined screenshot + audio capture for Netflix
  if (msg.type === 'CAPTURE_SCREENSHOT_AND_AUDIO' && sender.tab) {
    console.log('[ClipIt] 📷🎤 Combined capture request, tabId:', sender.tab.id);
    Promise.all([
      captureScreenshot(sender.tab.id, sender.tab.windowId),
      captureAudio(sender.tab.id, msg.duration || 3000)
    ])
      .then(([screenshotDataUrl, audioData]) => {
        console.log('[ClipIt] 📷🎤 Combined capture successful');
        sendResponse({ success: true, screenshotDataUrl, audioData });
      })
      .catch(e => {
        console.error('[ClipIt] Combined capture failed:', e);
        sendResponse({ success: false, error: e.message });
      });
    return true; // async response
  }

  // YouTube tracking (from content script — also checks dedup)
  if (msg.type === 'TRACK_VIDEO') {
    console.log(`[ClipIt] TRACK_VIDEO received: ${msg.videoId} — ${msg.title}`);
    const pendingFallback = pendingVideoTracks.get(msg.videoId);
    if (pendingFallback) {
      clearTimeout(pendingFallback);
      pendingVideoTracks.delete(msg.videoId);
    }

    const lastTime = recentlyTracked.get(msg.videoId);
    if (lastTime && Date.now() - lastTime < 5000) {
      // A fallback may have already recorded the video. Backfill its title
      // without creating another watch record.
      console.log(`[ClipIt] Video ${msg.videoId} already tracked recently, skipping`);
      if (msg.title && msg.title !== 'Unknown') {
        updateTrackedVideoTitle(msg.videoId, msg.title)
          .catch(error => console.warn(`[ClipIt] Could not backfill title for ${msg.videoId}:`, error));
      }
      getActiveTrackingLanguage(msg.lang)
        .then(lang => runVocabPipeline(msg.videoId, lang))
        .then(() => sendResponse({ success: true, is_new: false }));
    } else {
      recentlyTracked.set(msg.videoId, Date.now());
      getActiveTrackingLanguage(msg.lang)
        .then(lang => trackAndPrefetch(msg.videoId, msg.title, lang))
        .then(sendResponse);
    }
    return true;
  }
  if (msg.type === 'GET_VOCAB') {
    getCachedVocab(msg.videoId, msg.lang || 'ko').then(sendResponse);
    return true;
  }

  // Netflix tracking
  if (msg.type === 'TRACK_NETFLIX') {
    currentNetflixVideoId = msg.videoId;
    netflixSubtitles[msg.videoId] = netflixSubtitles[msg.videoId] || {};
    trackNetflix(msg.videoId, msg.title, msg.audioLang, msg.episodeInfo).then(sendResponse);
    return true;
  }
  // Update Netflix title (when we get a better title later)
  if (msg.type === 'UPDATE_NETFLIX_TITLE') {
    updateNetflixTitle(msg.videoId, msg.title);
    return;
  }
  // Netflix audio language detected/changed
  if (msg.type === 'NETFLIX_AUDIO_LANGUAGE') {
    updateNetflixAudioLanguage(msg.videoId, msg.audioLang);
    return;
  }
  if (msg.type === 'NETFLIX_SUBTITLES') {
    // Received subtitles from content script
    console.log(`[ClipIt] Received ${msg.subtitles.length} subtitles for Netflix ${msg.videoId}`);
    processNetflixSubtitles(msg.videoId, msg.subtitles, msg.language);
    sendResponse({ success: true });
    return true;
  }
  if (msg.type === 'GET_NETFLIX_SUBTITLES') {
    sendResponse(netflixSubtitles[msg.videoId] || {});
    return true;
  }

  // Get keyword timestamps for targeted screenshot capture
  if (msg.type === 'GET_KEYWORD_TIMESTAMPS') {
    const key = `keyword_timestamps_${msg.videoId}`;
    chrome.storage.local.get(key).then(result => {
      sendResponse(result[key] || []);
    });
    return true;
  }

  // Save screenshot to backend
  if (msg.type === 'SAVE_NETFLIX_SCREENSHOT') {
    saveScreenshotToBackend(msg.videoId, msg.timestamp, msg.dataUrl);
    return;
  }

  // Save thumbnail for Netflix video
  if (msg.type === 'SAVE_NETFLIX_THUMBNAIL') {
    saveThumbnailToBackend(msg.videoId, msg.dataUrl);
    return;
  }

  // Save audio to backend
  if (msg.type === 'SAVE_NETFLIX_AUDIO') {
    saveAudioToBackend(msg.videoId, msg.timestamp, msg.audioData);
    return;
  }

  // Save both screenshot and audio to backend
  if (msg.type === 'SAVE_NETFLIX_MEDIA') {
    if (msg.screenshotDataUrl) {
      saveScreenshotToBackend(msg.videoId, msg.timestamp, msg.screenshotDataUrl);
    }
    if (msg.audioData) {
      saveAudioToBackend(msg.videoId, msg.timestamp, msg.audioData);
    }
    return;
  }

  // Update watch time (from content scripts)
  if (msg.type === 'UPDATE_WATCH_TIME') {
    console.log(`[ClipIt] UPDATE_WATCH_TIME received: ${msg.videoId} +${msg.seconds}s`);
    updateWatchTime(msg.videoId, msg.seconds, msg.platform).then(sendResponse);
    return true;
  }

  // YouTube subtitles from content script (client-side fetch)
  if (msg.type === 'YOUTUBE_SUBTITLES') {
    console.log(`[ClipIt] YOUTUBE_SUBTITLES received: ${msg.videoId} (lang: ${msg.subtitles?.targetLanguage || 'ko'}, target: ${(msg.subtitles?.korean || msg.subtitles?.ukrainian || []).length}, en: ${msg.subtitles?.english?.length || 0})`);
    processYouTubeSubtitles(msg.videoId, msg.subtitles).then(sendResponse);
    return true;
  }
});

// ─── Netflix subtitle processing (received from content script) ──────────────

function detectSubtitleLanguage(url, content) {
  // Try to detect from URL parameters or content
  const urlLower = url.toLowerCase();

  // Common Netflix URL patterns for language
  if (urlLower.includes('ko') || urlLower.includes('korean')) return 'ko';
  if (urlLower.includes('uk') || urlLower.includes('ukrainian')) return 'uk';
  if (urlLower.includes('en') || urlLower.includes('english')) return 'en';

  // Try to detect from content
  const contentSample = content.slice(0, 2000).toLowerCase();

  // Check for Korean characters (Hangul)
  if (/[\uAC00-\uD7AF]/.test(contentSample)) return 'ko';

  // Check for Ukrainian characters (Cyrillic with Ukrainian-specific letters)
  if (/[\u0400-\u04FF]/.test(contentSample) && /[іїєґ]/i.test(contentSample)) return 'uk';

  // Check for mostly ASCII (likely English)
  if (/^[\x00-\x7F\s]+$/.test(contentSample.replace(/<[^>]*>/g, ''))) return 'en';

  return null;
}

function parseTTML(xml) {
  const subtitles = [];
  // Match <p> elements with timing
  const pRegex = /<p[^>]*begin="([^"]+)"[^>]*end="([^"]+)"[^>]*>([\s\S]*?)<\/p>/gi;
  let match;

  while ((match = pRegex.exec(xml)) !== null) {
    const begin = parseTimeToSeconds(match[1]);
    const end = parseTimeToSeconds(match[2]);
    const text = match[3]
      .replace(/<[^>]*>/g, '') // Remove HTML tags
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/\s+/g, ' ')
      .trim();

    if (text) {
      subtitles.push({
        start: begin,
        end: end,
        duration: end - begin,
        text: text,
      });
    }
  }

  return subtitles;
}

function parseWebVTT(vtt) {
  const subtitles = [];
  const lines = vtt.split('\n');
  let i = 0;

  while (i < lines.length) {
    // Look for timestamp line (00:00:00.000 --> 00:00:00.000)
    const timeLine = lines[i];
    const timeMatch = timeLine.match(/(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})/);

    if (timeMatch) {
      const start = parseTimeToSeconds(timeMatch[1]);
      const end = parseTimeToSeconds(timeMatch[2]);

      // Collect text lines until empty line
      i++;
      let textLines = [];
      while (i < lines.length && lines[i].trim() !== '') {
        textLines.push(lines[i].trim());
        i++;
      }

      const text = textLines.join(' ')
        .replace(/<[^>]*>/g, '')
        .trim();

      if (text) {
        subtitles.push({
          start: start,
          end: end,
          duration: end - start,
          text: text,
        });
      }
    }
    i++;
  }

  return subtitles;
}

function parseTimeToSeconds(timeStr) {
  // Handle formats: "00:00:00.000", "00:00.000", "00:00:00,000"
  const normalized = timeStr.replace(',', '.');
  const parts = normalized.split(':');

  if (parts.length === 3) {
    const [h, m, s] = parts;
    return parseInt(h) * 3600 + parseInt(m) * 60 + parseFloat(s);
  } else if (parts.length === 2) {
    const [m, s] = parts;
    return parseInt(m) * 60 + parseFloat(s);
  }
  return parseFloat(normalized);
}

async function trackNetflix(videoId, title, audioLang, episodeInfo) {
  try {
    const token = await getAuthToken();
    // Track the video with platform indicator
    const res = await fetch(`${API}/videos/track`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({
        video_id: `netflix_${videoId}`,
        title: title,
        platform: 'netflix',
        audio_lang: audioLang,
        season: episodeInfo?.season || null,
        episode: episodeInfo?.episode || null,
        episode_title: episodeInfo?.episodeTitle || null,
      }),
    });
    const data = await res.json();

    // Use shared updateStatus helper for language marking
    if (audioLang === 'ko' || audioLang === 'uk') {
      await updateStatus(`netflix_${videoId}`, audioLang, true);
      console.log(`[ClipIt] Marked video as having ${audioLang} (audio detected)`);
    }

    return { success: true, is_new: data.is_new };
  } catch (e) {
    console.error('[ClipIt] Error tracking Netflix:', e);
    return { success: false };
  }
}

async function updateNetflixTitle(videoId, title) {
  try {
    const res = await fetch(`${API}/videos/netflix_${videoId}/title`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (res.ok) {
      console.log(`[ClipIt] Updated Netflix title: ${title}`);
    }
  } catch (e) {
    // Silently fail - title update is optional
  }
}

async function updateNetflixAudioLanguage(videoId, audioLang) {
  if (audioLang === 'ko' || audioLang === 'uk') {
    await updateStatus(`netflix_${videoId}`, audioLang, true);
    console.log(`[ClipIt] Updated: ${audioLang} audio detected`);
  }
}

async function processNetflixSubtitles(videoId, subtitles, lang) {
  // Subtitles are already merged by content script
  const netflixVideoId = `netflix_${videoId}`;
  try {
    const res = await fetch(`${API}/netflix/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: netflixVideoId,
        language: lang,
        subtitles: subtitles,
      }),
    });

    const data = await res.json();

    // Store keyword timestamps for targeted screenshot capture
    if (data.keyword_timestamps && data.keyword_timestamps.length > 0) {
      const key = `keyword_timestamps_${videoId}`;
      await chrome.storage.local.set({ [key]: data.keyword_timestamps });
      console.log(`[ClipIt] Stored ${data.keyword_timestamps.length} keyword timestamps for ${videoId}`);
    }

    // Run vocab pipeline
    runVocabPipeline(netflixVideoId, lang);
  } catch (e) {
    console.error('[ClipIt] Error sending Netflix subtitles:', e);
  }
}

// ─── YouTube subtitle processing (from content script) ───────────────────────

async function processYouTubeSubtitles(videoId, subtitles) {
  console.log(`[ClipIt] Processing YouTube subtitles for ${videoId}`);
  const targetLanguage = await getActiveTrackingLanguage(subtitles.targetLanguage);
  const config = getLanguageConfig(targetLanguage);
  const targetSubtitles = getSubtitleListForLanguage(subtitles, targetLanguage);
  const uploadFlags = buildSubtitleUploadFlags(targetLanguage, targetSubtitles.length > 0);
  try {
    const res = await fetch(`${API}/youtube/subtitles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: videoId,
        korean: targetLanguage === 'ko' ? targetSubtitles : [],
        ukrainian: targetLanguage === 'uk' ? targetSubtitles : [],
        english: subtitles.english || [],
      }),
    });

    if (res.ok) {
      const data = await res.json();
      console.log(`[ClipIt] YouTube subtitles saved: ${videoId} (${config.statusBodyKey}: ${uploadFlags[config.statusBodyKey] || false})`);

      // Now run vocab pipeline for the app-selected language only.
      if (targetSubtitles.length > 0) {
        runVocabPipeline(videoId, targetLanguage);
      }

      return { success: true };
    } else {
      console.error('[ClipIt] Failed to save YouTube subtitles:', res.status);
      return { success: false };
    }
  } catch (e) {
    console.error('[ClipIt] Error processing YouTube subtitles:', e);
    return { success: false };
  }
}

async function trackAndPrefetch(videoId, title, lang = 'ko') {
  const token = await getAuthToken();
  if (!token) {
    console.warn('[ClipIt] No auth token — open the ClipIt app and log in first.');
    return { success: false, reason: 'no_token' };
  }

  try {
    // 1. Track the video
    const res = await fetch(`${API}/videos/track`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({ video_id: videoId, title }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      console.error('[ClipIt] Track failed:', res.status, err.detail || '');
      return { success: false, reason: res.status };
    }

    const data = await res.json();
    console.log(`[ClipIt] Tracked: ${videoId} — ${title} (new: ${data.is_new})`);
    void notifyAppVideoTracked(videoId, lang);

    // 2. Run vocab pipeline for the app-selected language only.
    runVocabPipeline(videoId, lang);

    return { success: true, is_new: data.is_new };
  } catch (e) {
    console.error('[ClipIt] trackAndPrefetch error:', e);
    return { success: false };
  }
}

async function runVocabPipeline(videoId, lang = 'ko') {
  console.log(`[ClipIt] runVocabPipeline starting: ${videoId} (${lang})`);
  const cacheKey = `vocab_${lang}_${videoId}`;

  // Check if we have a recent cache
  const existing = await chrome.storage.local.get(cacheKey);
  if (existing[cacheKey] && !existing[cacheKey].loading) {
    const age = Date.now() - (existing[cacheKey].cachedAt || 0);
    if (age < CACHE_TTL_MS) {
      console.log(`[ClipIt] runVocabPipeline: cache still fresh, skipping`);
      return; // Fresh cache, skip
    }
  }

  // Mark as loading
  await chrome.storage.local.set({ [cacheKey]: { loading: true, cachedAt: Date.now() } });

  try {
    // Step 1: fetch subtitles (skip for Netflix - already captured)
    if (!videoId.startsWith('netflix_')) {
      const config = getLanguageConfig(lang);
      console.log(`[Deadbird] Fetching subtitles for ${videoId} (${config.name})`);

      // Fetch subtitles directly from YouTube using the user's browser
      const subtitleData = await fetchAllSubtitles(videoId, lang);
      const subtitles = subtitleData?.subtitles || [];

      // If no target-language subtitles were found, mark the language unavailable
      // and avoid uploading an empty cache payload.
      if (subtitles.length === 0) {
        console.log(`[Deadbird] No ${lang} subtitles found for ${videoId}`);
        await updateStatus(videoId, lang, false);
        await chrome.storage.local.set({
          [cacheKey]: { loading: false, words: [], total: 0, cachedAt: Date.now() }
        });
        return;
      }

      // Upload subtitles to backend for storage
      try {
        const uploadRes = await fetch(`${API}/subtitles/upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_id: videoId,
            lang: lang,
            subtitles,
            ...buildSubtitleUploadFlags(lang, subtitles.length > 0),
          }),
        });

        if (uploadRes.ok) {
          console.log(`[Deadbird] Uploaded ${subtitles.length} subtitles to backend`);
        } else {
          console.error('[Deadbird] Failed to upload subtitles:', await uploadRes.text());
        }
      } catch (uploadError) {
        console.error('[Deadbird] Error uploading subtitles:', uploadError);
      }

      // Language exists even if no words match the frequency list later.
      await updateStatus(videoId, lang, true);
    }

    // Step 2: vocabulary (all words in freq list, no level filter)
    const vocabRes = await fetch(`${API}/vocabulary/${videoId}?limit=20&lang=${lang}`);
    if (!vocabRes.ok) throw new Error('vocab');
    const vocab = await vocabRes.json();

    if (!vocab.total_words) {
      // No words matched frequency list, but video may still have the language
      // Don't mark as false - just cache empty words
      console.log(`[Deadbird] No ${lang} vocab found in frequency list, caching empty result`);
      await chrome.storage.local.set({
        [cacheKey]: { loading: false, words: [], total: 0, cachedAt: Date.now() }
      });
      return;
    }

    // Step 3: flashcard data (English definitions + example sentences)
    const wordList = vocab.vocabulary.map(v => v.word);
    const fcRes = await fetch(`${API}/flashcard-data`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId, words: wordList, word_source: 'essential', language: lang }),
    });

    let words;
    if (fcRes.ok) {
      const fc = await fcRes.json();
      // Merge rank from vocab into flashcard data
      const rankMap = {};
      vocab.vocabulary.forEach(v => { rankMap[v.word] = v.rank; });
      words = fc.flashcards.map(card => ({
        ...card,
        rank: rankMap[card.target_word] || 0,
      }));

    } else {
      // Fallback: vocab words without flashcard enrichment
      words = vocab.vocabulary.map(v => ({
        target_word: v.word,
        dictionary_form: v.word,
        english: null,
        sentence: null,
        sentence_translation: null,
        rank: v.rank,
      }));
    }

    // Keep language status true because subtitles were found.
    console.log(`[Deadbird] Found ${words.length} words, setting has_${lang}=true`);
    await updateStatus(videoId, lang, true);

    await chrome.storage.local.set({
      [cacheKey]: { loading: false, words, total: words.length, cachedAt: Date.now() }
    });
  } catch (error) {
    console.error(`[Deadbird] Vocab pipeline error for ${videoId} (${lang}):`, error);
    await chrome.storage.local.set({
      [cacheKey]: { loading: false, error: true, words: null, cachedAt: Date.now() }
    });
  }
}

async function updateStatus(videoId, lang, value) {
  const config = getLanguageConfig(lang);
  if (!config.statusPath) {
    console.warn(`[ClipIt] No backend status endpoint configured for language: ${lang}`);
    return;
  }
  await fetch(`${API}/videos/${videoId}/${config.statusPath}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ [config.statusBodyKey]: value }),
  }).catch(() => {});
}

async function getCachedVocab(videoId, lang = 'ko') {
  const cacheKey = `vocab_${lang}_${videoId}`;
  const result = await chrome.storage.local.get(cacheKey);
  return result[cacheKey] || { loading: true };
}

// ─── Screenshot capture ──────────────────────────────────────────────────────

async function captureScreenshot(tabId, windowId) {
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab(windowId, {
      format: 'jpeg',
      quality: 85
    });
    return dataUrl;
  } catch (e) {
    console.error('[ClipIt] captureVisibleTab failed:', e);
    throw e;
  }
}

async function saveScreenshotToBackend(videoId, timestamp, dataUrl) {
  try {
    const res = await fetch(`${API}/netflix/screenshot`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId, timestamp, data_url: dataUrl }),
    });
    if (res.ok) {
      console.log(`[ClipIt] Screenshot saved to backend: ${videoId} @ ${timestamp}s`);
    }
  } catch (e) {
    console.error('[ClipIt] Failed to save screenshot:', e);
  }
}

async function saveAudioToBackend(videoId, timestamp, audioData) {
  try {
    const res = await fetch(`${API}/netflix/audio`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: videoId,
        timestamp: timestamp,
        audio_data: audioData.base64,
        mime_type: audioData.mimeType,
      }),
    });
    if (res.ok) {
      console.log(`[ClipIt] Audio saved to backend: ${videoId} @ ${timestamp}s`);
    }
  } catch (e) {
    console.error('[ClipIt] Failed to save audio:', e);
  }
}

async function saveThumbnailToBackend(videoId, dataUrl) {
  try {
    const res = await fetch(`${API}/netflix/thumbnail`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        video_id: videoId,
        data_url: dataUrl,
      }),
    });
    if (res.ok) {
      console.log(`[ClipIt] Thumbnail saved for: ${videoId}`);
    }
  } catch (e) {
    console.error('[ClipIt] Failed to save thumbnail:', e);
  }
}

// ─── Watch time tracking ─────────────────────────────────────────────────────

async function updateWatchTime(videoId, seconds, platform) {
  const token = await getAuthToken();
  if (!token) {
    console.warn('[ClipIt] No auth token — cannot update watch time');
    return { success: false, reason: 'no_token' };
  }

  // Prepend platform prefix for Netflix videos if not already present
  const fullVideoId = platform === 'netflix' && !videoId.startsWith('netflix_')
    ? `netflix_${videoId}`
    : videoId;

  try {
    const res = await fetch(`${API}/videos/watch-time`, {
      method: 'POST',
      headers: authHeaders(token),
      body: JSON.stringify({
        video_id: fullVideoId,
        seconds: seconds,
      }),
    });

    if (res.ok) {
      console.log(`[ClipIt] Watch time updated: +${seconds}s for ${fullVideoId}`);
      return { success: true };
    } else {
      console.error('[ClipIt] Watch time update failed:', res.status);
      return { success: false, reason: res.status };
    }
  } catch (e) {
    console.error('[ClipIt] Watch time update error:', e);
    return { success: false, reason: e.message };
  }
}
