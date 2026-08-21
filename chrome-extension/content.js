/**
 * ClipIt — content script
 * Detects YouTube video navigation (SPA) and sends video ID + title to background.
 * Also tracks actual watch time while video is playing.
 * Caption filtering (Korean + English required) is handled server-side.
 */

// ─── Inject timedtext interceptor into page context ───────────────────────────
// This captures YouTube's actual subtitle fetches (which work, unlike our attempts)
// Must use external script file to bypass YouTube's CSP
(function injectTimedtextInterceptor() {
  const script = document.createElement('script');
  script.src = chrome.runtime.getURL('youtube-inject.js');
  script.onload = function() {
    console.log('[ClipIt] YouTube interceptor script loaded');
    this.remove();
  };
  script.onerror = function(e) {
    console.error('[ClipIt] Failed to load YouTube interceptor:', e);
  };
  (document.head || document.documentElement).appendChild(script);
})();

let preferredLanguage = 'ko';

const LANGUAGE_CONFIGS = {
  ko: { code: 'ko', subtitleKey: 'korean' },
  uk: { code: 'uk', subtitleKey: 'ukrainian' },
};

const SUPPORTED_LANGUAGES = ['ko', 'uk'];

function getLanguageConfig(lang = 'ko') {
  return LANGUAGE_CONFIGS[lang] || { code: lang, subtitleKey: lang };
}

function normalizeLanguage(lang) {
  return SUPPORTED_LANGUAGES.includes(lang) ? lang : 'ko';
}

chrome.storage.local.get('language').then(result => {
  preferredLanguage = normalizeLanguage(result.language);
}).catch(() => {});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.language) {
    preferredLanguage = normalizeLanguage(changes.language.newValue);
  }
});

function isTargetSubtitleLang(lang, targetLang = preferredLanguage) {
  return lang === targetLang || lang?.startsWith(`${targetLang}-`);
}

// Store intercepted subtitles
const interceptedSubtitles = { target: null, en: null, lang: 'ko' };
let subtitlesSentForVideo = null; // Track which video we've already sent subtitles for
let subtitleSendTimeout = null;

// Listen for intercepted timedtext data
window.addEventListener('message', (event) => {
  if (event.source !== window || event.data?.type !== 'CLIPIT_TIMEDTEXT_CAPTURED') return;

  const { lang, content, url } = event.data;
  console.log('[ClipIt] Received intercepted timedtext:', lang, 'length:', content?.length);

  // Parse the subtitle content
  const subs = parseTimedtextContent(content);
  console.log('[ClipIt] Parsed', subs.length, 'subtitles from intercepted', lang);

  if (isTargetSubtitleLang(lang)) {
    interceptedSubtitles.target = subs;
    interceptedSubtitles.lang = preferredLanguage;
  } else if (lang === 'en' || lang.startsWith('en')) {
    interceptedSubtitles.en = subs;
  }

  const videoId = new URLSearchParams(location.search).get('v');
  if (!videoId) return;

  // Don't re-send if we already sent for this video with target-language subtitles
  if (subtitlesSentForVideo === videoId && interceptedSubtitles.target?.length > 0) {
    console.log('[ClipIt] Already sent subtitles for this video, skipping');
    return;
  }

  // Clear any pending send timeout
  if (subtitleSendTimeout) {
    clearTimeout(subtitleSendTimeout);
  }

  // Wait 500ms for both languages to arrive before sending
  subtitleSendTimeout = setTimeout(() => {
    if (interceptedSubtitles.target && interceptedSubtitles.target.length > 0) {
      console.log('[ClipIt] Sending intercepted subtitles to backend:', videoId);
      console.log('[ClipIt] Target:', interceptedSubtitles.lang, interceptedSubtitles.target.length, 'English:', interceptedSubtitles.en?.length || 0);
      subtitlesSentForVideo = videoId;
      const targetKey = getLanguageConfig(interceptedSubtitles.lang).subtitleKey;
      try {
        chrome.runtime.sendMessage({
          type: 'YOUTUBE_SUBTITLES',
          videoId,
          subtitles: {
            targetLanguage: interceptedSubtitles.lang,
            [targetKey]: interceptedSubtitles.target,
            english: interceptedSubtitles.en || [],
            hasEnglish: (interceptedSubtitles.en?.length || 0) > 0
          }
        }, () => { try { void chrome.runtime.lastError; } catch (_) {} });
      } catch (_) {}
    }
  }, 500);
});

