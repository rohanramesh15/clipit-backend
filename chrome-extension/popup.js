const API = 'https://project-deadbird-backend.fly.dev/api';
const APP_URL = 'https://clipit-sable.vercel.app';
const root = document.getElementById('root');

// ─── State ────────────────────────────────────────────
let state = {
  view: 'loading',   // loading | offline | not-logged-in | empty | list | detail
  videos: [],
  selected: null,    // { video_id, title }
  words: null,       // null | 'loading' | 'no-words' | 'error' | []
  isNetflixTab: false,
  isYouTubeTab: false,
  audioEnabled: false,
  hideSubtitles: false, // hide subtitles while still capturing them
  lang: 'ko',        // 'ko' | 'uk'
  deleteConfirm: null, // { video_id, title } | null
  isDeleting: false,
  theme: 'dark',     // 'dark' | 'light'
};

// Supported learning languages and their display metadata.
const SUPPORTED_LANGUAGES = ['ko', 'uk'];
const LANG_NAMES = { ko: 'Korean', uk: 'Ukrainian' };
const LANG_BADGES = { ko: 'KO', uk: 'UK' };
const normalizeLang = (l) => (SUPPORTED_LANGUAGES.includes(l) ? l : 'ko');

// ─── Boot ─────────────────────────────────────────────
(async function init() {
  // Load persisted preferences
  const stored = await chrome.storage.local.get(['language', 'theme', 'hideSubtitles']);
  state.lang = normalizeLang(stored.language);
  state.theme = stored.theme === 'light' ? 'light' : 'dark';
  state.hideSubtitles = stored.hideSubtitles === true;

  // Apply theme to body
  document.body.classList.toggle('light', state.theme === 'light');

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;

    let shouldRefetch = false;
    if (changes.theme) {
      state.theme = changes.theme.newValue === 'light' ? 'light' : 'dark';
      document.body.classList.toggle('light', state.theme === 'light');
    }
    if (changes.language) {
      state.lang = normalizeLang(changes.language.newValue);
      state.selected = null;
      state.words = null;
      shouldRefetch = true;
    }

    if (shouldRefetch) {
      state.view = 'loading';
      render();
      fetchVideos();
    } else if (changes.theme) {
      render();
    }
  });

  await fetchVideos();

  // Refresh every 5s so newly tracked videos appear without closing the popup
  setInterval(() => {
    if (['list', 'empty', 'not-logged-in'].includes(state.view)) fetchVideos();
  }, 5000);
})();

async function fetchVideos() {
  try {
    // Check if we're on a Netflix or YouTube tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    state.isNetflixTab = tab?.url?.includes('netflix.com/watch') || false;
    state.isYouTubeTab = tab?.url?.includes('youtube.com/watch') || false;

    // Check if audio is enabled for this tab
    if (state.isNetflixTab && tab?.id) {
      const result = await chrome.runtime.sendMessage({ type: 'CHECK_AUDIO_ENABLED', tabId: tab.id });
      state.audioEnabled = result?.enabled || false;
    }

    const { deadbird_token: token } = await chrome.storage.local.get('deadbird_token');
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    const res = await fetch(`${API}/videos/history/filtered?lang=${state.lang}`, {
      signal: AbortSignal.timeout(3000),
      headers,
    });
    if (res.status === 401 || res.status === 403) {
      state.view = 'not-logged-in';
      render();
      return;
    }
    if (!res.ok) throw new Error();
    const data = await res.json();
    state.videos = data.videos || [];
    state.view = state.videos.length ? 'list' : 'empty';
  } catch {
    state.view = 'offline';
  }
  render();
}

// ─── Render ───────────────────────────────────────────
function render() {
  const { view } = state;
  if      (view === 'loading')      root.innerHTML = tmplLoading();
  else if (view === 'offline')      root.innerHTML = tmplOffline();
  else if (view === 'not-logged-in') root.innerHTML = tmplNotLoggedIn();
  else if (view === 'empty')        root.innerHTML = tmplEmpty();
  else if (view === 'list')         root.innerHTML = tmplList();
  else if (view === 'detail')       root.innerHTML = tmplDetail();
  bindEvents();
}

