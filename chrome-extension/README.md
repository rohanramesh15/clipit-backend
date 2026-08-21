# Deadbird Chrome Extension

A Chrome extension that automatically tracks YouTube and Netflix videos you watch for language learning, syncing them with your Deadbird account.

## Features

- **Automatic Video Tracking**: Automatically detects when you watch Korean or Ukrainian videos on YouTube or Netflix
- **User Authentication**: Seamlessly syncs with your Deadbird web app account
- **Netflix Support**: Captures subtitles, screenshots, and audio from Netflix content
- **Vocabulary Extraction**: Shows the most common words from each video
- **Multi-language Support**: Supports Korean (KO) and Ukrainian (UK)

## Setup

### 1. Load the Extension

1. Open Chrome and navigate to `chrome://extensions/`
2. Enable "Developer mode" (toggle in top right)
3. Click "Load unpacked"
4. Select the `chrome-extension` directory

### 2. Sign In to Deadbird

Before using the extension, you must sign in to your Deadbird account:

1. Open the ClipIt web app: https://www.joinclipit.com
2. Sign in or create an account
3. Keep the tab open in the background (the extension will automatically sync your authentication)

**Important**: The extension requires you to be signed in to track videos. If you see a "Sign in required" message in the extension popup, open the web app and log in.

## How It Works

### Authentication Flow

1. You sign in to the Deadbird web app
2. The web app stores a JWT token in localStorage
3. The extension's `token-bridge.js` content script automatically syncs this token to Chrome storage
4. All API requests from the extension include this token for authentication
5. Videos are tracked under your user account

### Video Tracking

**YouTube:**
- Automatically detects when you navigate to a YouTube video
- Extracts video ID and title
- Sends to backend for subtitle analysis and vocabulary extraction
- Only tracks videos with Korean or Ukrainian subtitles (with English)

**Netflix:**
- Requires you to enable audio capture (click extension icon, then "Enable Audio")
- Captures subtitles, screenshots, and audio clips at key moments
- Tracks episode information (season, episode number, title)

### Popup Interface

The extension popup shows:

- **List View**: All your tracked videos (filtered by language: KO/UK)
- **Detail View**: Vocabulary words found in each video with:
  - Target language word and frequency rank
  - English translation
  - Example sentence with translation
- **Status Indicators**:
  - Green dot: Connected and signed in
  - Red dot: Not signed in or offline
  - Video count badge

## Error States

The extension gracefully handles various error scenarios:

### Not Signed In
- **Message**: "Sign in required"
- **Action**: Click "Open Deadbird App" button and log in
- **Cause**: No authentication token found

### Offline
- **Message**: "Cannot connect to Deadbird"
- **Action**: Check internet connection and try again
- **Cause**: Network request failed or backend unreachable

### No Videos Yet
- **Message**: "No videos tracked yet"
- **Action**: Watch Korean or Ukrainian videos on YouTube or Netflix
- **Cause**: You haven't watched any videos in the selected language

### Couldn't Load Words
- **Message**: "Couldn't load words"
- **Action**: Try again or check if video has subtitles
- **Cause**: Subtitle extraction or vocabulary pipeline failed

### No Words Found
- **Message**: "No common [language] words found"
- **Cause**: Video didn't contain words from the frequency list

## Configuration

### URLs

The extension is configured to work with:

- **Backend API**: https://project-deadbird-backend.fly.dev/api
- **Frontend App**: https://www.joinclipit.com
- **Development**: Also supports localhost URLs for development

### Permissions

The extension requires:

- `storage`: Store authentication tokens and cached vocabulary
- `scripting`: Inject content scripts into YouTube and Netflix
- `tabCapture`: Capture audio from Netflix tabs
- `offscreen`: Record audio in background
- `activeTab`: Detect current video being watched
- `<all_urls>`: Access YouTube, Netflix, and Deadbird frontend

## Privacy & Data

- Videos are tracked per user account
- Authentication tokens are stored locally in Chrome storage
- Video data (titles, IDs, timestamps) is sent to the Deadbird backend
- Netflix: Screenshots and audio clips are captured only for keyword moments
- All data is private to your account

## Troubleshooting

### Extension says "Not signed in" even though I'm logged in

1. Make sure you have the Deadbird web app open and are logged in
2. Refresh the web app page to trigger token sync
3. Wait a few seconds for the token to sync to the extension
4. Click the extension icon again

### Videos aren't being tracked

1. Check that you're signed in (open extension popup)
2. Verify the video has Korean or Ukrainian subtitles
3. For Netflix: Click extension icon and enable audio capture
4. Check browser console for any error messages

### "Couldn't load words" error

1. Verify the video has subtitles in the target language
2. Check that the Deadbird backend is accessible
3. Try refreshing the page and watching the video again

### Netflix audio capture not working

1. Click the extension icon while on a Netflix watch page
2. Click "Enable Audio" button
3. You should see a microphone badge in the extension header
4. If it still doesn't work, refresh the Netflix page and try again

## Development

For local development:

1. Update URLs in `popup.js`, `background.js`, and `manifest.json` to use localhost
2. Run the Deadbird backend at `http://localhost:8000`
3. Run the Deadbird frontend at `http://localhost:5176`
4. Reload the extension in `chrome://extensions/`

## Support

For issues or feature requests, please contact the Deadbird team or open an issue on GitHub.