function parseTimedtextContent(content) {
  if (!content) return [];

  // Try JSON3 format first
  if (content.trim().startsWith('{')) {
    try {
      const data = JSON.parse(content);
      if (data.events) {
        return data.events
          .filter(e => e.segs && e.segs.length > 0)
          .map(e => ({
            start: (e.tStartMs || 0) / 1000,
            duration: (e.dDurationMs || 0) / 1000,
            text: e.segs.map(s => s.utf8 || '').join('').trim()
          }))
          .filter(s => s.text);
      }
    } catch (e) {}
  }

  // Try XML format
  if (content.trim().startsWith('<')) {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(content, 'text/xml');
      const textElements = doc.querySelectorAll('text');
      return Array.from(textElements).map(el => ({
        start: parseFloat(el.getAttribute('start') || '0'),
        duration: parseFloat(el.getAttribute('dur') || '0'),
        text: el.textContent?.trim() || ''
      })).filter(s => s.text);
    } catch (e) {}
  }

  return [];
}

let lastTrackedId = null;

// ─── Watch time tracking ─────────────────────────────────────────────────────
let watchTimeAccumulator = 0; // Seconds accumulated since last sync
let lastWatchTimeSync = Date.now();
let isVideoPlaying = false;
let watchTimeInterval = null;
const WATCH_TIME_SYNC_INTERVAL = 30000; // Sync every 30 seconds

function getVideoElement() {
  return document.querySelector('video.html5-main-video') || document.querySelector('video');
}

let watchTimeRetryCount = 0;
const MAX_WATCH_TIME_RETRIES = 10;

function startWatchTimeTracking() {
  if (watchTimeInterval) return; // Already tracking

  const video = getVideoElement();
  if (!video) {
    // Retry up to 10 times (5 seconds total) if video element not found
    if (watchTimeRetryCount < MAX_WATCH_TIME_RETRIES) {
      watchTimeRetryCount++;
      console.log(`[ClipIt] Video element not found, retrying (${watchTimeRetryCount}/${MAX_WATCH_TIME_RETRIES})...`);
      setTimeout(startWatchTimeTracking, 500);
    } else {
      console.log('[ClipIt] Video element not found after max retries');
      watchTimeRetryCount = 0;
    }
    return;
  }

  watchTimeRetryCount = 0; // Reset retry count on success
  console.log('[ClipIt] Video element found, attaching event listeners');

  // Listen for play/pause events
  video.addEventListener('play', () => {
    isVideoPlaying = true;
    console.log('[ClipIt] Video playing - tracking watch time');
  });

  video.addEventListener('pause', () => {
    isVideoPlaying = false;
    console.log('[ClipIt] Video paused');
    // Sync immediately on pause
    syncWatchTime();
  });

  video.addEventListener('ended', () => {
    isVideoPlaying = false;
    syncWatchTime();
  });

  // Set initial state - check if video is already playing
  isVideoPlaying = !video.paused;
  if (isVideoPlaying) {
    console.log('[ClipIt] Video already playing on attach');
  }

  // Accumulate watch time every second
  watchTimeInterval = setInterval(() => {
    if (isVideoPlaying && lastTrackedId) {
      watchTimeAccumulator++;

      // Sync periodically
      if (Date.now() - lastWatchTimeSync >= WATCH_TIME_SYNC_INTERVAL) {
        syncWatchTime();
      }
    }
  }, 1000);
}

function syncWatchTime() {
  if (watchTimeAccumulator > 0 && lastTrackedId) {
    const secondsToSync = watchTimeAccumulator;
    watchTimeAccumulator = 0;
    lastWatchTimeSync = Date.now();

    console.log(`[ClipIt] Syncing ${secondsToSync}s watch time for ${lastTrackedId}`);

    try {
      chrome.runtime.sendMessage({
        type: 'UPDATE_WATCH_TIME',
        videoId: lastTrackedId,
        seconds: secondsToSync,
        platform: 'youtube'
      }, () => { try { void chrome.runtime.lastError; } catch (_) {} });
    } catch (_) {}
  }
}