function bindEvents() {
  root.querySelectorAll('[data-action]').forEach(el => {
    el.addEventListener('click', handleAction);
  });
  // Prevent clicks inside dialog from propagating to overlay
  root.querySelectorAll('.dialog').forEach(el => {
    el.addEventListener('click', e => e.stopPropagation());
  });
}

async function handleAction(e) {
  const el = e.currentTarget;
  const action = el.dataset.action;

  if (action === 'open-app') {
    chrome.tabs.create({ url: APP_URL });
  }
  if (action === 'back') {
    state.view = 'list';
    state.selected = null;
    state.words = null;
    render();
  }
  if (action === 'enable-audio') {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id) {
      await chrome.runtime.sendMessage({ type: 'ENABLE_AUDIO_CAPTURE', tabId: tab.id });
      state.audioEnabled = true;
      render();
    }
  }
  if (action === 'toggle-hide-subtitles') {
    state.hideSubtitles = !state.hideSubtitles;
    chrome.storage.local.set({ hideSubtitles: state.hideSubtitles });
    // Send to current Netflix or YouTube tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab?.id && (state.isNetflixTab || state.isYouTubeTab)) {
      chrome.tabs.sendMessage(tab.id, { type: 'SET_HIDE_SUBTITLES', hide: state.hideSubtitles });
    }
    render();
  }
  if (action === 'show-delete-confirm') {
    const { id, title } = el.dataset;
    state.deleteConfirm = { video_id: id, title };
    render();
  }
  if (action === 'cancel-delete') {
    state.deleteConfirm = null;
    render();
  }
  if (action === 'delete-video-only') {
    await deleteVideo(false);
  }
  if (action === 'delete-video-and-flashcards') {
    await deleteVideo(true);
  }
}

async function deleteVideo(deleteFlashcards = false) {
  if (!state.deleteConfirm || state.isDeleting) return;

  state.isDeleting = true;
  render();

  try {
    const { deadbird_token: token } = await chrome.storage.local.get('deadbird_token');
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    const params = new URLSearchParams();
    if (deleteFlashcards) {
      params.set('delete_flashcards', 'true');
      params.set('lang', state.lang);
    }
    const url = `${API}/videos/${encodeURIComponent(state.deleteConfirm.video_id)}${params.toString() ? '?' + params.toString() : ''}`;
    const res = await fetch(url, {
      method: 'DELETE',
      headers,
    });
    if (res.ok) {
      state.videos = state.videos.filter(v => v.video_id !== state.deleteConfirm.video_id);
      if (state.videos.length === 0) {
        state.view = 'empty';
      }
    }
  } catch (error) {
    console.error('Failed to delete video:', error);
  } finally {
    state.deleteConfirm = null;
    state.isDeleting = false;
    render();
  }
}

// ─── Load words — checks cache first ─────────────────
async function loadWords(videoId, title) {
  const lang = state.lang;
  state.selected = { video_id: videoId, title };
  state.view = 'detail';

  // Check cache first for instant display
  const cacheKey = `vocab_${lang}_${videoId}`;
  const cached = await chrome.storage.local.get(cacheKey);
  const entry = cached[cacheKey];

  if (entry && !entry.loading && !entry.error) {
    state.words = entry.words && entry.words.length ? entry.words : 'no-words';
    render();
    return;
  }

  if (entry && entry.loading) {
    // Pipeline still running in background — show spinner and poll
    state.words = 'loading';
    render();
    pollForResult(videoId, lang);
    return;
  }

  // No cache yet — trigger pipeline and show spinner
  state.words = 'loading';
  render();
  const isNetflix = videoId.startsWith('netflix_');
  chrome.runtime.sendMessage(
    { type: isNetflix ? 'TRACK_NETFLIX' : 'TRACK_VIDEO', videoId, title, lang },
    () => pollForResult(videoId, lang)
  );
}

// Poll chrome.storage until pipeline result is ready
function pollForResult(videoId, lang, attempts = 0) {
  if (attempts > 40) { // 20s timeout
    state.words = 'error';
    render();
    return;
  }
  setTimeout(async () => {
    const cacheKey = `vocab_${lang}_${videoId}`;
    const cached = await chrome.storage.local.get(cacheKey);
    const entry = cached[cacheKey];
    if (entry && !entry.loading) {
      if (entry.error) {
        state.words = 'error';
      } else {
        state.words = entry.words && entry.words.length ? entry.words : 'no-words';
      }
      render();
    } else {
      pollForResult(videoId, lang, attempts + 1);
    }
  }, 500);
}

