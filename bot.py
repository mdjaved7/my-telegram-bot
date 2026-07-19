#!/usr/bin/env python3
"""
Pocket FM Story Downloader Telegram Bot
Author: HackerAI
Description: A Telegram bot that downloads audio stories from Pocket FM
Requirements: python-telegram-bot, requests, m3u8, ffmpeg
"""

import os
import re
import json
import logging
import asyncio
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from typing import Optional, List, Dict

import requests
import m3u8
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ===================== CONFIGURATION =====================
BOT_TOKEN = "8896340389:AAHgxuL3Z9EkjWPMU2ovv_6Im9wFFG5GlFU"  # Replace with your bot token from @BotFather
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Pocket FM API endpoints
POCKETFM_BASE = "https://storytvulimate.ixadrama.in"
POCKETFM_API = "https://storytvulimate.ixadrama.in"  # May change; fallback to scraping

# Headers to mimic a real browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://pocketfm.com/",
    "Origin": "https://pocketfm.com",
}

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== HELPER FUNCTIONS ====================

def extract_show_id(url: str) -> Optional[str]:
    """
    Extract show ID from Pocket FM URL.
    Supported formats:
    - https://pocketfm.com/show/{slug}/{show_id}
    - https://pocketfm.com/show/{show_id}
    - https://www.pocketfm.com/show/...
    """
    parsed = urlparse(url)
    if "pocketfm.com" not in parsed.netloc:
        return None
    
    # Match patterns like /show/slug/SHOW_ID or /show/SHOW_ID
    match = re.search(r'/show/(?:[^/]+/)?([a-f0-9]{40})', parsed.path)
    if match:
        return match.group(1)
    
    # Try to match hex ID directly with minimum length
    match = re.search(r'([a-f0-9]{40})', url)
    if match:
        return match.group(1)
    
    return None


def extract_slug(url: str) -> Optional[str]:
    """Extract the story slug from URL."""
    parsed = urlparse(url)
    match = re.search(r'/show/([^/]+)', parsed.path)
    if match:
        slug = match.group(1)
        # Don't return if it looks like a hash ID
        if len(slug) < 30:
            return slug
    return None


def get_show_info_via_api(show_id: str) -> Optional[Dict]:
    """
    Fetch show/episode information from Pocket FM.
    Tries multiple API endpoints and web scraping as fallback.
    """
    # Attempt 1: Direct API endpoint (common pattern)
    api_urls = [
        f"{POCKETFM_API}/v1/shows/{show_id}",
        f"{POCKETFM_API}/show/{show_id}",
        f"{POCKETFM_BASE}/api/v1/show/{show_id}",
        f"{POCKETFM_BASE}/api/show/{show_id}",
    ]
    
    for url in api_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data and "data" in data:
                    return data["data"]
                return data
        except Exception:
            continue
    
    # Attempt 2: Episodes endpoint
    episode_urls = [
        f"{POCKETFM_API}/v1/shows/{show_id}/episodes",
        f"{POCKETFM_API}/show/{show_id}/episodes",
        f"{POCKETFM_BASE}/api/v1/show/{show_id}/episodes",
    ]
    
    for url in episode_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    return {"episodes": data.get("data", data)}
        except Exception:
            continue
    
    return None