function resetWatchTimeTracking() {
  // Sync any remaining time before resetting
  syncWatchTime();
  watchTimeAccumulator = 0;
  isVideoPlaying = false;
}

function getVideoId() {
  return new URLSearchParams(location.search).get('v');
}

function getTitle() {
  const selectors = [
    'h1.ytd-watch-metadata yt-formatted-string',
    '#above-the-fold #title h1',
    'ytd-watch-metadata h1 yt-formatted-string',
    '#title h1 yt-formatted-string',
    'h1.style-scope.ytd-watch-metadata',
  ];
  for (const s of selectors) {
    const el = document.querySelector(s);
    if (el?.textContent?.trim()) return el.textContent.trim();
  }
  const documentTitle = document.title.replace(' - YouTube', '').trim();
  return documentTitle === 'YouTube' ? 'Unknown' : (documentTitle || 'Unknown');
}

function sendTrack(videoId) {
  // Reset watch time when switching videos
  resetWatchTimeTracking();

  // YouTube can take several seconds to replace its generic page title and
  // render the watch-page heading. Only send a title once it is meaningful;
  // the background worker has its own bounded fallback for exceptional cases.
  let attempts = 0;
  const MAX_TITLE_ATTEMPTS = 60; // 30 seconds
  const interval = setInterval(() => {
    try {
      if (getVideoId() !== videoId) {
        clearInterval(interval);
        return;
      }

      const title = getTitle();
      attempts++;
      if (title !== 'Unknown') {
        clearInterval(interval);
        console.log('[ClipIt] Sending title after', attempts, 'attempts:', title);

        chrome.runtime.sendMessage({
          type: 'TRACK_VIDEO',
          videoId,
          title,
          lang: preferredLanguage,
        }, () => { try { void chrome.runtime.lastError; } catch (_) {} });

        // Start tracking watch time for this video
        setTimeout(startWatchTimeTracking, 1000);

        // Fetch and send subtitles client-side (bypasses YouTube IP blocking on cloud servers)
        setTimeout(() => sendSubtitlesToBackground(videoId, preferredLanguage), 2000);
      } else if (attempts >= MAX_TITLE_ATTEMPTS) {
        clearInterval(interval);
        console.warn(`[ClipIt] No usable YouTube title after ${MAX_TITLE_ATTEMPTS} attempts: ${videoId}`);
      }
    } catch (_) {
      // Extension context invalidated (extension reloaded while tab was open) — stop silently
      clearInterval(interval);
    }
  }, 500);
}

function checkForNewVideo() {
  try {
    const videoId = getVideoId();
    if (videoId && videoId !== lastTrackedId) {
      lastTrackedId = videoId;
      lastHref = location.href;
      sendTrack(videoId);
    }
  } catch (_) {}
}

let lastHref = location.href;

// YouTube fires this event on every SPA navigation — most reliable trigger
window.addEventListener('yt-navigate-finish', checkForNewVideo);

// Fallback interval for cases where the event doesn't fire
const navInterval = setInterval(() => {
  try {
    if (location.href === lastHref) return;
    lastHref = location.href;
    checkForNewVideo();
  } catch (_) {
    clearInterval(navInterval);
  }
}, 1000);

// Track on initial page load with retry mechanism
// Sometimes the page isn't fully ready when content script runs
let initialLoadRetries = 0;
const MAX_INITIAL_RETRIES = 5;

function tryInitialVideoDetection() {
  const videoId = getVideoId();
  if (videoId) {
    console.log('[ClipIt] Initial video detected:', videoId);
    checkForNewVideo();
  } else if (initialLoadRetries < MAX_INITIAL_RETRIES) {
    initialLoadRetries++;
    console.log(`[ClipIt] No video ID yet, retrying initial detection (${initialLoadRetries}/${MAX_INITIAL_RETRIES})...`);
    setTimeout(tryInitialVideoDetection, 500);
  }
}

// Start initial detection
tryInitialVideoDetection();

// Sync watch time when page is about to unload
window.addEventListener('beforeunload', () => {
  syncWatchTime();
});