// ─── Templates ────────────────────────────────────────
function tmplLoading() {
  return `
    ${header({ dot: null, right: '' })}
    <div class="body">
      <div class="center-state">
        <div class="spinner"></div>
        <p class="sub">Connecting...</p>
      </div>
    </div>
  `;
}

function tmplOffline() {
  return `
    ${header({ dot: 'red', right: '<span class="count-badge">Offline</span>' })}
    <div class="body">
      <div class="center-state">
        <div class="icon">⚡</div>
        <p class="title">Cannot connect to ClipIt</p>
        <p class="sub">Please check your internet connection or try again later</p>
      </div>
    </div>
  `;
}

function tmplNotLoggedIn() {
  return `
    ${header({ dot: 'red', right: '<span class="count-badge">Not signed in</span>' })}
    <div class="body">
      <div class="center-state">
        <div class="icon">🔒</div>
        <p class="title">Sign in required</p>
        <p class="sub">Open the app and log in — the extension will pick up your session automatically.</p>
      </div>
    </div>
    ${footer()}
  `;
}

function tmplEmpty() {
  const langName = LANG_NAMES[state.lang] || 'Korean';
  return `
    ${header({ dot: 'green', right: '<span class="count-badge">0 videos</span>' })}
    <div class="body">
      <div class="center-state">
        <div class="icon">📺</div>
        <p class="title">No videos tracked yet</p>
        <p class="sub">Watch any ${langName} video on YouTube or Netflix with subtitles — it'll appear here automatically</p>
      </div>
    </div>
    ${footer()}
  `;
}

function tmplList() {
  const { videos } = state;
  const cards = videos.map(v => {
    const isNetflix = v.video_id.startsWith('netflix_');
    const thumbUrl = isNetflix
      ? '' // Netflix doesn't have public thumbnails
      : `https://img.youtube.com/vi/${v.video_id}/mqdefault.jpg`;
    const platformBadge = isNetflix
      ? '<span class="platform-badge netflix">N</span>'
      : '<span class="platform-badge youtube">▶</span>';
    // Build episode info string for Netflix shows
    const episodeInfo = isNetflix && (v.season || v.episode)
      ? (v.season && v.episode
          ? `S${v.season}:E${v.episode}`
          : v.season
            ? `Season ${v.season}`
            : `Episode ${v.episode}`)
      : '';
    return `
      <div class="video-card">
        ${isNetflix
          ? `<div class="video-thumb netflix-thumb">${platformBadge}</div>`
          : `<img class="video-thumb"
              src="${thumbUrl}"
              alt=""
              onerror="this.style.background='#2A242C';this.style.border='1px solid rgba(247,241,234,0.09)'"
            >${platformBadge}`
        }
        <div class="video-meta">
          <div class="video-title-text">${esc(v.title)}</div>
          ${episodeInfo ? `<div class="video-episode-info">${episodeInfo}</div>` : ''}
        </div>
        <button class="delete-btn"
          data-action="show-delete-confirm"
          data-id="${v.video_id}"
          data-title="${esc(v.title)}"
          title="Remove from history">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
          </svg>
        </button>
      </div>
    `;
  }).join('');

  const deleteDialog = state.deleteConfirm ? `
    <div class="dialog-overlay" data-action="cancel-delete">
      <div class="dialog">
        <div class="dialog-header">
          <div class="dialog-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>
            </svg>
          </div>
          <button class="dialog-close" data-action="cancel-delete" ${state.isDeleting ? 'disabled' : ''}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M18 6L6 18"/><path d="M6 6l12 12"/>
            </svg>
          </button>
        </div>
        <div class="dialog-title">Remove from History?</div>
        <div class="dialog-video-title">${esc(state.deleteConfirm.title)}</div>
        <div class="dialog-text">
          Choose whether to also delete flashcards for words found in this video.
        </div>
        <div class="dialog-actions-vertical">
          <button class="dialog-btn-orange" data-action="delete-video-only" ${state.isDeleting ? 'disabled' : ''}>
            ${state.isDeleting ? '<span class="btn-spinner"></span> Removing...' : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="btn-icon"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg> Remove Video Only'}
          </button>
          <button class="dialog-btn-red" data-action="delete-video-and-flashcards" ${state.isDeleting ? 'disabled' : ''}>
            ${state.isDeleting ? '<span class="btn-spinner"></span> Removing...' : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="btn-icon"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg> Remove Video & Flashcards'}
          </button>
          <button class="dialog-btn-cancel" data-action="cancel-delete" ${state.isDeleting ? 'disabled' : ''}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  ` : '';

  return `
    ${header({ dot: 'green', right: `<span class="count-badge">${videos.length} tracked</span>` })}
    <div class="body">
      <div class="video-list">${cards}</div>
    </div>
    ${footer()}
    ${deleteDialog}
  `;
}