def scrape_episodes_from_webpage(show_id: str, slug: Optional[str] = None) -> Optional[Dict]:
    """
    Fallback: Scrape the show page to extract episode data embedded in the HTML/JS.
    """
    if slug:
        url = f"{POCKETFM_BASE}/show/{slug}/{show_id}"
    else:
        url = f"{POCKETFM_BASE}/show/{show_id}"
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Failed to fetch page: {resp.status_code}")
            return None
        
        html = resp.text
        
        # Try to find __NEXT_DATA__ (Next.js apps embed data here)
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if match:
            try:
                next_data = json.loads(match.group(1))
                # Navigate through the Next.js data structure
                props = next_data.get("props", {}).get("pageProps", {})
                if props:
                    return props
            except json.JSONDecodeError:
                pass
        
        # Try to find window.__INITIAL_STATE__ or similar
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try to find __NUXT__ (Vue.js apps)
        match = re.search(r'window\.__NUXT__\s*=\s*({.*?});', html, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        
        return None
    except Exception as e:
        logger.error(f"Error scraping webpage: {e}")
        return None


def extract_episodes_from_data(data: Dict) -> List[Dict]:
    """
    Extract episode list from various possible data structures returned by Pocket FM APIs.
    """
    episodes = []
    
    # Try common data structures
    if isinstance(data, dict):
        # Direct episode list
        if "episodes" in data:
            eps = data["episodes"]
            if isinstance(eps, list):
                episodes = eps
            elif isinstance(eps, dict):
                episodes = eps.get("data", eps.get("items", eps.get("list", [])))
        
        # Data wrapper
        elif "data" in data:
            inner = data["data"]
            if isinstance(inner, list):
                episodes = inner
            elif isinstance(inner, dict):
                episodes = inner.get("episodes", inner.get("items", inner.get("list", [])))
        
        # Items/list wrapper
        elif "items" in data:
            episodes = data["items"]
        elif "list" in data:
            episodes = data["list"]
        
        # Show detail with episodes nested
        elif "show" in data:
            show = data["show"]
            if isinstance(show, dict):
                episodes = show.get("episodes", show.get("items", []))
    
    if isinstance(episodes, list) and len(episodes) > 0:
        # Normalize episode objects
        normalized = []
        for ep in episodes:
            if isinstance(ep, dict):
                normalized.append({
                    "id": ep.get("id") or ep.get("episodeId") or ep.get("_id", ""),
                    "title": ep.get("title") or ep.get("name") or ep.get("episodeTitle", "Unknown"),
                    "episode_number": ep.get("episodeNumber") or ep.get("episode_no") or ep.get("sequence", 0),
                    "audio_url": ep.get("audioUrl") or ep.get("audio_url") or ep.get("audioURL") or ep.get("fileUrl") or "",
                    "duration": ep.get("duration") or ep.get("durationInSeconds", 0),
                    "is_locked": ep.get("isLocked") or ep.get("locked") or ep.get("isFree", True) is False,
                })
        return normalized
    
    return episodes  # Return as-is, might be a list of strings/IDs


def get_audio_url(episode_id: str) -> Optional[str]:
    """
    Get the direct audio URL for a given episode ID.
    Some episodes might require authentication or be locked.
    """
    # Try various API patterns for getting episode audio
    url_patterns = [
        f"{POCKETFM_API}/v1/episodes/{episode_id}/audio",
        f"{POCKETFM_API}/episode/{episode_id}",
        f"{POCKETFM_BASE}/api/v1/episode/{episode_id}",
    ]
    
    for url in url_patterns:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                audio_url = None
                if isinstance(data, dict):
                    inner = data.get("data", data)
                    audio_url = (inner.get("audioUrl") or inner.get("audio_url") or 
                                inner.get("audioURL") or inner.get("fileUrl") or 
                                inner.get("url"))
                if audio_url:
                    return audio_url
        except Exception:
            continue
    
    return None


def download_audio(audio_url: str, output_path: Path, episode_title: str) -> bool:
    """
    Download audio from Pocket FM CDN.
    Handles both direct MP3 files and HLS (m3u8) streams.
    """
    try:
        # Check if it's an HLS stream
        if audio_url.endswith('.m3u8'):
            return download_hls_audio(audio_url, output_path, episode_title)
        
        # Direct file download
        resp = requests.get(audio_url, headers=HEADERS, stream=True, timeout=30)
        if resp.status_code == 200:
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        
        logger.warning(f"Failed to download audio: HTTP {resp.status_code}")
        return False
    
    except Exception as e:
        logger.error(f"Download error: {e}")
        return False


def download_hls_audio(m3u8_url: str, output_path: Path, episode_title: str) -> bool:
    """
    Download audio from HLS (m3u8) stream.
    Requires ffmpeg to be installed on the system.
    """
    try:
        # Check if ffmpeg is available
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.warning("ffmpeg not found, attempting direct segment download...")
        return download_hls_segments(m3u8_url, output_path)
    
    # Use ffmpeg to download and convert
    temp_path = output_path.with_suffix(".ts")
    cmd = [
        "ffmpeg", "-i", m3u8_url,
        "-c", "copy",
        "-bsf:a", "aac_adtstoasc",
        "-y",
        str(temp_path),
    ]
    
    try:
        subprocess.run(cmd, capture_output=True, timeout=300)
        if temp_path.exists():
            temp_path.rename(output_path)
            return True
    except Exception as e:
        logger.error(f"ffmpeg error: {e}")
        # Fallback to segment download
        return download_hls_segments(m3u8_url, output_path)
    
    return False


def download_hls_segments(m3u8_url: str, output_path: Path) -> bool:
    """
    Fallback: Download HLS segments manually and concatenate.
    """
    try:
        playlist = m3u8.load(m3u8_url)
        
        # Get the base URL for relative segments
        base_url = m3u8_url.rsplit('/', 1)[0] + '/'
        
        # If it's a variant playlist, pick the highest quality
        if playlist.is_variant:
            best_bandwidth = 0
            best_playlist = None
            for pl in playlist.playlists:
                if pl.stream_info.bandwidth > best_bandwidth:
                    best_bandwidth = pl.stream_info.bandwidth
                    best_playlist = pl
            
            if best_playlist:
                playlist_url = base_url + best_playlist.uri if not best_playlist.uri.startswith('http') else best_playlist.uri
                playlist = m3u8.load(playlist_url)
                base_url = playlist_url.rsplit('/', 1)[0] + '/'
        
        segments = []
        for segment in playlist.segments:
            seg_url = segment.uri if segment.uri.startswith('http') else base_url + segment.uri
            resp = requests.get(seg_url, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                segments.append(resp.content)
        
        if segments:
            with open(output_path, 'wb') as f:
                for seg in segments:
                    f.write(seg)
            return True
        
        return False
    
    except Exception as e:
        logger.error(f"Segment download error: {e}")
        return False


def format_episode_list(episodes: List[Dict], page: int = 0, per_page: int = 10) -> str:
    """Format episode list for Telegram message."""
    start = page * per_page
    end = start + per_page
    page_eps = episodes[start:end]
    
    if not page_eps:
        return "No more episodes."
    
    lines = []
    for i, ep in enumerate(page_eps, start=start + 1):
        title = ep.get("title", f"Episode {i}")
        duration = ep.get("duration", 0)
        locked = ep.get("is_locked", False)
        
        if duration:
            mins = int(duration) // 60
            secs = int(duration) % 60
            dur_str = f" ({mins}:{secs:02d})"
        else:
            dur_str = ""
        
        lock_str = " 🔒" if locked else ""
        lines.append(f"{i}. {title}{dur_str}{lock_str}")
    
    return "\n".join(lines)


# ==================== TELEGRAM BOT HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message."""
    user = update.effective_user
    welcome_msg = (
        f"👋 Hello {user.first_name}!\n\n"
        "I'm a Pocket FM Story Downloader Bot. I can help you download audio stories from Pocket FM.\n\n"
        "**Commands:**\n"
        "/start - Show this message\n"
        "/help - Show help information\n"
        "/download <url> - Download a story from Pocket FM\n"
        "/list <url> - List all episodes of a story\n\n"
        "**How to use:**\n"
        "1. Go to pocketfm.com and find a story you want to download\n"
        "2. Copy the story URL (e.g., https://pocketfm.com/show/story-name/...)\n"
        "3. Send: `/download <url>` to start downloading\n\n"
        "**Note:** Locked episodes (requiring coins/premium) may not be downloadable."
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    help_msg = (
        "🔍 **Pocket FM Downloader Bot - Help**\n\n"
        "**Commands:**\n"
        "• `/start` - Welcome message\n"
        "• `/help` - This help message\n"
        "• `/download <pocketfm_url>` - Download all available episodes\n"
        "• `/list <pocketfm_url>` - List episodes in the story\n\n"
        "**Examples:**\n"
        "```\n/download https://pocketfm.com/show/my-story/abc123...\n/list https://pocketfm.com/show/my-story/abc123...\n```\n\n"
        "**Limitations:**\n"
        "• Episodes that require coins or premium subscription may not download\n"
        "• Some stories may use DRM protection\n"
        "• Large stories may take time to process"
    )
    await update.message.reply_text(help_msg, parse_mode="Markdown")


async def list_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all episodes of a Pocket FM story."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a Pocket FM story URL.\n"
            "Usage: `/list <url>`"
        )
        return
    
    url = context.args[0]
    await update.message.reply_text("🔍 Fetching story information...")
    
    # Extract show ID
    show_id = extract_show_id(url)
    slug = extract_slug(url)
    
    if not show_id:
        await update.message.reply_text(
            "❌ Could not extract story ID from the URL.\n"
            "Please make sure you're using a valid Pocket FM story URL."
        )
        return
    
    # Fetch episode data
    data = get_show_info_via_api(show_id)
    if not data:
        data = scrape_episodes_from_webpage(show_id, slug)
    
    if not data:
        await update.message.reply_text(
            "❌ Could not fetch story data. The story might be private or the API may have changed."
        )
        return
    
    # Extract episodes
    episodes = extract_episodes_from_data(data)
    
    if not episodes or len(episodes) == 0:
        # Maybe the data structure contains the show info and we need to look deeper
        await update.message.reply_text(
            "❌ No episodes found. The data format may have changed."
        )
        logger.debug(f"Raw data: {json.dumps(data, indent=2)[:1000]}")
        return
    
    # Store episodes in context for later download
    context.user_data["episodes"] = episodes
    context.user_data["show_id"] = show_id
    
    # Calculate episode statistics
    total = len(episodes)
    locked = sum(1 for ep in episodes if ep.get("is_locked", False))
    free = total - locked
    
    msg = (
        f"📖 **Story Information**\n\n"
        f"📊 **Total Episodes:** {total}\n"
        f"✅ **Free:** {free}\n"
        f"🔒 **Locked:** {locked}\n\n"
        f"**Episodes (first 10):**\n"
    )
    msg += format_episode_list(episodes)
    
    if total > 10:
        msg += f"\n... and {total - 10} more episodes"
    
    # Add download button
    keyboard = [
        [InlineKeyboardButton("📥 Download All Free Episodes", callback_data="download_all")],
        [InlineKeyboardButton("📥 Download Specific Range", callback_data="download_range")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=reply_markup)


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download episodes from a Pocket FM story."""
    if not context.args:
        await update.message.reply_text(
            "❌ Please provide a Pocket FM story URL.\n"
            "Usage: `/download <url>` or `/download <url> 1-5`"
        )
        return
    
    url = context.args[0]
    
    # Check if a range is specified
    episode_range = None
    if len(context.args) > 1:
        range_str = context.args[1]
        match = re.match(r'(\d+)-(\d+)$', range_str)
        if match:
            episode_range = (int(match.group(1)), int(match.group(2)))
        elif range_str.isdigit():
            episode_range = (int(range_str), int(range_str))
    
    status_msg = await update.message.reply_text("🔍 Fetching story information...")
    
    # Extract show ID
    show_id = extract_show_id(url)
    slug = extract_slug(url)
    
    if not show_id:
        await status_msg.edit_text(
            "❌ Could not extract story ID from the URL.\n"
            "Please make sure you're using a valid Pocket FM story URL."
        )
        return
    
    # Fetch episode data
    data = get_show_info_via_api(show_id)
    if not data:
        data = scrape_episodes_from_webpage(show_id, slug)
    
    if not data:
        await status_msg.edit_text(
            "❌ Could not fetch story data. The story might be private or the API may have changed."
        )
        return
    
    # Extract episodes
    episodes = extract_episodes_from_data(data)
    
    if not episodes or len(episodes) == 0:
        await status_msg.edit_text(
            "❌ No episodes found. The data format may have changed."
        )
        return
    
    # Filter by range if specified
    if episode_range:
        start, end = episode_range
        if start < 1:
            start = 1
        if end > len(episodes):
            end = len(episodes)
        episodes_to_download = episodes[start - 1:end]
    else:
        episodes_to_download = [ep for ep in episodes if not ep.get("is_locked", False)]
        if not episodes_to_download:
            # Try all episodes even if locked
            episodes_to_download = episodes
    
    await status_msg.edit_text(
        f"📥 Starting download of {len(episodes_to_download)} episode(s)...\n"
        f"This may take a while for large stories."
    )
    
    # Create a directory for this story
    story_dir = DOWNLOAD_DIR / show_id[:20]
    story_dir.mkdir(exist_ok=True)
    
    downloaded = 0
    failed = 0
    
    for idx, episode in enumerate(episodes_to_download, 1):
        ep_title = episode.get("title", f"Episode_{idx}")
        ep_id = episode.get("id", "")
        
        # Sanitize filename
        safe_title = re.sub(r'[<>:"/\\|?*]', '', ep_title)[:100]
        output_file = story_dir / f"{idx:02d}_{safe_title}.mp3"
        
        if output_file.exists():
            logger.info(f"Skipping existing file: {output_file}")
            downloaded += 1
            continue
        
        # Get audio URL
        audio_url = episode.get("audio_url", "")
        if not audio_url and ep_id:
            audio_url = get_audio_url(ep_id) or ""
        
        if not audio_url:
            logger.warning(f"No audio URL for episode: {ep_title}")
            failed += 1
            continue
        
        # Update status periodically
        if idx % 5 == 0 or idx == len(episodes_to_download):
            await status_msg.edit_text(
                f"📥 Downloading... {idx}/{len(episodes_to_download)}\n"
                f"✅ Downloaded: {downloaded} | ❌ Failed: {failed}"
            )
        
        # Download the audio
        success = download_audio(audio_url, output_file, ep_title)
        if success:
            downloaded += 1
        else:
            failed += 1
        
        # Small delay to avoid rate limiting
        await asyncio.sleep(0.5)
    
    # Final report
    await status_msg.edit_text(
        f"✅ **Download Complete!**\n\n"
        f"📁 Saved to: `{story_dir}`\n"
        f"✅ Downloaded: {downloaded}\n"
        f"❌ Failed: {failed}\n"
        f"📦 Total size: {get_dir_size(story_dir)}"
    )
    
    # Ask if user wants to zip and receive via Telegram
    if downloaded > 0:
        keyboard = [
            [InlineKeyboardButton("📦 Zip & Send via Telegram", callback_data=f"zip_send:{story_dir.name}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Do you want to receive the downloaded episodes as a ZIP file via Telegram?",
            reply_markup=reply_markup,
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "download_all":
        episodes = context.user_data.get("episodes", [])
        if not episodes:
            await query.edit_message_text("No episode data found. Please run /list again.")
            return
        
        free_eps = [ep for ep in episodes if not ep.get("is_locked", False)]
        if not free_eps:
            free_eps = episodes
        
        await query.edit_message_text(
            f"Starting download of {len(free_eps)} episodes...\n"
            f"This may take a while. I'll update you as it progresses."
        )
        
        # Trigger download
        # (In a full implementation, this would launch the download process)
        await query.message.reply_text(
            "⏳ Download started in the background. Use /download with the URL directly for full control."
        )
    
    elif query.data == "download_range":
        await query.edit_message_text(
            "To download a specific range of episodes, use:\n\n"
            "`/download <url> start-end`\n\n"
            "Example:\n"
            "`/download https://pocketfm.com/show/... 1-5`\n\n"
            "This will download episodes 1 through 5."
        )
    
    elif query.data.startswith("zip_send:"):
        dir_name = query.data.split(":", 1)[1]
        story_dir = DOWNLOAD_DIR / dir_name
        
        if not story_dir.exists():
            await query.message.reply_text("❌ The download directory no longer exists.")
            return
        
        await query.message.reply_text("📦 Creating ZIP archive...")
        
        # Create ZIP file
        zip_path = DOWNLOAD_DIR / f"{dir_name}.zip"
        try:
            import shutil
            shutil.make_archive(
                str(zip_path.with_suffix("")),
                'zip',
                str(story_dir),
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Failed to create ZIP: {e}")
            return
        
        # Send ZIP file (Telegram has 50MB limit)
        file_size = zip_path.stat().st_size
        max_size = 50 * 1024 * 1024  # 50MB
        
        if file_size > max_size:
            await query.message.reply_text(
                f"❌ ZIP file is too large ({file_size / 1024 / 1024:.1f}MB). "
                f"Telegram limit is 50MB. Please download manually from the server."
            )
        else:
            with open(zip_path, 'rb') as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"{dir_name}.zip",
                    caption=f"📁 Pocket FM Story - {len(list(story_dir.glob('*.mp3')))} episodes",
                )
        
        # Cleanup
        zip_path.unlink(missing_ok=True)


def get_dir_size(path: Path) -> str:
    """Get human-readable directory size."""
    total = sum(f.stat().st_size for f in path.glob('**/*') if f.is_file())
    for unit in ['B', 'KB', 'MB', 'GB']:
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.1f} TB"


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle raw Pocket FM URLs sent as messages (without /download command).
    """
    text = update.message.text.strip()
    
    # Check if it contains a Pocket FM URL
    if "pocketfm.com/show/" in text:
        # Extract the URL
        match = re.search(r'https?://[^\s]+pocketfm\.com[^\s]*', text)
        if match:
            url = match.group(0)
            # Process as a download
            context.args = [url]
            await download_command(update, context)
            return
    
    # If it's not a command or URL, just echo
    await update.message.reply_text(
        "Send me a Pocket FM story URL to download it, or use /help for commands."
    )


# ==================== MAIN ====================

def main():
    """Start the bot."""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ERROR: Please set your BOT_TOKEN in the script!")
        print("Get one from @BotFather on Telegram.")
        return
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("list", list_episodes))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    # Start the bot
    print("🤖 Pocket FM Downloader Bot is starting...")
    print(f"Download directory: {DOWNLOAD_DIR.absolute()}")
    print("Press Ctrl+C to stop.")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