// Also sync on visibility change (user switches tabs)
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    syncWatchTime();
  }
});

// ─── Hide subtitles feature ──────────────────────────────────────────────────
const HIDE_SUBTITLES_STYLE_ID = 'deadbird-hide-subtitles';

function setSubtitlesHidden(hide) {
  let styleEl = document.getElementById(HIDE_SUBTITLES_STYLE_ID);

  if (hide) {
    if (!styleEl) {
      styleEl = document.createElement('style');
      styleEl.id = HIDE_SUBTITLES_STYLE_ID;
      styleEl.textContent = `
        /* Hide YouTube subtitles/captions - covers various caption containers */
        .ytp-caption-window-container,
        .caption-window,
        .captions-text,
        .ytp-caption-segment {
          opacity: 0 !important;
          pointer-events: none !important;
        }
      `;
      document.head.appendChild(styleEl);
      console.log('[ClipIt] YouTube subtitles hidden (still being captured)');
    }
  } else {
    if (styleEl) {
      styleEl.remove();
      console.log('[ClipIt] YouTube subtitles visible');
    }
  }
}

// Listen for hide subtitles toggle from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'SET_HIDE_SUBTITLES') {
    setSubtitlesHidden(msg.hide);
    sendResponse({ success: true });
  }
});

// Apply saved preference on load
chrome.storage.local.get('hideSubtitles').then(result => {
  if (result.hideSubtitles) {
    setSubtitlesHidden(true);
  }
});

// ─── Client-side subtitle fetching ───────────────────────────────────────────
// Fetch subtitles directly from YouTube (bypasses cloud IP blocking)

async function fetchYouTubeSubtitles(videoId, targetLang = preferredLanguage) {
  console.log(`[ClipIt] Fetching ${targetLang} subtitles client-side for ${videoId}`);

  try {
    // Get the caption tracks from ytInitialPlayerResponse
    const playerResponse = await getPlayerResponse(videoId);
    if (!playerResponse) {
      console.log('[ClipIt] Could not get player response');
      return null;
    }

    console.log('[ClipIt] Player response captions:', playerResponse?.captions);
    const captionTracks = playerResponse?.captions?.playerCaptionsTracklistRenderer?.captionTracks;
    if (!captionTracks || captionTracks.length === 0) {
      console.log('[ClipIt] No caption tracks available');
      // Log what we did find
      console.log('[ClipIt] Available keys in captions:', Object.keys(playerResponse?.captions || {}));
      return null;
    }

    console.log('[ClipIt] Found caption tracks:', captionTracks.map(t => t.languageCode));

    // Find target-language and English tracks
    const targetTrack = captionTracks.find(t => t.languageCode === targetLang || t.languageCode?.startsWith(`${targetLang}-`));
    const enTrack = captionTracks.find(t => t.languageCode === 'en' || t.languageCode?.startsWith('en'));
    console.log('[ClipIt] Target track:', targetTrack?.languageCode, 'English track:', enTrack?.languageCode);
    console.log('[ClipIt] Target baseUrl:', targetTrack?.baseUrl?.substring(0, 150));

    if (!targetTrack && !enTrack) {
      console.log('[ClipIt] No target-language or English subtitles found');
      return null;
    }

    // Fetch the subtitle content using baseUrl but with ip=0.0.0.0 stripped
    const [targetSubs, enSubs] = await Promise.all([
      targetTrack ? fetchSubtitleTrackSimple(videoId, targetTrack.languageCode, targetTrack.baseUrl) : Promise.resolve([]),
      enTrack ? fetchSubtitleTrackSimple(videoId, enTrack.languageCode, enTrack.baseUrl) : Promise.resolve([])
    ]);

    console.log(`[ClipIt] Fetched ${targetSubs.length} ${targetLang} and ${enSubs.length} English subtitles`);

    const targetKey = getLanguageConfig(targetLang).subtitleKey;
    return {
      targetLanguage: targetLang,
      [targetKey]: targetSubs,
      english: enSubs,
      hasEnglish: enSubs.length > 0
    };
  } catch (e) {
    console.error('[ClipIt] Error fetching subtitles:', e);
    return null;
  }
}