function tmplDetail() {
  const { selected, words } = state;
  let body;

  if (words === 'loading') {
    body = `
      <div class="center-state">
        <div class="spinner"></div>
        <p class="sub">Fetching subtitles & words...</p>
      </div>
    `;
  } else if (words === 'no-words') {
    const langName = LANG_NAMES[state.lang] || 'Korean';
    body = `
      <div class="center-state">
        <div class="icon">🈚</div>
        <p class="title">No common ${langName} words found</p>
        <p class="sub">No words from this video matched the ${langName} frequency list</p>
      </div>
    `;
  } else if (words === 'error') {
    body = `
      <div class="center-state">
        <div class="icon">⚠️</div>
        <p class="title">Couldn't load words</p>
        <p class="sub">There was an error loading the vocabulary. Please try again.</p>
      </div>
    `;
  } else if (Array.isArray(words)) {
    const cards = words.map(w => {
      const eng = w.english && w.english !== 'definition not available'
        ? w.english
        : null;
      const hasSentence = w.sentence && w.sentence_translation;
      return `
        <div class="word-card">
          <div class="word-row">
            <span class="word-korean">${esc(w.target_word)}</span>
            ${w.rank ? `<span class="word-rank">#${w.rank}</span>` : ''}
          </div>
          ${eng ? `<div class="word-english">${esc(eng)}</div>` : ''}
          ${hasSentence ? `
            <div class="word-sentence">
              <div class="ko">${esc(w.sentence)}</div>
              <div class="en">${esc(w.sentence_translation)}</div>
            </div>
          ` : ''}
        </div>
      `;
    }).join('');

    body = `
      <div class="words-header">${words.length} words found</div>
      <div class="words-list">${cards}</div>
    `;
  } else {
    body = `<div class="center-state"><div class="spinner"></div></div>`;
  }

  return `
    <div class="header">
      <button class="back-btn" data-action="back">← Back</button>
      <span class="detail-title">${esc(selected.title)}</span>
    </div>
    <div class="body">${body}</div>
    ${footer()}
  `;
}

// ─── Helpers ──────────────────────────────────────────
function header({ dot, right }) {
  const dotHtml = dot ? `<span class="status-dot ${dot}"></span>` : '';
  const audioBtn = state.isNetflixTab ? (
    state.audioEnabled
      ? '<span class="audio-badge enabled" title="Audio capture enabled">🎤</span>'
      : '<button class="audio-btn" data-action="enable-audio" title="Enable audio capture">Enable Audio</button>'
  ) : '';
  const hideSubsBtn = (state.isNetflixTab || state.isYouTubeTab) ? `
    <button class="audio-btn ${state.hideSubtitles ? 'active' : ''}" data-action="toggle-hide-subtitles" title="${state.hideSubtitles ? 'Show subtitles' : 'Hide subtitles (still captured)'}">
      ${state.hideSubtitles ? 'Show Subtitles' : 'Hide Subtitles'}
    </button>
  ` : '';
  const langBadge = LANG_BADGES[state.lang] || 'KO';
  return `
    <div class="header">
      <div class="header-brand">
        <img class="header-logo" src="logo.png" alt="ClipIt" />
        <span class="header-title"><span class="lip">lip</span><span class="it">It</span></span>
      </div>
      <div class="header-right">
        ${hideSubsBtn}
        ${audioBtn}
        <span class="lang-badge" title="Synced from ClipIt app">${langBadge}</span>
        ${right}
        ${dotHtml}
      </div>
    </div>
  `;
}

function footer() {
  return `
    <div class="footer">
      <button class="footer-btn" data-action="open-app">Open App →</button>
    </div>
  `;
}

function esc(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
