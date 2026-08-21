/**
 * token-bridge.js — content script injected into the ClipIt frontend.
 * Reads the Supabase session from localStorage and syncs its access token into
 * chrome.storage.local so that the background service worker and popup can
 * attach it to authenticated API requests.
 *
 * The frontend uses Supabase for auth (see clipit-frontend/src/lib/supabaseClient.ts)
 * and Supabase's client persists the session itself under a fixed key derived
 * from the project ref, not a key this app chooses — see SUPABASE_AUTH_KEY below.
 *
 * The `storage` event only fires in OTHER windows, not the window that called setItem.
 * So we poll every second to catch same-window login/logout events.
 *
 * Injected into:
 * - https://www.joinclipit.com/* (production)
 */

// Supabase project ref is pyvyjdzwjgdbainzoiug (clipit-frontend/.env.production);
// the JS client always stores the session under `sb-<project-ref>-auth-token`.
const SUPABASE_AUTH_KEY = 'sb-pyvyjdzwjgdbainzoiug-auth-token';

let lastSynced = null;
let lastLanguage = null;
let syncInterval = null;

function extensionStorageAvailable() {
  return Boolean(chrome?.runtime?.id && chrome?.storage?.local);
}

function stopSyncingAfterReload(error) {
  console.warn('[ClipIt] Extension context unavailable; refresh the page after reloading the extension.', error);
  if (syncInterval) {
    clearInterval(syncInterval);
    syncInterval = null;
  }
}

function readSupabaseAccessToken() {
  const raw = localStorage.getItem(SUPABASE_AUTH_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw);
    return session?.access_token || null;
  } catch (error) {
    console.warn('[ClipIt] Failed to parse Supabase session', error);
    return null;
  }
}

function syncToken() {
  try {
    if (!extensionStorageAvailable()) return;
    const token = readSupabaseAccessToken();
    if (token === lastSynced) return; // nothing changed
    lastSynced = token;
    if (token) {
      chrome.storage.local.set({ deadbird_token: token });
      console.log('[ClipIt] Token synced to extension storage');
    } else {
      chrome.storage.local.remove('deadbird_token');
      console.log('[ClipIt] Token removed from extension storage');
    }
  } catch (error) {
    stopSyncingAfterReload(error);
  }
}

function syncLanguage() {
  try {
    if (!extensionStorageAvailable()) return;
    const storedLanguage = localStorage.getItem('deadbird_language');
    const language = ['ko', 'uk'].includes(storedLanguage) ? storedLanguage : 'ko';

    if (language !== lastLanguage) {
      lastLanguage = language;
      chrome.storage.local.set({ language });
      console.log('[ClipIt] Language synced to extension storage', language);
    }
  } catch (error) {
    stopSyncingAfterReload(error);
  }
}

// Sync immediately on page load
syncToken();
syncLanguage();

// Tell the web app as soon as the extension has persisted a tracked video.
// This crosses Chrome's extension/page boundary without exposing the user's
// token, and lets the app refresh its cached Watch History immediately.
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === 'VIDEO_TRACKED') {
    window.dispatchEvent(new CustomEvent('clipit:video-tracked', {
      detail: { videoId: message.videoId, lang: message.lang },
    }));
    return;
  }
  if (message?.type === 'CAPTION_RESYNC_PROGRESS') {
    window.dispatchEvent(new CustomEvent(`clipit:caption-resync-${message.state}`, {
      detail: message,
    }));
  }
});

// The Home word inventory can ask the extension to re-fetch historic YouTube
// captions directly from the user's browser.  The page never receives the
// extension token; this bridge only forwards the selected video IDs.
window.addEventListener('clipit:resync-captions', (event) => {
  const detail = event instanceof CustomEvent ? event.detail : null;
  const videoIds = Array.isArray(detail?.videoIds) ? detail.videoIds : [];
  if (!videoIds.length) return;
  try {
    chrome.runtime.sendMessage({
      type: 'RESYNC_WATCHED_CAPTIONS',
      videoIds,
      lang: detail?.lang,
    }).then((response) => {
      if (!response?.success) {
        window.dispatchEvent(new CustomEvent('clipit:caption-resync-failed'));
      }
    }).catch((error) => {
      stopSyncingAfterReload(error);
      window.dispatchEvent(new CustomEvent('clipit:caption-resync-failed'));
    });
  } catch (error) {
    stopSyncingAfterReload(error);
    window.dispatchEvent(new CustomEvent('clipit:caption-resync-failed'));
  }
});

// Poll every second to catch same-window login/logout
syncInterval = setInterval(() => {
  syncToken();
  syncLanguage();
}, 1000);