async function getPlayerResponse(videoId) {
  // Try to get from page's ytInitialPlayerResponse first
  try {
    // The player response is often embedded in a script tag
    const scripts = document.querySelectorAll('script');
    console.log(`[ClipIt] Searching ${scripts.length} scripts for ytInitialPlayerResponse`);
    for (const script of scripts) {
      const text = script.textContent;
      if (text && text.includes('ytInitialPlayerResponse')) {
        console.log('[ClipIt] Found script with ytInitialPlayerResponse');
        const match = text.match(/ytInitialPlayerResponse\s*=\s*(\{.+?\});/s);
        if (match) {
          console.log('[ClipIt] Successfully parsed ytInitialPlayerResponse from script');
          return JSON.parse(match[1]);
        }
      }
    }
    console.log('[ClipIt] ytInitialPlayerResponse not found in scripts, trying window object');
    // Also try window.ytInitialPlayerResponse directly
    if (window.ytInitialPlayerResponse) {
      console.log('[ClipIt] Found window.ytInitialPlayerResponse');
      return window.ytInitialPlayerResponse;
    }
  } catch (e) {
    console.log('[ClipIt] Could not parse ytInitialPlayerResponse from page:', e.message);
  }

  // Fallback: fetch the watch page and extract player response
  console.log('[ClipIt] Trying fallback: fetching watch page directly');
  try {
    const response = await fetch(`https://www.youtube.com/watch?v=${videoId}`);
    console.log('[ClipIt] Fetch response status:', response.status);
    const html = await response.text();
    console.log('[ClipIt] Fetched HTML length:', html.length);
    const match = html.match(/ytInitialPlayerResponse\s*=\s*(\{.+?\});/s);
    if (match) {
      console.log('[ClipIt] Successfully parsed ytInitialPlayerResponse from fetched page');
      return JSON.parse(match[1]);
    }
    console.log('[ClipIt] ytInitialPlayerResponse not found in fetched HTML');
  } catch (e) {
    console.log('[ClipIt] Could not fetch player response:', e.message);
  }

  return null;
}

async function fetchSubtitleTrackSimple(videoId, lang, baseUrl) {
  // Strategy: Try innertube API first (more reliable), then baseUrl with credentials
  console.log('[ClipIt] Fetching subtitle track for', lang);

  // Method 1: Try YouTube's innertube API (internal API, no IP restrictions)
  try {
    const innertubeResult = await fetchViaInnertube(videoId, lang);
    if (innertubeResult && innertubeResult.length > 0) {
      console.log('[ClipIt] Got', innertubeResult.length, 'subtitles via innertube');
      return innertubeResult;
    }
  } catch (e) {
    console.log('[ClipIt] Innertube method failed:', e.message);
  }

  // Method 2: Use baseUrl with credentials
  if (baseUrl) {
    let url = baseUrl;
    if (!url.includes('fmt=')) {
      url += '&fmt=json3';
    }
    console.log('[ClipIt] Trying baseUrl:', url.substring(0, 150) + '...');

    try {
      let response = await fetch(url, { credentials: 'include' });
      console.log('[ClipIt] Fetch status:', response.status);

      if (response.ok) {
        const text = await response.text();
        console.log('[ClipIt] Response length:', text.length);

        if (text && text.length > 10) {
          if (text.trim().startsWith('{')) {
            try {
              const data = JSON.parse(text);
              if (data.events) {
                const subs = data.events
                  .filter(e => e.segs && e.segs.length > 0)
                  .map(e => ({
                    start: (e.tStartMs || 0) / 1000,
                    duration: (e.dDurationMs || 0) / 1000,
                    text: e.segs.map(s => s.utf8 || '').join('').trim()
                  }))
                  .filter(s => s.text);
                console.log('[ClipIt] Parsed', subs.length, 'subtitles from JSON');
                return subs;
              }
            } catch (jsonErr) {
              console.log('[ClipIt] JSON parse error:', jsonErr.message);
            }
          }

          if (text.trim().startsWith('<')) {
            const subs = parseSubtitleXML(text);
            console.log('[ClipIt] Parsed', subs.length, 'subtitles from XML');
            return subs;
          }
        }
      }

      // Try XML format (same URL without fmt=json3)
      console.log('[ClipIt] Trying XML format with original baseUrl');
      response = await fetch(baseUrl, { credentials: 'include' });
      if (response.ok) {
        const xmlText = await response.text();
        console.log('[ClipIt] XML response length:', xmlText.length);
        if (xmlText && xmlText.trim().startsWith('<')) {
          const subs = parseSubtitleXML(xmlText);
          console.log('[ClipIt] Parsed', subs.length, 'subtitles from XML');
          return subs;
        }
      }
    } catch (e) {
      console.log('[ClipIt] BaseUrl fetch error:', e.message);
    }
  }

  return [];
}

async function fetchViaInnertube(videoId, lang) {
  // Method 1: Try to extract subtitle data from YouTube's player object
  try {
    const playerSubs = await extractFromPlayer(videoId, lang);
    if (playerSubs && playerSubs.length > 0) {
      console.log('[ClipIt] Got', playerSubs.length, 'subtitles from player object');
      return playerSubs;
    }
  } catch (e) {
    console.log('[ClipIt] Player extraction failed:', e.message);
  }

  // Method 2: Try innertube API with full context
  const apiKey = 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8';
  const url = 'https://www.youtube.com/youtubei/v1/player?key=' + apiKey;

  // Get additional context from the page
  let clientVersion = '2.20240401.00.00';
  let visitorData = '';
  try {
    const ytcfg = window.ytcfg?.data_ || {};
    clientVersion = ytcfg.INNERTUBE_CLIENT_VERSION || clientVersion;
    visitorData = ytcfg.VISITOR_DATA || '';
  } catch (e) {}

  const body = {
    context: {
      client: {
        clientName: 'WEB',
        clientVersion: clientVersion,
        hl: 'en',
        gl: 'US',
        visitorData: visitorData
      }
    },
    videoId: videoId
  };

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      credentials: 'include'
    });

    if (!response.ok) {
      console.log('[ClipIt] Innertube player response not ok:', response.status);
      return [];
    }

    const data = await response.json();
    console.log('[ClipIt] Innertube response has captions:', !!data?.captions);
    const captionTracks = data?.captions?.playerCaptionsTracklistRenderer?.captionTracks;

    if (!captionTracks) {
      console.log('[ClipIt] No caption tracks in innertube response');
      console.log('[ClipIt] Innertube playabilityStatus:', data?.playabilityStatus?.status);
      return [];
    }

    console.log('[ClipIt] Innertube caption tracks:', captionTracks.map(t => t.languageCode));

    const track = captionTracks.find(t =>
      t.languageCode === lang || t.languageCode?.startsWith(lang + '-')
    );

    if (!track?.baseUrl) {
      console.log('[ClipIt] No track found for language:', lang);
      return [];
    }

    // Check if this URL also has ip=0.0.0.0
    console.log('[ClipIt] Innertube baseUrl for', lang, ':', track.baseUrl.substring(0, 150));

    const subResponse = await fetch(track.baseUrl + '&fmt=json3', { credentials: 'include' });
    if (!subResponse.ok) {
      console.log('[ClipIt] Subtitle fetch from innertube URL failed:', subResponse.status);
      return [];
    }

    const text = await subResponse.text();
    console.log('[ClipIt] Innertube subtitle response length:', text.length);

    if (text && text.length > 10 && text.trim().startsWith('{')) {
      const subData = JSON.parse(text);
      if (subData.events) {
        const subs = subData.events
          .filter(e => e.segs && e.segs.length > 0)
          .map(e => ({
            start: (e.tStartMs || 0) / 1000,
            duration: (e.dDurationMs || 0) / 1000,
            text: e.segs.map(s => s.utf8 || '').join('').trim()
          }))
          .filter(s => s.text);
        console.log('[ClipIt] Parsed', subs.length, 'subtitles from innertube');
        return subs;
      }
    }

    return [];
  } catch (e) {
    console.log('[ClipIt] Innertube fetch error:', e.message);
    return [];
  }
}

async function extractFromPlayer(videoId, lang) {
  // Try to get subtitles from YouTube's player object
  const player = document.getElementById('movie_player');
  if (!player) {
    console.log('[ClipIt] No movie_player element found');
    return [];
  }

  // Check if player has caption-related methods
  if (typeof player.getOption !== 'function') {
    console.log('[ClipIt] Player does not have getOption method');
    return [];
  }

  try {
    // Get available caption tracks
    const tracklist = player.getOption('captions', 'tracklist');
    console.log('[ClipIt] Player tracklist:', tracklist);

    if (!tracklist || tracklist.length === 0) {
      return [];
    }

    // Find the track for our language
    const track = tracklist.find(t =>
      t.languageCode === lang || t.languageCode?.startsWith(lang + '-') ||
      t.vss_id?.includes(lang)
    );

    if (!track) {
      console.log('[ClipIt] No track for', lang, 'in player');
      return [];
    }

    console.log('[ClipIt] Found player track:', track);

    // Try to get the caption text - this might require the captions to be enabled
    // Enable captions temporarily
    const currentTrack = player.getOption('captions', 'track');
    player.setOption('captions', 'track', track);

    // Wait a bit for captions to load
    await new Promise(resolve => setTimeout(resolve, 500));

    // Try to extract from the caption window in DOM
    const captionWindow = document.querySelector('.ytp-caption-window-container');
    if (captionWindow) {
      console.log('[ClipIt] Found caption window container');
    }

    // Restore original track setting
    if (currentTrack) {
      player.setOption('captions', 'track', currentTrack);
    }

    // This method might not give us full subtitle data, return empty
    return [];
  } catch (e) {
    console.log('[ClipIt] Error extracting from player:', e.message);
    return [];
  }
}

async function fetchSubtitleTrack(baseUrl) {
  console.log('[ClipIt] Fetching subtitle track from:', baseUrl.substring(0, 100) + '...');
  try {
    // Try JSON format first
    let url = baseUrl + '&fmt=json3';
    let response = await fetch(url);
    console.log('[ClipIt] JSON3 fetch status:', response.status);

    if (response.ok) {
      const text = await response.text();
      console.log('[ClipIt] JSON3 response length:', text.length, 'starts with:', text.substring(0, 100));
      if (text && text.trim().startsWith('{')) {
        try {
          const data = JSON.parse(text);
          console.log('[ClipIt] JSON3 parsed, has events:', !!data.events, 'event count:', data.events?.length);
          if (data.events) {
            const subs = data.events
              .filter(e => e.segs && e.segs.length > 0)
              .map(e => ({
                start: (e.tStartMs || 0) / 1000,
                duration: (e.dDurationMs || 0) / 1000,
                text: e.segs.map(s => s.utf8 || '').join('').trim()
              }))
              .filter(s => s.text);
            console.log('[ClipIt] Parsed', subs.length, 'subtitles from JSON3');
            return subs;
          }
        } catch (jsonErr) {
          console.log('[ClipIt] JSON parse failed:', jsonErr.message);
        }
      }
    }

    // Fallback to XML format
    console.log('[ClipIt] Trying XML fallback');
    response = await fetch(baseUrl);
    console.log('[ClipIt] XML fetch status:', response.status);
    if (!response.ok) return [];

    const xmlText = await response.text();
    console.log('[ClipIt] XML response length:', xmlText.length);
    return parseSubtitleXML(xmlText);
  } catch (e) {
    console.error('[ClipIt] Error fetching subtitle track:', e);
    return [];
  }
}

function parseSubtitleXML(xmlText) {
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(xmlText, 'text/xml');
    const textElements = doc.querySelectorAll('text');

    return Array.from(textElements).map(el => ({
      start: parseFloat(el.getAttribute('start') || '0'),
      duration: parseFloat(el.getAttribute('dur') || '0'),
      text: el.textContent?.trim() || ''
    })).filter(s => s.text);
  } catch (e) {
    console.error('[ClipIt] Error parsing subtitle XML:', e);
    return [];
  }
}

// Send subtitles to background script after tracking
async function sendSubtitlesToBackground(videoId, targetLang = preferredLanguage) {
  // Reset intercepted subtitles for this video
  interceptedSubtitles.target = null;
  interceptedSubtitles.en = null;
  interceptedSubtitles.lang = targetLang;
  subtitlesSentForVideo = null; // Allow sending for new video

  // Method 1: Wait for player to be ready, then trigger caption loading
  console.log('[ClipIt] Waiting for player to be ready...');

  // Try multiple times to trigger caption loading (player takes time to initialize)
  for (let attempt = 0; attempt < 5; attempt++) {
    await new Promise(resolve => setTimeout(resolve, 1000));

    if (await triggerCaptionLoading(targetLang)) {
      console.log('[ClipIt] Caption loading triggered on attempt', attempt + 1);
      break;
    }
    console.log('[ClipIt] Player not ready, attempt', attempt + 1, 'of 5');
  }

  // Wait for interceptor to capture subtitles (YouTube takes a moment to load them)
  console.log('[ClipIt] Waiting for interceptor to capture subtitles...');
  await new Promise(resolve => setTimeout(resolve, 3000));

  // Check if interceptor captured any subtitles
  if (interceptedSubtitles.target && interceptedSubtitles.target.length > 0) {
    console.log('[ClipIt] Using intercepted subtitles (already sent by interceptor listener)');
    return; // Interceptor listener already sent them
  }

  // Method 2: Fall back to manual fetch attempts
  console.log('[ClipIt] Interceptor did not capture subtitles, trying manual fetch...');
  const subtitles = await fetchYouTubeSubtitles(videoId, targetLang);
  const targetKey = getLanguageConfig(targetLang).subtitleKey;
  if (subtitles?.[targetKey]?.length > 0) {
    try {
      chrome.runtime.sendMessage({
        type: 'YOUTUBE_SUBTITLES',
        videoId,
        subtitles
      }, () => { try { void chrome.runtime.lastError; } catch (_) {} });
      console.log(`[ClipIt] Sent subtitles to background for ${videoId}`);
    } catch (_) {}
  }
}

async function triggerCaptionLoading(targetLang = preferredLanguage) {
  // Method 1: Try player API if available - switch through tracks to force fetches
  const player = document.getElementById('movie_player');
  if (player && typeof player.getOption === 'function') {
    try {
      const tracklist = player.getOption('captions', 'tracklist');
      console.log('[ClipIt] Available caption tracks via API:', tracklist?.map(t => t.languageCode) || 'none');

      if (tracklist && tracklist.length > 0) {
        // Find target-language and English tracks
        const targetTrack = tracklist.find(t => t.languageCode === targetLang || t.languageCode?.startsWith(`${targetLang}-`));
        const enTrack = tracklist.find(t => t.languageCode === 'en' || t.languageCode?.startsWith('en'));

        // Load target-language track first (this triggers a fetch)
        if (targetTrack) {
          console.log('[ClipIt] Triggering target caption track via API');
          player.setOption('captions', 'track', targetTrack);
          await new Promise(resolve => setTimeout(resolve, 500));
        }

        // Then load English track (this triggers another fetch)
        if (enTrack) {
          console.log('[ClipIt] Triggering English caption track via API');
          player.setOption('captions', 'track', enTrack);
          await new Promise(resolve => setTimeout(resolve, 500));
        }

        // Switch back to target-language captions if available
        if (targetTrack) {
          player.setOption('captions', 'track', targetTrack);
        }

        if (targetTrack || enTrack) {
          return true;
        }
      }
    } catch (e) {
      console.log('[ClipIt] Player API error:', e.message);
    }
  }

  // Method 2: Try clicking the CC button directly
  const ccButton = document.querySelector('.ytp-subtitles-button');
  if (ccButton) {
    const isPressed = ccButton.getAttribute('aria-pressed') === 'true';
    console.log('[ClipIt] Found CC button, currently pressed:', isPressed);

    if (!isPressed) {
      console.log('[ClipIt] Clicking CC button to enable captions...');
      ccButton.click();
      return true;
    } else {
      // Captions already enabled - we need to force YouTube to make a fresh request
      // Do a longer toggle to ensure the request happens
      console.log('[ClipIt] Captions already enabled, toggling off...');
      ccButton.click();
      await new Promise(resolve => setTimeout(resolve, 300));
      console.log('[ClipIt] Toggling captions back on...');
      ccButton.click();
      return true;
    }
  }

  console.log('[ClipIt] No caption trigger method available yet');
  return false;
}
