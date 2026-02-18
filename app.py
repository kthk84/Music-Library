from flask import Flask, render_template, request, jsonify, send_file, Response
import os
import json
import sys
import subprocess
import time
from pathlib import Path
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, APIC
from mutagen.id3 import ID3NoHeaderError
import requests
import hashlib
from typing import Dict, List, Optional
import base64
import threading

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max request size

# Global variable to store current folder path
current_folder = None

# AcoustID API for audio fingerprinting (free tier)
ACOUSTID_API_KEY = "8XaBELgH"  # Public demo key - replace with your own from acoustid.org
ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"

# MusicBrainz API (free, no key needed)
MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/"
MUSICBRAINZ_HEADERS = {
    'User-Agent': 'MP3Cleaner/1.0 (https://github.com/yourusername/mp3cleaner)'
}

# Last.fm API (free, requires key - get from https://www.last.fm/api/account/create)
LASTFM_API_KEY = "b25b959554ed76058ac220b7b2e0a026"  # Public demo key
LASTFM_API_URL = "http://ws.audioscrobbler.com/2.0/"

# Rate limiting
last_api_call = {'musicbrainz': 0, 'lastfm': 0, 'itunes': 0}

def clean_filename(filename: str) -> str:
    """Remove track number prefix from filename (e.g. '80. Song.mp3' -> 'Song.mp3')"""
    import re
    # Remove patterns like "80. ", "001. ", "01 ", etc.
    cleaned = re.sub(r'^\d+[\.\s]+', '', filename)
    return cleaned

def is_spam_metadata(text: str) -> bool:
    """Detect if metadata contains commercial spam"""
    if not text:
        return False
    
    spam_patterns = [
        'www.', 'http', '.com', '.ru', '.org',
        'download', 'скачать', 'купить', 'buy',
        'visit', 'free mp3', 'mp3download',
        'torrent', 'pirate', '@', 'telegram'
    ]
    
    text_lower = text.lower()
    return any(pattern in text_lower for pattern in spam_patterns)

def get_file_info(filepath: str) -> Dict:
    """Extract current MP3 tag information"""
    try:
        audio = MP3(filepath, ID3=ID3)
        
        # Get all common tags
        title = str(audio.get('TIT2', [''])[0]) if audio.get('TIT2') else ''
        artist = str(audio.get('TPE1', [''])[0]) if audio.get('TPE1') else ''
        album = str(audio.get('TALB', [''])[0]) if audio.get('TALB') else ''
        year = str(audio.get('TDRC', [''])[0]) if audio.get('TDRC') else ''
        genre = str(audio.get('TCON', [''])[0]) if audio.get('TCON') else ''
        
        # Additional metadata
        album_artist = str(audio.get('TPE2', [''])[0]) if audio.get('TPE2') else ''
        composer = str(audio.get('TCOM', [''])[0]) if audio.get('TCOM') else ''
        publisher = str(audio.get('TPUB', [''])[0]) if audio.get('TPUB') else ''
        track_number = str(audio.get('TRCK', [''])[0]) if audio.get('TRCK') else ''
        disc_number = str(audio.get('TPOS', [''])[0]) if audio.get('TPOS') else ''
        
        # Comments (often full of spam)
        comment = ''
        if audio.get('COMM'):
            try:
                comment = str(audio.get('COMM')[0].text[0]) if audio.get('COMM')[0].text else ''
            except:
                pass
        
        # Copyright
        copyright_text = str(audio.get('TCOP', [''])[0]) if audio.get('TCOP') else ''
        
        # Encoder/Software
        encoder = str(audio.get('TENC', [''])[0]) if audio.get('TENC') else ''
        
        # URL frames (often spam)
        url = str(audio.get('WXXX', [''])[0]) if audio.get('WXXX') else ''
        
        # File info
        duration = int(audio.info.length) if audio.info else 0
        bitrate = audio.info.bitrate // 1000 if audio.info else 0
        
        filename = os.path.basename(filepath)
        cleaned_filename = clean_filename(filename)
        
        # Extract album cover if present
        cover_data = None
        has_cover = False
        
        # Try to find APIC frame (album cover)
        # APIC frames can have various keys: APIC:, APIC:Cover, APIC:cover, etc.
        apic = None
        for key in audio.keys():
            if key.startswith('APIC'):
                apic = audio.get(key)
                break
        
        if apic and hasattr(apic, 'data') and apic.data:
            try:
                # Convert to base64 for JSON transfer
                cover_data = base64.b64encode(apic.data).decode('utf-8')
                has_cover = True
                print(f"Found cover in {filename}: {len(apic.data)} bytes -> {len(cover_data)} base64 chars")
            except Exception as e:
                print(f"Error encoding cover for {filename}: {e}")
        
        # Detect spam in metadata
        has_spam = (
            is_spam_metadata(title) or 
            is_spam_metadata(artist) or 
            is_spam_metadata(album) or
            is_spam_metadata(comment) or
            is_spam_metadata(publisher) or
            is_spam_metadata(copyright_text) or
            bool(url)
        )
        
        return {
            'filepath': filepath,
            'filename': filename,
            'cleaned_filename': cleaned_filename,
            'has_number_prefix': filename != cleaned_filename,
            'title': title,
            'artist': artist,
            'album': album,
            'year': year,
            'genre': genre,
            'album_artist': album_artist,
            'composer': composer,
            'publisher': publisher,
            'comment': comment,
            'copyright': copyright_text,
            'encoder': encoder,
            'url': url,
            'track_number': track_number,
            'disc_number': disc_number,
            'duration': duration,
            'bitrate': bitrate,
            'size': os.path.getsize(filepath),
            'has_spam': has_spam,
            'has_cover': has_cover,
            'cover': cover_data
        }
    except Exception as e:
        return {
            'filepath': filepath,
            'filename': os.path.basename(filepath),
            'error': str(e)
        }

def rate_limit(api_name: str, min_delay: float = 1.0):
    """Simple rate limiting to avoid API bans"""
    global last_api_call
    current_time = time.time()
    time_since_last = current_time - last_api_call.get(api_name, 0)
    
    if time_since_last < min_delay:
        time.sleep(min_delay - time_since_last)
    
    last_api_call[api_name] = time.time()

def search_itunes(title: str, artist: str) -> Optional[List[Dict]]:
    """Search iTunes API for track metadata (better for recent tracks)"""
    try:
        query = f"{artist} {title}".strip()
        if not query:
            return None
        
        rate_limit('itunes', 0.5)
        
        params = {
            'term': query,
            'media': 'music',
            'entity': 'song',
            'limit': 5
        }
        
        response = requests.get(
            'https://itunes.apple.com/search',
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            for track in data.get('results', []):
                # iTunes provides album artwork URLs
                cover_url = track.get('artworkUrl100', '')
                if cover_url:
                    # Get higher resolution version (600x600)
                    cover_url = cover_url.replace('100x100', '600x600')
                
                result = {
                    'title': track.get('trackName', ''),
                    'artist': track.get('artistName', ''),
                    'album': track.get('collectionName', ''),
                    'year': track.get('releaseDate', '')[:4] if track.get('releaseDate') else '',
                    'genre': track.get('primaryGenreName', ''),
                    'confidence': 0.85,
                    'source': 'iTunes',
                    'is_compilation': False,
                    'cover_url': cover_url
                }
                results.append(result)
            
            return results if results else None
        
        return None
    except Exception as e:
        print(f"iTunes error: {e}")
        return None

def search_lastfm(title: str, artist: str) -> Optional[List[Dict]]:
    """Search Last.fm API for track metadata (huge database)"""
    try:
        if not title:
            return None
        
        rate_limit('lastfm', 0.2)
        
        params = {
            'method': 'track.search',
            'track': title,
            'artist': artist,
            'api_key': LASTFM_API_KEY,
            'format': 'json',
            'limit': 5
        }
        
        response = requests.get(
            LASTFM_API_URL,
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            
            if data.get('results') and data['results'].get('trackmatches'):
                tracks = data['results']['trackmatches'].get('track', [])
                
                # Ensure tracks is a list
                if isinstance(tracks, dict):
                    tracks = [tracks]
                
                for track in tracks[:5]:
                    track_name = track.get('name', '')
                    artist_name = track.get('artist', '')
                    
                    if not track_name:
                        continue
                    
                    # Get additional info for this track
                    album_name = ''
                    year = ''
                    genre = ''
                    
                    try:
                        rate_limit('lastfm', 0.2)
                        info_params = {
                            'method': 'track.getInfo',
                            'track': track_name,
                            'artist': artist_name,
                            'api_key': LASTFM_API_KEY,
                            'format': 'json'
                        }
                        
                        info_response = requests.get(LASTFM_API_URL, params=info_params, timeout=5)
                        if info_response.status_code == 200:
                            info_data = info_response.json()
                            if info_data.get('track'):
                                track_info = info_data['track']
                                if track_info.get('album'):
                                    album_name = track_info['album'].get('title', '')
                                    
                                    # Get album cover from Last.fm images
                                    if track_info['album'].get('image'):
                                        images = track_info['album'].get('image', [])
                                        # Get largest image (usually last in list)
                                        for img in reversed(images):
                                            if img.get('#text'):
                                                cover_url = img.get('#text')
                                                break
                                
                                # Get genre from tags
                                if track_info.get('toptags') and track_info['toptags'].get('tag'):
                                    tags = track_info['toptags']['tag']
                                    if isinstance(tags, list) and len(tags) > 0:
                                        genre = tags[0].get('name', '')
                    except:
                        pass
                    
                    result = {
                        'title': track_name,
                        'artist': artist_name,
                        'album': album_name,
                        'year': year,
                        'genre': genre,
                        'confidence': 0.75,
                        'source': 'Last.fm',
                        'is_compilation': is_compilation_album(album_name),
                        'cover_url': cover_url if 'cover_url' in locals() else None
                    }
                    results.append(result)
            
            return results if results else None
        
        return None
    except Exception as e:
        print(f"Last.fm error: {e}")
        return None

def is_compilation_album(album_name: str) -> bool:
    """Detect if album is a compilation/hitlist"""
    if not album_name:
        return False
    
    compilation_keywords = [
        # International compilations
        'hitzone', 'now that', 'now party', 'now anthems', 'best of', 'greatest hits',
        'compilation', 'collected', 'hits', 'top 40', 'top 100',
        'dance hits', 'party hits', 'ultimate', 'essentials',
        'collection', 'anthology', 'hitlist', 'chart', 'charts',
        'various artists', 'various', 'sampler',
        'tribute', 'soundtrack', 'ost', 'original soundtrack',
        'ministry of sound', 'absolute', 'clubland',
        'hot hits', 'mega hits', 'summer hits', 'winter hits',
        'love songs', 'ballads collection', 'greatest', 'the best',
        
        # Genre compilations
        'anthems:', 'anthems ', 'r&b anthems', 'dance anthems',
        'club anthems', 'party anthems', 'workout anthems',
        
        # Dutch compilations
        'verzamelalbum', 'tmf', 'slam!fm', 'radio 538', 
        'top 2000', 'life is music', 'foute uur', 'nrj',
        'q-music', '100% nl', 'top 40', 'mega top',
        'hot this week', 'hot hits', 'weekly top', 'week chart',
        
        # Series patterns
        'vol.', 'volume', 'part', 'edition', 'series',
        
        # Award shows / Events
        'awards', 'grammy', 'ama', 'vma', 'billboard',
        'festival', 'classics', 'legends'
    ]
    
    album_lower = album_name.lower()
    
    # Check for compilation patterns
    if any(keyword in album_lower for keyword in compilation_keywords):
        return True
    
    # Check for "Life Is Music" pattern (with year/number)
    if 'life is music' in album_lower:
        return True
    
    # Check for "Anthems" pattern
    if 'anthems' in album_lower and ':' in album_lower:
        return True
    
    # Check for "NOW" series (NOW 1, NOW 100, NOW Party, etc.)
    if album_lower.startswith('now ') or ' now ' in album_lower:
        # "NOW" + number/volume/anything = compilation
        return True
    
    # Check for year patterns like "Hits 2020" or "2020 Hits"
    if 'hits' in album_lower and any(str(year) in album_lower for year in range(1990, 2030)):
        return True
    
    # Check for numbered editions like "Best Of Vol. 2" or "Hits Part 3"
    if any(word in album_lower for word in ['vol.', 'volume', 'part']) and any(str(num) in album_lower for num in range(1, 100)):
        return True
    
    return False

def download_cover_art(url: str) -> Optional[str]:
    """Download cover art from URL and return as base64"""
    try:
        if not url:
            return None
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200 and response.content:
            # Convert to base64 for JSON transfer
            return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        print(f"Cover download error: {e}")
    return None

def is_single_release(album_name: str) -> bool:
    """Detect if album is just a single release"""
    if not album_name:
        return False
    
    single_keywords = [
        '- single', ' single', '(single)', 'ep - single'
    ]
    
    album_lower = album_name.lower()
    return any(keyword in album_lower for keyword in single_keywords)

def similarity_score(str1: str, str2: str) -> float:
    """Calculate similarity between two strings (0-1)"""
    if not str1 or not str2:
        return 0.0
    
    str1 = str1.lower().strip()
    str2 = str2.lower().strip()
    
    if str1 == str2:
        return 1.0
    
    # Check if one contains the other
    if str1 in str2 or str2 in str1:
        return 0.8
    
    # Simple word overlap
    words1 = set(str1.split())
    words2 = set(str2.split())
    if words1 and words2:
        overlap = len(words1.intersection(words2))
        total = len(words1.union(words2))
        return overlap / total if total > 0 else 0.0
    
    return 0.0

def rank_result(result: Dict, existing_title: str = '', existing_artist: str = '', filename: str = '', all_results: list = None) -> float:
    """Calculate ranking score for a result (higher is better)"""
    score = result.get('confidence', 0.5)
    
    # CRITICAL: Check if result matches existing metadata or filename
    result_title = result.get('title', '')
    result_artist = result.get('artist', '')
    
    # Extract artist from filename (e.g. "SFB - Way It Goes.mp3" -> "SFB")
    filename_artist = ''
    if filename:
        name = os.path.splitext(filename)[0]
        for separator in [' - ', ' – ', ' — ', '_-_']:
            if separator in name:
                parts = name.split(separator, 1)
                filename_artist = parts[0].strip()
                break
    
    # If we have existing artist tag or filename artist, validate against it
    reference_artist = existing_artist or filename_artist
    if reference_artist and result_artist:
        artist_similarity = similarity_score(reference_artist, result_artist)
        
        if artist_similarity >= 0.8:
            # Strong match - BOOST
            score += 0.3
        elif artist_similarity >= 0.5:
            # Partial match
            score += 0.1
        elif artist_similarity < 0.3:
            # Poor match - HEAVY PENALTY
            score -= 0.5
    
    # If we have existing title, validate
    if existing_title and result_title:
        title_similarity = similarity_score(existing_title, result_title)
        
        if title_similarity >= 0.8:
            score += 0.2
        elif title_similarity < 0.3:
            score -= 0.3
    
    # Boost for having complete metadata
    if result.get('title'): score += 0.05
    if result.get('artist'): score += 0.05
    if result.get('album'): score += 0.05
    if result.get('year'): score += 0.03
    if result.get('genre'): score += 0.02
    
    # CRITICAL: Penalize compilations VERY heavily
    if result.get('is_compilation', False):
        score -= 1.5  # Massive penalty to avoid compilations
    
    # Penalize singles (prefer full albums)
    if is_single_release(result.get('album', '')):
        score -= 0.15
    
    # BOOST for original full albums (not single, not compilation)
    album = result.get('album', '')
    if album and not is_single_release(album) and not result.get('is_compilation', False):
        score += 0.5  # Strong boost for original albums
    
    # CRITICAL: Boost for earliest release date (original album)
    # A track is ALWAYS first released on its original album!
    if all_results and result.get('year'):
        try:
            result_year = int(result['year'])
            
            # Find the earliest year among all results
            years = []
            for r in all_results:
                if r.get('year'):
                    try:
                        years.append(int(r['year']))
                    except:
                        pass
            
            if years:
                earliest_year = min(years)
                
                # If this is the earliest release, BIG BOOST (it's the original!)
                if result_year == earliest_year:
                    score += 1.0  # HUGE boost for first release
                # If it's within 1 year of earliest (could be re-release), small boost
                elif result_year <= earliest_year + 1:
                    score += 0.3
                # If it's 2+ years later, penalty (likely compilation/re-release)
                elif result_year >= earliest_year + 2:
                    score -= 0.5  # Penalty for later releases
        except:
            pass
    
    # CRITICAL: Strongly prefer iTunes/Apple Music (most reliable, no compilations)
    if result.get('source') == 'iTunes':
        score += 0.8  # HUGE boost - iTunes is most reliable!
    
    # Demote Last.fm (often returns compilations)
    if result.get('source') == 'Last.fm':
        score -= 0.2  # Small penalty - less reliable
    
    # Demote MusicBrainz slightly (mixed quality)
    if result.get('source') == 'MusicBrainz':
        score -= 0.1
    
    return score

def search_musicbrainz(title: str, artist: str) -> Optional[List[Dict]]:
    """Search MusicBrainz for track metadata, prefer original albums"""
    try:
        # Rate limit: MusicBrainz requires 1 req/second
        rate_limit('musicbrainz', 1.0)
        
        # Build query
        query_parts = []
        if title:
            query_parts.append(f'recording:"{title}"')
        if artist:
            query_parts.append(f'artist:"{artist}"')
        
        if not query_parts:
            return None
        
        query = ' AND '.join(query_parts)
        
        params = {
            'query': query,
            'fmt': 'json',
            'limit': 10
        }
        
        response = requests.get(
            f"{MUSICBRAINZ_API_URL}recording/",
            params=params,
            headers=MUSICBRAINZ_HEADERS,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('recordings') and len(data['recordings']) > 0:
                results = []
                
                for recording in data['recordings'][:10]:
                    # Get artist
                    artist_name = ''
                    if recording.get('artist-credit'):
                        artists = [a['name'] for a in recording['artist-credit'] if isinstance(a, dict)]
                        artist_name = ', '.join(artists)
                    
                    # Get all releases for this recording
                    if recording.get('releases'):
                        for release in recording['releases'][:5]:  # Check first 5 releases
                            album_name = release.get('title', '')
                            is_compilation = is_compilation_album(album_name)
                            
                            result = {
                                'title': recording.get('title', ''),
                                'artist': artist_name,
                                'album': album_name,
                                'year': release.get('date', '')[:4] if release.get('date') else '',
                                'genre': '',
                                'confidence': recording.get('score', 0) / 100,
                                'is_compilation': is_compilation,
                                'source': 'MusicBrainz'
                            }
                            
                            # Get genre from tags
                            if recording.get('tags'):
                                genres = [tag['name'] for tag in recording['tags'][:2]]
                                result['genre'] = ', '.join(genres) if genres else ''
                            
                            # Boost confidence for non-compilation albums
                            if not is_compilation:
                                result['confidence'] += 0.15
                            
                            results.append(result)
                
                # Sort: prefer non-compilations and higher confidence
                results.sort(key=lambda x: (not x['is_compilation'], x['confidence']), reverse=True)
                
                # Return top 5 unique albums
                unique_albums = []
                seen_albums = set()
                for r in results:
                    key = (r['title'], r['artist'], r['album'])
                    if key not in seen_albums:
                        seen_albums.add(key)
                        unique_albums.append(r)
                        if len(unique_albums) >= 5:
                            break
                
                return unique_albums if unique_albums else None
        
        return None
    except Exception as e:
        print(f"MusicBrainz error: {e}")
        return None

def search_by_filename(filename: str) -> Optional[List[Dict]]:
    """Try to extract info from filename and search"""
    # Remove extension
    name = os.path.splitext(filename)[0]
    
    # Common patterns: "Artist - Title", "Title - Artist", "01 Title", etc.
    # Try to split by common separators
    for separator in [' - ', ' – ', ' — ', '_-_', ' _ ']:
        if separator in name:
            parts = name.split(separator, 1)
            if len(parts) == 2:
                # Try both orders - first try iTunes (better for recent)
                result = search_itunes(parts[1].strip(), parts[0].strip())
                if result and len(result) > 0:
                    return result
                
                result = search_itunes(parts[0].strip(), parts[1].strip())
                if result and len(result) > 0:
                    return result
                
                # Fallback to MusicBrainz
                result = search_musicbrainz(parts[1].strip(), parts[0].strip())
                if result and len(result) > 0:
                    return result
                
                result = search_musicbrainz(parts[0].strip(), parts[1].strip())
                if result and len(result) > 0:
                    return result
    
    # Try iTunes with just the filename
    result = search_itunes(name, '')
    if result and len(result) > 0:
        return result
    
    # Try MusicBrainz with just the filename as title
    result = search_musicbrainz(name, '')
    if result and len(result) > 0:
        return result
    
    return None

def open_folder_dialog():
    """Open native folder selection dialog"""
    try:
        if sys.platform == 'darwin':  # macOS
            script = '''
            tell application "System Events"
                activate
                set folderPath to choose folder with prompt "Select MP3 folder to clean:"
                return POSIX path of folderPath
            end tell
            '''
            result = subprocess.run(['osascript', '-e', script], 
                                  capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return result.stdout.strip()
        elif sys.platform == 'win32':  # Windows
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            folder_path = filedialog.askdirectory(title="Select MP3 folder to clean")
            root.destroy()
            return folder_path
        else:  # Linux
            # Try using zenity if available
            result = subprocess.run(['zenity', '--file-selection', '--directory', 
                                   '--title=Select MP3 folder to clean'],
                                  capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                return result.stdout.strip()
    except Exception as e:
        print(f"Error opening folder dialog: {e}")
    return None

@app.route('/')
def index():
    """Serve the main HTML page"""
    return render_template('index.html')

@app.route('/api/browse-folder', methods=['POST'])
def browse_folder():
    """Open native folder browser dialog"""
    folder_path = open_folder_dialog()
    
    if folder_path:
        return jsonify({'folder_path': folder_path})
    else:
        return jsonify({'error': 'No folder selected'}), 400


# --- Shazam-Soundeo Sync ---

@app.route('/api/shazam-sync/bootstrap', methods=['GET'])
def shazam_bootstrap():
    """Return settings + status in one call for fast initial load."""
    from config_shazam import load_config, get_soundeo_cookies_path
    settings = load_config()
    settings['soundeo_cookies_path_resolved'] = get_soundeo_cookies_path()
    status = _get_best_available_status()
    status['compare_running'] = getattr(app, '_shazam_compare_running', False)
    return jsonify({
        'settings': settings,
        'status': status,
    })


@app.route('/api/app-state', methods=['GET'])
def get_app_state():
    """Return app-wide state to restore after load/refresh (last folder path, etc.)."""
    from shazam_cache import load_app_state
    return jsonify(load_app_state())


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Return Shazam-Soundeo sync settings."""
    from config_shazam import load_config
    return jsonify(load_config())


@app.route('/api/settings', methods=['POST'])
def save_settings():
    """Save Shazam-Soundeo sync settings."""
    from config_shazam import save_config, load_config
    data = request.json or {}
    config = load_config()
    if 'destination_folders' in data:
        config['destination_folders'] = [p for p in data['destination_folders'] if p]
    if 'soundeo_cookies_path' in data:
        config['soundeo_cookies_path'] = str(data['soundeo_cookies_path'] or '')
    if 'shazam_db_path' in data:
        config['shazam_db_path'] = str(data['shazam_db_path'] or '')
    if 'headed_mode' in data:
        config['headed_mode'] = bool(data['headed_mode'])
    if 'stream_to_ui' in data:
        config['stream_to_ui'] = bool(data['stream_to_ui'])
    save_config(config)
    return jsonify(load_config())


def _fetch_shazam_from_db():
    """Read tracks from Shazam DB. Uses auto-detect first (like original), falls back to config path."""
    from config_shazam import load_config
    from shazam_reader import get_shazam_tracks
    # Try auto-detect first (primary ShazamLibrary path) - this worked when Compare loaded 3000+ tracks
    new_tracks = []
    try:
        new_tracks = get_shazam_tracks(db_path=None)
    except FileNotFoundError:
        pass
    # Fallback: config path if auto-detect failed
    if not new_tracks:
        config = load_config()
        db_path = (config.get('shazam_db_path') or '').strip() or None
        if db_path and os.path.exists(db_path):
            try:
                new_tracks = get_shazam_tracks(db_path=db_path)
            except (FileNotFoundError, Exception):
                pass
    return new_tracks


@app.route('/api/shazam-sync/fetch-shazam', methods=['POST'])
def shazam_sync_fetch():
    """Fetch tracks from Shazam DB and merge into cache (no duplicates)."""
    from shazam_cache import load_shazam_cache, save_shazam_cache, merge_shazam_tracks
    try:
        new_tracks = _fetch_shazam_from_db()
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': f'Shazam: {str(e)}'}), 500
    if not new_tracks:
        return jsonify({
            'error': 'No tracks found in Shazam database. Ensure Shazam has tagged songs. Check Settings for custom DB path.',
            'total': 0, 'added': 0,
        }), 200
    existing = load_shazam_cache()
    merged, added = merge_shazam_tracks(existing, new_tracks)
    save_shazam_cache(merged)
    # Update status immediately so list shows tracks without requiring Compare
    status = _rebuild_status_from_caches() or _build_status_from_shazam_only(merged)
    if status:
        from shazam_cache import save_status_cache
        app._shazam_sync_status = status
        save_status_cache(status)
    return jsonify({
        'total': len(merged),
        'added': added,
        'message': f'Fetched {len(new_tracks)} from Shazam, {added} new. Total cached: {len(merged)}.',
    })


def _path_under(path: str, folder: str) -> bool:
    """True if path is folder or under folder."""
    if not path or not folder:
        return False
    p = os.path.abspath(path)
    d = os.path.abspath(folder).rstrip(os.sep)
    return p == d or p.startswith(d + os.sep)


def _folder_scan_stats(
    folder_paths: List[str],
    local_tracks: List[Dict],
    have_locally: List[Dict],
) -> List[Dict]:
    """Per-folder counts: scanned (from local_tracks), matched (from have_locally with filepath under folder)."""
    result = []
    for folder in folder_paths:
        folder_abs = os.path.abspath(folder).rstrip(os.sep)
        scanned = sum(1 for t in local_tracks if _path_under(t.get('filepath', ''), folder_abs))
        matched = sum(1 for h in have_locally if _path_under(h.get('filepath', ''), folder_abs))
        result.append({
            'path': folder,
            'scanned': scanned,
            'matched': matched,
        })
    return result


def _run_compare_background():
    """Background thread: run full compare and save result."""
    from config_shazam import get_destination_folders, get_destination_folders_raw
    from local_scanner import scan_folders, compute_to_download, _find_matching_local_track, ScanCancelled
    from shazam_cache import (
        load_local_scan_cache, save_local_scan_cache,
        local_scan_cache_valid, save_status_cache, load_skip_list,
    )
    try:
        shazam_tracks = getattr(app, '_shazam_compare_shazam_tracks', [])
        if not shazam_tracks:
            app._shazam_compare_running = False
            return
        configured_folders = get_destination_folders_raw()
        folder_paths = get_destination_folders()  # only existing dirs
        if not configured_folders:
            skipped = load_skip_list()
            to_dl = [
                {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                for t in shazam_tracks
                if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped
            ]
            status = {
                'error': 'No destination folders configured. Add folders in Settings.',
                'shazam_count': len(shazam_tracks),
                'local_count': 0,
                'to_download_count': len(to_dl),
                'to_download': to_dl,
                'have_locally': [],
                'folder_stats': [],
            }
        else:
            local_scan = load_local_scan_cache()
            if local_scan_cache_valid(local_scan, folder_paths):
                local_tracks = local_scan.get('tracks', [])
            else:
                app._shazam_compare_cancel_requested = False
                app._shazam_scan_progress = {'scanning': True, 'current': 0, 'total': 0, 'message': 'Discovering files...'}
                # #region agent log
                try:
                    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
                    with open(_log_path, 'a') as _f:
                        _f.write(json.dumps({'location': 'app.py:_run_compare_background', 'message': 'progress set', 'data': {'phase': 'discovering'}, 'timestamp': int(__import__('time').time() * 1000), 'hypothesisId': 'H3'}) + '\n')
                except Exception:
                    pass
                # #endregion

                def _on_progress(current: int, total: int):
                    msg = 'Discovering files...' if total == 0 else 'Scanning files...'
                    app._shazam_scan_progress = {'scanning': True, 'current': current, 'total': total, 'message': msg}

                def _should_cancel():
                    return getattr(app, '_shazam_compare_cancel_requested', False)

                try:
                    # use_filename_only=True: match from "Artist - Title" in filenames only (no per-file tag read). Much faster for large libraries.
                    local_tracks = scan_folders(
                        folder_paths,
                        on_progress=_on_progress,
                        should_cancel=_should_cancel,
                        use_filename_only=True,
                    )
                except ScanCancelled:
                    app._shazam_sync_status = {'message': 'Compare cancelled.'}
                    app._shazam_scan_progress = {}
                    app._shazam_compare_running = False
                    return
                app._shazam_scan_progress = {'scanning': True, 'current': len(local_tracks), 'total': len(local_tracks), 'message': 'Matching tracks...'}
                save_local_scan_cache(folder_paths, local_tracks)
            skipped = load_skip_list()
            to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
            to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
            to_dl_set = {(s['artist'], s['title']) for s in to_download}
            have_locally = []
            for s in shazam_tracks:
                if (s['artist'], s['title']) not in to_dl_set:
                    item = {'artist': s['artist'], 'title': s['title']}
                    if s.get('shazamed_at') is not None:
                        item['shazamed_at'] = s['shazamed_at']
                    match = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                    if match and match.get('filepath'):
                        item['filepath'] = match['filepath']
                    have_locally.append(item)
            folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
            status = {
                'shazam_count': len(shazam_tracks),
                'local_count': len(local_tracks),
                'to_download_count': len(to_download),
                'to_download': to_download,
                'have_locally': have_locally,
                'folder_stats': folder_stats,
            }
            missing = [p for p in configured_folders if p not in folder_paths]
            if missing:
                status['folder_warning'] = f'{len(missing)} folder(s) not found or not accessible (not scanned): ' + ', '.join(os.path.basename(p.rstrip(os.sep)) or p for p in missing[:5])
        _merge_preserved_urls_into_status(status)
        app._shazam_sync_status = status
        save_status_cache(status)
        # #region agent log
        try:
            _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
            with open(_log_path, 'a') as _f:
                _f.write(json.dumps({'location': 'app.py:_run_compare_background', 'message': 'compare done', 'hypothesisId': 'H4', 'data': {'have_len': len(status.get('have_locally', [])), 'to_download_len': len(status.get('to_download', []))}, 'timestamp': int(__import__('time').time() * 1000)}) + '\n')
        except Exception:
            pass
        # #endregion
    except Exception:
        app._shazam_sync_status = {'error': 'Compare failed'}
    finally:
        app._shazam_compare_running = False


@app.route('/api/shazam-sync/compare', methods=['POST'])
def shazam_sync_compare():
    """
    Start compare in background. Returns immediately. Poll status for result.
    """
    from config_shazam import get_destination_folders, get_destination_folders_raw
    from local_scanner import compute_to_download, _find_matching_local_track
    from shazam_cache import (
        load_shazam_cache, save_shazam_cache, load_local_scan_cache,
        local_scan_cache_valid, load_skip_list,
    )
    if getattr(app, '_shazam_compare_running', False):
        return jsonify({'running': True, 'message': 'Compare already in progress'}), 200
    shazam_tracks = load_shazam_cache()
    if not shazam_tracks:
        try:
            new_tracks = _fetch_shazam_from_db()
            if new_tracks:
                from shazam_cache import merge_shazam_tracks
                merged, _ = merge_shazam_tracks([], new_tracks)
                save_shazam_cache(merged)
                shazam_tracks = merged
        except Exception:
            pass
    if not shazam_tracks:
        return jsonify({
            'error': 'No Shazam tracks found. Click "Fetch Shazam" first.',
            'shazam_count': 0, 'local_count': 0, 'to_download_count': 0,
            'to_download': [], 'have_locally': [],
        }), 400
    configured_folders = get_destination_folders_raw()
    folder_paths = get_destination_folders()
    if not configured_folders:
        # No folders configured
        skipped = load_skip_list()
        to_dl = [
            {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
            for t in shazam_tracks
            if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped
        ]
        status = {
            'error': 'No destination folders configured. Add folders in Settings.',
            'shazam_count': len(shazam_tracks),
            'local_count': 0,
            'to_download_count': len(to_dl),
            'to_download': to_dl,
            'have_locally': [],
            'folder_stats': [],
        }
        _merge_preserved_urls_into_status(status)
        app._shazam_sync_status = status
        from shazam_cache import save_status_cache
        save_status_cache(status)
        return jsonify(status)
    # Use cached local scan if valid (cache keyed by folder_paths = existing dirs only)
    local_scan = load_local_scan_cache()
    cache_valid = local_scan_cache_valid(local_scan, folder_paths)
    # #region agent log
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
        os.makedirs(os.path.dirname(_log_path), exist_ok=True)
        with open(_log_path, 'a') as _f:
            _f.write(json.dumps({'location': 'app.py:compare', 'message': 'compare branch', 'data': {'cache_valid': cache_valid, 'folder_count': len(folder_paths)}, 'timestamp': int(__import__('time').time() * 1000), 'hypothesisId': 'H1'}) + '\n')
    except Exception:
        pass
    # #endregion
    if cache_valid:
        local_tracks = local_scan.get('tracks', [])
        skipped = load_skip_list()
        to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
        to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        to_dl_set = {(s['artist'], s['title']) for s in to_download}
        have_locally = []
        for s in shazam_tracks:
            if (s['artist'], s['title']) not in to_dl_set:
                item = {'artist': s['artist'], 'title': s['title']}
                if s.get('shazamed_at') is not None:
                    item['shazamed_at'] = s['shazamed_at']
                match = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                if match and match.get('filepath'):
                    item['filepath'] = match['filepath']
                have_locally.append(item)
        folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
        status = {
            'shazam_count': len(shazam_tracks),
            'local_count': len(local_tracks),
            'to_download_count': len(to_download),
            'to_download': to_download,
            'have_locally': have_locally,
            'folder_stats': folder_stats,
        }
        _merge_preserved_urls_into_status(status)
        app._shazam_sync_status = status
        from shazam_cache import save_status_cache
        save_status_cache(status)
        # #region agent log
        try:
            _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
            with open(_log_path, 'a') as _f:
                _f.write(json.dumps({'location': 'app.py:compare(cache_valid)', 'message': 'returning status', 'hypothesisId': 'H1H5', 'data': {'have_len': len(have_locally), 'to_download_len': len(to_download), 'shazam_count': len(shazam_tracks)}, 'timestamp': int(__import__('time').time() * 1000)}) + '\n')
        except Exception:
            pass
        # #endregion
        return jsonify(status)
    # Need to scan - require at least one existing folder
    if not folder_paths:
        return jsonify({
            'error': 'Destination folders not found (drive unmounted?). Check paths in Settings. Only existing folders are scanned.',
            'shazam_count': len(shazam_tracks),
            'local_count': 0,
            'to_download_count': 0,
            'to_download': [],
            'have_locally': [],
            'folder_stats': [],
        }), 400
    # Run scan in background
    app._shazam_compare_shazam_tracks = shazam_tracks
    app._shazam_compare_running = True
    # #region agent log
    try:
        _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
        with open(_log_path, 'a') as _f:
            _f.write(json.dumps({'location': 'app.py:compare', 'message': 'starting background', 'data': {}, 'timestamp': int(__import__('time').time() * 1000), 'hypothesisId': 'H3'}) + '\n')
    except Exception:
        pass
    # #endregion
    thread = threading.Thread(target=_run_compare_background, daemon=True)
    thread.start()
    return jsonify({'running': True, 'message': 'Scanning local folders...'})


@app.route('/api/shazam-sync/cancel-compare', methods=['POST'])
def shazam_sync_cancel_compare():
    """Request cancellation of the running compare (scan will stop at next checkpoint)."""
    if not getattr(app, '_shazam_compare_running', False):
        return jsonify({'ok': True, 'message': 'No compare in progress.'})
    app._shazam_compare_cancel_requested = True
    return jsonify({'ok': True, 'message': 'Stopping compare...'})


def _build_status_from_shazam_only(shazam_tracks: List[Dict]) -> Optional[Dict]:
    """Build status with all Shazam tracks as to_download (minus skipped). Used after Fetch."""
    if not shazam_tracks:
        return None
    from shazam_cache import load_skip_list
    skipped = load_skip_list()
    to_dl = [
        {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
        for t in shazam_tracks
        if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped
    ]
    return {
        'shazam_count': len(shazam_tracks), 'local_count': 0, 'to_download_count': len(to_dl),
        'to_download': to_dl, 'have_locally': [],
        'folder_stats': [],
        'message': 'Click Compare to match with local folders.',
    }


def _rebuild_status_from_caches():
    """Rebuild compare status from shazam + local caches when status file is missing/stale."""
    from config_shazam import get_destination_folders
    from local_scanner import compute_to_download, _find_matching_local_track
    from shazam_cache import load_shazam_cache, load_local_scan_cache, local_scan_cache_valid, load_skip_list, load_status_cache
    shazam_tracks = load_shazam_cache()
    if not shazam_tracks:
        return None
    folder_paths = get_destination_folders()  # only existing dirs (cache is keyed by these)
    if not folder_paths:
        skipped = load_skip_list()
        to_dl = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                 for t in shazam_tracks if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        out = {'shazam_count': len(shazam_tracks), 'local_count': 0, 'to_download_count': len(to_dl),
               'to_download': to_dl, 'have_locally': [], 'folder_stats': [], 'error': 'No destination folders configured.'}
        old = load_status_cache()
        if old:
            if old.get('urls'):
                out['urls'] = dict(old['urls'])
            if old.get('starred'):
                out['starred'] = dict(old['starred'])
        return out
    local_scan = load_local_scan_cache()
    if not local_scan_cache_valid(local_scan, folder_paths):
        return None
    local_tracks = local_scan.get('tracks', [])
    skipped = load_skip_list()
    to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
    to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
    to_dl_set = {(s['artist'], s['title']) for s in to_download}
    have_locally = []
    for s in shazam_tracks:
        if (s['artist'], s['title']) not in to_dl_set:
            item = {'artist': s['artist'], 'title': s['title']}
            if s.get('shazamed_at') is not None:
                item['shazamed_at'] = s['shazamed_at']
            match = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
            if match and match.get('filepath'):
                item['filepath'] = match['filepath']
            have_locally.append(item)
    folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
    out = {'shazam_count': len(shazam_tracks), 'local_count': len(local_tracks), 'to_download_count': len(to_download),
           'to_download': to_download, 'have_locally': have_locally, 'folder_stats': folder_stats}
    # Preserve favorited/synced URLs and starred state across rebuilds so they survive refresh
    old = load_status_cache()
    if old:
        if old.get('urls'):
            out['urls'] = dict(old['urls'])
        if old.get('starred'):
            out['starred'] = dict(old['starred'])
    return out


def _merge_preserved_urls_into_status(status: Dict) -> None:
    """Merge existing favorited/synced URLs and starred state from cache into status so they survive compare/rescan. Mutates status."""
    from shazam_cache import load_status_cache
    old = load_status_cache()
    if old:
        if old.get('urls'):
            status['urls'] = {**(status.get('urls') or {}), **old['urls']}
        if old.get('starred'):
            status['starred'] = {**(status.get('starred') or {}), **old['starred']}


def _status_is_stale(status: Optional[Dict]) -> bool:
    """True if status doesn't match current shazam_cache (e.g. Fetch added more tracks)."""
    if not status:
        return True
    from shazam_cache import load_shazam_cache
    current = load_shazam_cache()
    current_count = len(current) if current else 0
    return status.get('shazam_count', 0) != current_count


def _get_best_available_status():
    """Return best available status: in-memory, file, rebuild, or partial. Persists when building fresh.
    Always prefers cached data on restart - only rebuilds when Shazam tracks have changed (new/removed)."""
    from shazam_cache import load_status_cache, save_status_cache, load_shazam_cache, load_skip_list
    # #region agent log
    _log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor', 'debug.log')
    def _log_status_source(source, st):
        try:
            have_len = len(st.get('have_locally') or [])
            to_len = st.get('to_download_count', 0)
            with open(_log_path, 'a') as _f:
                _f.write(json.dumps({'location': 'app.py:_get_best_available_status', 'message': 'source', 'hypothesisId': 'H2', 'data': {'source': source, 'have_len': have_len, 'to_download_count': to_len}, 'timestamp': int(__import__('time').time() * 1000)}) + '\n')
        except Exception:
            pass
    # #endregion
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status and not _status_is_stale(app._shazam_sync_status):
        # #region agent log
        _log_status_source('in_memory', app._shazam_sync_status)
        # #endregion
        return dict(app._shazam_sync_status)
    cached = load_status_cache()
    has_cached_data = cached and (cached.get('shazam_count', 0) > 0 or cached.get('have_locally') or cached.get('to_download'))
    if has_cached_data and not _status_is_stale(cached):
        app._shazam_sync_status = cached
        # #region agent log
        _log_status_source('file_cache', cached)
        # #endregion
        return dict(cached)
    rebuilt = _rebuild_status_from_caches()
    if rebuilt:
        app._shazam_sync_status = rebuilt
        save_status_cache(rebuilt)
        # #region agent log
        _log_status_source('rebuilt', rebuilt)
        # #endregion
        return rebuilt
    # Fallback: use cached status even if stale (e.g. Shazam count changed) - better than empty
    if has_cached_data:
        out = dict(cached)
        if _status_is_stale(cached):
            out['message'] = out.get('message') or 'Data may be outdated. Click Fetch Shazam or Compare to refresh.'
        app._shazam_sync_status = out
        # #region agent log
        _log_status_source('file_cache_stale', out)
        # #endregion
        return out
    shazam_tracks = load_shazam_cache()
    if shazam_tracks:
        skipped = load_skip_list()
        to_dl = [
            {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
            for t in shazam_tracks
            if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped
        ]
        partial = {
            'shazam_count': len(shazam_tracks), 'local_count': 0, 'to_download_count': len(to_dl),
            'to_download': to_dl, 'have_locally': [],
            'folder_stats': [],
            'message': 'Local folders need rescan. Click Rescan to refresh.',
        }
        old = load_status_cache()
        if old:
            if old.get('urls'):
                partial['urls'] = dict(old['urls'])
            if old.get('starred'):
                partial['starred'] = dict(old['starred'])
        app._shazam_sync_status = partial
        save_status_cache(partial)
        return partial
    return {'shazam_count': 0, 'local_count': 0, 'to_download_count': 0, 'to_download': [], 'have_locally': [], 'folder_stats': []}


@app.route('/api/shazam-sync/status', methods=['GET'])
def shazam_sync_status():
    """Return last comparison status. Never return empty when Shazam/local data exists."""
    compare_running = getattr(app, '_shazam_compare_running', False)
    out = _get_best_available_status()
    # #region agent log
    try:
        with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
            _f.write(json.dumps({"location": "app.py:shazam_sync_status", "message": "returning status", "hypothesisId": "H3", "data": {"urls_keys": list((out.get("urls") or {}).keys()), "starred_keys": list((out.get("starred") or {}).keys())}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
    except Exception:
        pass
    # #endregion
    out['compare_running'] = compare_running
    if compare_running:
        out['message'] = out.get('message') or 'Scanning local folders...'
        scan_progress = getattr(app, '_shazam_scan_progress', None) or {}
        if scan_progress.get('scanning'):
            out['scan_progress'] = {
                'current': scan_progress.get('current', 0),
                'total': scan_progress.get('total', 0),
                'message': scan_progress.get('message') or ('Discovering files...' if scan_progress.get('total', 0) == 0 else 'Scanning files...'),
            }
    return jsonify(out)


@app.route('/api/shazam-sync/export/local-filenames')
def shazam_export_local_filenames():
    """Download a text log of all scanned local filenames (one per line)."""
    from config_shazam import get_destination_folders
    from shazam_cache import load_local_scan_cache, local_scan_cache_valid
    folder_paths = get_destination_folders()
    local_scan = load_local_scan_cache()
    if not folder_paths or not local_scan_cache_valid(local_scan, folder_paths):
        return Response("Run Compare first to scan local folders.", status=400, mimetype='text/plain')
    tracks = local_scan.get('tracks', [])
    lines = [t.get('filename') or os.path.basename(t.get('filepath', '')) or '' for t in tracks]
    body = '\n'.join(lines) if lines else ''
    return Response(body, mimetype='text/plain', headers={
        'Content-Disposition': 'attachment; filename="local_filenames.txt"'
    })


@app.route('/api/shazam-sync/export/shazam-tracks')
def shazam_export_shazam_tracks():
    """Download a text log of all Shazam tracks (Artist | Title per line)."""
    from shazam_cache import load_shazam_cache
    tracks = load_shazam_cache()
    if not tracks:
        return Response("Fetch Shazam first to load tracks.", status=400, mimetype='text/plain')
    lines = [f"{t.get('artist', '')} | {t.get('title', '')}" for t in tracks]
    body = '\n'.join(lines) if lines else ''
    return Response(body, mimetype='text/plain', headers={
        'Content-Disposition': 'attachment; filename="shazam_tracks.txt"'
    })


@app.route('/api/shazam-sync/stream-file')
def shazam_stream_file():
    """Stream audio file for playback. Path must be under a destination folder."""
    from config_shazam import get_destination_folders_raw
    import base64
    path_b64 = request.args.get('path')
    if not path_b64:
        return "Missing path", 400
    try:
        path = base64.urlsafe_b64decode(path_b64).decode('utf-8')
    except Exception:
        return "Invalid path", 400
    path = os.path.abspath(path)
    if not os.path.exists(path) or not os.path.isfile(path):
        return "File not found", 404
    allowed = [os.path.abspath(f).rstrip(os.sep) for f in get_destination_folders_raw() if f]
    if not any(path == d or path.startswith(d + os.sep) for d in allowed):
        return "Access denied", 403
    ext = os.path.splitext(path)[1].lower()
    mimetypes = {'.mp3': 'audio/mpeg', '.aiff': 'audio/aiff', '.aif': 'audio/aiff', '.wav': 'audio/wav'}
    mimetype = mimetypes.get(ext, 'application/octet-stream')
    return send_file(path, mimetype=mimetype, as_attachment=False, conditional=True)


@app.route('/api/shazam-sync/rescan', methods=['POST'])
def shazam_sync_rescan():
    """Force rescan of all local folders and re-compare."""
    import os
    from shazam_cache import LOCAL_SCAN_CACHE_PATH
    if os.path.exists(LOCAL_SCAN_CACHE_PATH):
        os.remove(LOCAL_SCAN_CACHE_PATH)
    return shazam_sync_compare()


@app.route('/api/shazam-sync/rescan-folder', methods=['POST'])
def shazam_sync_rescan_folder():
    """Rescan a single folder only, merge with existing cache, re-compare."""
    from config_shazam import get_destination_folders_raw
    from local_scanner import scan_folders, compute_to_download, _find_matching_local_track, ScanCancelled
    from shazam_cache import (
        load_shazam_cache, load_local_scan_cache, save_local_scan_cache,
        save_status_cache, load_skip_list,
    )
    data = request.get_json() or {}
    folder_path = (data.get('folder_path') or '').strip()
    if not folder_path or not os.path.isdir(folder_path):
        return jsonify({'error': 'Invalid or missing folder path.'}), 400
    if getattr(app, '_shazam_compare_running', False):
        return jsonify({'error': 'Compare already in progress.'}), 400

    folder_abs = os.path.abspath(folder_path).rstrip(os.sep)
    folder_paths = get_destination_folders_raw()
    folder_paths_norm = [os.path.abspath(p).rstrip(os.sep) for p in folder_paths]
    if folder_abs not in folder_paths_norm:
        folder_paths = list(folder_paths) + [folder_path]
        from config_shazam import load_config, save_config
        cfg = load_config()
        cfg['destination_folders'] = folder_paths
        save_config(cfg)

    def _path_under(path: str, base: str) -> bool:
        p = os.path.abspath(path)
        return p == base or p.startswith(base + os.sep)

    def _run():
        try:
            cache = load_local_scan_cache()
            other_tracks = []
            if cache and cache.get('tracks'):
                for t in cache.get('tracks', []):
                    fp = t.get('filepath', '')
                    if not _path_under(fp, folder_abs):
                        other_tracks.append(t)
            app._shazam_compare_cancel_requested = False
            app._shazam_scan_progress = {'scanning': True, 'current': 0, 'total': 0, 'message': 'Discovering files...'}

            def _on_progress(cur: int, tot: int):
                msg = 'Discovering files...' if tot == 0 else 'Scanning files...'
                app._shazam_scan_progress = {'scanning': True, 'current': cur, 'total': tot, 'message': msg}

            def _should_cancel():
                return getattr(app, '_shazam_compare_cancel_requested', False)

            new_tracks = scan_folders(
                [folder_path],
                on_progress=_on_progress,
                should_cancel=_should_cancel,
                use_filename_only=True,
            )
            app._shazam_scan_progress = {}
            merged = other_tracks + [{"artist": t["artist"], "title": t["title"], "filepath": t["filepath"]} for t in new_tracks]
            save_local_scan_cache(folder_paths, merged)
            shazam_tracks = load_shazam_cache()
            if not shazam_tracks:
                app._shazam_sync_status = {'message': 'No Shazam tracks. Fetch Shazam first.'}
            else:
                skipped = load_skip_list()
                to_download_raw, title_word_index, exact_match_map = compute_to_download(shazam_tracks, merged)
                to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
                to_dl_set = {(s['artist'], s['title']) for s in to_download}
                have_locally = []
                for s in shazam_tracks:
                    if (s['artist'], s['title']) not in to_dl_set:
                        item = {'artist': s['artist'], 'title': s['title']}
                        if s.get('shazamed_at') is not None:
                            item['shazamed_at'] = s['shazamed_at']
                        match = _find_matching_local_track(s, merged, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                        if match and match.get('filepath'):
                            item['filepath'] = match['filepath']
                        have_locally.append(item)
                folders_with_data = list(set([folder_abs] + [os.path.abspath(f).rstrip(os.sep) for f in (cache.get('folders') or [])]))
                folder_stats = _folder_scan_stats(folders_with_data, merged, have_locally)
                status = {
                    'shazam_count': len(shazam_tracks),
                    'local_count': len(merged),
                    'to_download_count': len(to_download),
                    'to_download': to_download,
                    'have_locally': have_locally,
                    'folder_stats': folder_stats,
                }
                _merge_preserved_urls_into_status(status)
                app._shazam_sync_status = status
                save_status_cache(status)
        except ScanCancelled:
            app._shazam_sync_status = {'message': 'Rescan cancelled.'}
        except Exception:
            app._shazam_sync_status = {'error': 'Rescan failed'}
        finally:
            app._shazam_compare_running = False

    app._shazam_compare_running = True
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({'running': True, 'message': f'Rescanning: {os.path.basename(folder_path) or folder_path}'})


@app.route('/api/shazam-sync/progress', methods=['GET'])
def shazam_sync_progress():
    """Return current automation progress."""
    progress = getattr(app, '_shazam_sync_progress', None)
    if not progress:
        return jsonify({'running': False, 'current': 0, 'total': 0})
    return jsonify(progress)


def _run_soundeo_automation(tracks: list):
    """Background thread: run Soundeo automation for tracks."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import run_favorite_tracks, set_streaming_active

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    # #region agent log
    try:
        _log = {"location": "app.py:_run_soundeo_automation", "message": "sync load cookies path", "data": {"cookies_path": cookies_path}, "timestamp": int(__import__("time").time() * 1000), "hypothesisId": "H1H5"}
        with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
            _f.write(json.dumps(_log) + "\n")
    except Exception:
        pass
    # #endregion
    headed = config.get('headed_mode', True)
    stream_frames = config.get('stream_to_ui', True)

    def on_progress(current: int, total: int, msg: str, url: Optional[str]):
        app._shazam_sync_progress = {
            'running': True,
            'current': current,
            'total': total,
            'message': msg,
            'last_url': url,
        }

    try:
        results = run_favorite_tracks(
            tracks, cookies_path,
            headed=headed,
            stream_frames=stream_frames,
            on_progress=on_progress,
        )
        # #region agent log
        try:
            _urls = results.get('urls') or {}
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"location": "app.py:_run_soundeo_automation", "message": "sync results", "hypothesisId": "H1", "data": {"urls_keys": list(_urls.keys()), "urls_len": len(_urls)}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        app._shazam_sync_progress = {
            'running': False,
            'done': results.get('done', 0),
            'failed': results.get('failed', 0),
            'error': results.get('error'),
            'urls': results.get('urls', {}),
            'stopped': results.get('stopped', False),
        }
        # Persist favorited URLs and starred state into status cache so they survive refresh
        from shazam_cache import save_status_cache
        status = getattr(app, '_shazam_sync_status', None) or {}
        existing_urls = status.get('urls') or {}
        new_urls = results.get('urls') or {}
        existing_starred = status.get('starred') or {}
        # Tracks we just favorited (or that were already starred) are starred in Soundeo
        new_starred = {k: True for k in new_urls}
        status = dict(status)
        status['urls'] = {**existing_urls, **new_urls}
        status['starred'] = {**existing_starred, **new_starred}
        app._shazam_sync_status = status
        # #region agent log
        try:
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"location": "app.py:_run_soundeo_automation", "message": "before save_status_cache", "hypothesisId": "H1H2", "data": {"status_urls_keys": list(status.get("urls", {}).keys()), "status_starred_keys": list(status.get("starred", {}).keys())}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        save_status_cache(status)
    except Exception as e:
        # #region agent log
        try:
            with open("/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/MP3 Cleaner/.cursor/debug.log", "a", encoding="utf-8") as _f:
                _f.write(json.dumps({"location": "app.py:_run_soundeo_automation", "message": "exception", "hypothesisId": "H1", "data": {"error": str(e)}, "timestamp": int(__import__("time").time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
        app._shazam_sync_progress = {
            'running': False,
            'error': str(e),
        }


@app.route('/api/shazam-sync/run-soundeo', methods=['POST'])
def shazam_sync_run_soundeo():
    """Start background Soundeo automation. Uses selected tracks from body, else all to_download."""
    if getattr(app, '_shazam_sync_progress', {}).get('running'):
        return jsonify({'error': 'Sync already running.'}), 400
    tracks = None
    if request.get_data():
        try:
            data = request.get_json(silent=True) or {}
            if data.get('tracks'):
                tracks = data['tracks']
        except Exception:
            pass
    if not tracks:
        status = getattr(app, '_shazam_sync_status', None)
        if not status or not status.get('to_download'):
            return jsonify({'error': 'No tracks to sync. Run Compare first or select tracks.'}), 400
        tracks = status['to_download']
    app._shazam_sync_progress = {'running': True, 'current': 0, 'total': len(tracks)}
    thread = threading.Thread(
        target=_run_soundeo_automation,
        args=(tracks,),
        daemon=True,
    )
    thread.start()
    return jsonify({'status': 'started', 'total': len(tracks)})


@app.route('/api/shazam-sync/stop', methods=['POST'])
def shazam_sync_stop():
    """Request the running Soundeo sync to stop after the current track."""
    from soundeo_automation import request_sync_stop
    request_sync_stop()
    return jsonify({'ok': True, 'message': 'Stop requested. Sync will stop after current track.'})


@app.route('/api/shazam-sync/skip', methods=['POST'])
def shazam_sync_skip():
    """Add selected tracks to skip list. Body: { tracks: [{artist, title}, ...] }."""
    try:
        data = request.get_json(silent=True) or {}
        tracks = data.get('tracks', [])
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not tracks:
        return jsonify({'error': 'No tracks to skip'}), 400
    from shazam_cache import add_to_skip_list
    added = add_to_skip_list(tracks)
    # Update in-memory status: remove skipped from to_download
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status:
        from shazam_cache import load_skip_list
        skipped = load_skip_list()
        to_dl = [t for t in app._shazam_sync_status.get('to_download', [])
                 if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        app._shazam_sync_status['to_download'] = to_dl
        app._shazam_sync_status['to_download_count'] = len(to_dl)
        from shazam_cache import save_status_cache
        save_status_cache(app._shazam_sync_status)
    return jsonify({'skipped': added, 'message': f'Added {added} track(s) to skip list'})


# Minimal 1x1 gray JPEG for placeholder
_PLACEHOLDER_JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xfe\x46\x9d\xff\xd9'


@app.route('/api/shazam-sync/video-feed')
def shazam_sync_video_feed():
    """MJPEG stream of live automation browser."""
    from soundeo_automation import get_latest_frame, is_streaming_active

    def generate():
        for _ in range(9000):
            frame = get_latest_frame()
            if frame:
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            elif is_streaming_active():
                yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + _PLACEHOLDER_JPEG + b'\r\n')
            time.sleep(0.2)

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


@app.route('/api/soundeo/start-save-session', methods=['POST'])
def soundeo_start_save_session():
    """Start save-session flow: opens browser for user to log in."""
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import run_save_session_flow, create_save_session_event
    import soundeo_automation as _snd

    if getattr(app, '_save_session_thread', None) and app._save_session_thread.is_alive():
        return jsonify({'error': 'Save session already in progress.'}), 400
    cookies_path = get_soundeo_cookies_path()
    app._soundeo_save_session_cookies_path = cookies_path  # persist this exact path when user clicks "I have logged in"
    done_event = create_save_session_event()
    def run():
        run_save_session_flow(cookies_path, headed=True, done_event=done_event)
    app._save_session_thread = threading.Thread(target=run, daemon=True)
    app._save_session_thread.start()
    # Brief wait so we can detect if browser failed to start (e.g. Chrome profile in use)
    import time as _time
    _time.sleep(2.5)
    err = getattr(_snd, '_save_session_last_error', None)
    if err:
        return jsonify({'error': 'Browser could not start. Close any open Soundeo/Chrome window that was used for Save Session or Sync, then try again. Details: ' + err[:200]}), 500
    return jsonify({'status': 'started', 'message': 'Browser opened. Log in, wait for the main page, then click "I have logged in".'})


@app.route('/api/soundeo/session-saved', methods=['POST'])
def soundeo_session_saved():
    """User has logged in - signal save-session flow to save cookies, persist path in config."""
    from soundeo_automation import signal_save_session_done
    from config_shazam import get_soundeo_cookies_path, load_config, save_config
    signal_save_session_done()
    # Persist the exact path we wrote to (from start-save-session), not a freshly resolved path that may differ
    config = load_config()
    config['soundeo_cookies_path'] = getattr(app, '_soundeo_save_session_cookies_path', None) or get_soundeo_cookies_path()
    save_config(config)
    return jsonify({'status': 'ok'})


@app.route('/api/scan-folder', methods=['POST'])
def scan_folder():
    """Scan a folder for MP3 files"""
    global current_folder
    
    data = request.json
    folder_path = data.get('folder_path', '')
    
    if not folder_path or not os.path.exists(folder_path):
        return jsonify({'error': 'Invalid folder path'}), 400
    
    # Store the current folder path globally
    current_folder = folder_path
    print(f"✅ Set current_folder to: {current_folder}")

    mp3_files = []

    # Recursively find all MP3 files
    try:
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith('.mp3'):
                    filepath = os.path.join(root, file)
                    file_info = get_file_info(filepath)
                    mp3_files.append(file_info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Persist app state so it survives refresh/restart
    try:
        from shazam_cache import save_app_state
        save_app_state({'last_folder_path': folder_path, 'last_scan_count': len(mp3_files)})
    except Exception:
        pass

    return jsonify({
        'count': len(mp3_files),
        'files': mp3_files
    })

@app.route('/api/lookup-metadata', methods=['POST'])
def lookup_metadata():
    """Look up metadata for a single file - returns multiple options"""
    data = request.json
    filepath = data.get('filepath', '')
    title = data.get('title', '')
    artist = data.get('artist', '')
    filename = os.path.basename(filepath) if filepath else ''
    
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Invalid file path'}), 400
    
    all_results = []
    errors = []
    
    # PRIORITY ORDER: iTunes first (most reliable), then others as backup
    # iTunes/Apple Music: Best quality, official releases only, no compilations
    # Last.fm: Large database but includes many compilations
    # MusicBrainz: Comprehensive but mixed quality
    search_functions = [
        ('iTunes', lambda: search_itunes(title, artist) if (title or artist) else None),
        ('MusicBrainz', lambda: search_musicbrainz(title, artist) if (title or artist) else None),
        ('Last.fm', lambda: search_lastfm(title, artist) if (title or artist) else None),
    ]
    
    for source_name, search_func in search_functions:
        try:
            results = search_func()
            if results:
                all_results.extend(results)
        except Exception as e:
            errors.append(f"{source_name}: {str(e)}")
            print(f"Error searching {source_name}: {e}")
    
    # If no results yet, try filename-based search
    if not all_results:
        try:
            filename = os.path.basename(filepath)
            filename_results = search_by_filename(filename)
            if filename_results:
                all_results.extend(filename_results)
        except Exception as e:
            errors.append(f"Filename search: {str(e)}")
            print(f"Error in filename search: {e}")
    
    if all_results:
        # Remove duplicates based on title+artist+album
        unique_results = []
        seen = set()
        for r in all_results:
            try:
                key = (r.get('title', '').lower(), r.get('artist', '').lower(), r.get('album', '').lower())
                if key not in seen and r.get('title'):
                    seen.add(key)
                    unique_results.append(r)
            except Exception as e:
                print(f"Error processing result: {e}")
                continue
        
        # Calculate ranking scores AFTER we have all unique results
        # This allows us to find the earliest release date
        for r in unique_results:
            try:
                r['rank_score'] = rank_result(r, title, artist, filename, all_results=unique_results)
            except Exception as e:
                print(f"Error ranking result: {e}")
                r['rank_score'] = 0.5
        
        # Sort by ranking score (higher is better)
        try:
            unique_results.sort(key=lambda x: x.get('rank_score', 0), reverse=True)
        except Exception as e:
            print(f"Error sorting results: {e}")
        
        # Download cover art for best match
        best_match = unique_results[0] if unique_results else None
        if best_match and best_match.get('cover_url'):
            cover_data = download_cover_art(best_match['cover_url'])
            if cover_data:
                best_match['cover'] = cover_data
        
        # Return top 5 results
        return jsonify({
            'results': unique_results[:5],
            'count': len(unique_results),
            'best_match': best_match,
            'sources_checked': len(search_functions),
            'errors': errors if errors else None
        })
    else:
        error_msg = 'No metadata found in any source'
        if errors:
            error_msg += f' (Errors: {"; ".join(errors[:2])})'
        return jsonify({'error': error_msg, 'details': errors}), 404

@app.route('/api/clean-metadata', methods=['POST'])
def clean_metadata():
    """Remove spam/commercial metadata from files"""
    data = request.json
    filepaths = data.get('filepaths', [])
    
    results = []
    errors = []
    
    for filepath in filepaths:
        try:
            if not os.path.exists(filepath):
                raise Exception('File not found')
            
            audio = MP3(filepath, ID3=ID3)
            
            cleaned = []
            
            # Remove or clean spam fields
            if 'COMM' in audio and audio['COMM']:  # Comments
                if is_spam_metadata(str(audio['COMM'][0].text[0]) if audio['COMM'][0].text else ''):
                    del audio['COMM']
                    cleaned.append('comment')
            
            if 'TPUB' in audio:  # Publisher
                if is_spam_metadata(str(audio['TPUB'])):
                    del audio['TPUB']
                    cleaned.append('publisher')
            
            if 'TCOP' in audio:  # Copyright
                if is_spam_metadata(str(audio['TCOP'])):
                    del audio['TCOP']
                    cleaned.append('copyright')
            
            if 'WXXX' in audio:  # URLs
                del audio['WXXX']
                cleaned.append('url')
            
            if 'WOAR' in audio:  # Artist URL
                del audio['WOAR']
                cleaned.append('artist_url')
            
            # Clean title/artist/album if they have spam
            if 'TIT2' in audio and is_spam_metadata(str(audio['TIT2'])):
                # Don't delete, but mark for user review
                cleaned.append('title_has_spam')
            
            if 'TPE1' in audio and is_spam_metadata(str(audio['TPE1'])):
                cleaned.append('artist_has_spam')
            
            if cleaned:
                audio.save()
            
            results.append({
                'filepath': filepath,
                'cleaned_fields': cleaned,
                'status': 'cleaned' if cleaned else 'clean'
            })
            
        except Exception as e:
            errors.append({
                'filepath': filepath,
                'error': str(e)
            })
    
    return jsonify({
        'success': len([r for r in results if r['status'] == 'cleaned']),
        'already_clean': len([r for r in results if r['status'] == 'clean']),
        'failed': len(errors),
        'results': results,
        'errors': errors
    })

@app.route('/api/update-tags', methods=['POST'])
def update_tags():
    """Update MP3 tags for a file"""
    data = request.json
    filepath = data.get('filepath', '')
    new_tags = data.get('tags', {})
    
    
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'Invalid file path'}), 400
    
    try:
        from mutagen.id3 import TPE2, TCOM, TPUB, TRCK, TPOS, COMM, TCOP, TENC
        
        # Load the MP3 file
        audio = MP3(filepath, ID3=ID3)
        
        # Add ID3 tag if it doesn't exist
        try:
            audio.add_tags()
        except Exception:
            pass  # Tags already exist
        
        # Update basic tags
        if 'title' in new_tags:
            if new_tags['title']:
                audio['TIT2'] = TIT2(encoding=3, text=new_tags['title'])
            elif 'TIT2' in audio:
                del audio['TIT2']
        
        if 'artist' in new_tags:
            if new_tags['artist']:
                audio['TPE1'] = TPE1(encoding=3, text=new_tags['artist'])
            elif 'TPE1' in audio:
                del audio['TPE1']
        
        if 'album' in new_tags:
            if new_tags['album']:
                audio['TALB'] = TALB(encoding=3, text=new_tags['album'])
            elif 'TALB' in audio:
                del audio['TALB']
        
        if 'year' in new_tags:
            if new_tags['year']:
                audio['TDRC'] = TDRC(encoding=3, text=str(new_tags['year']))
            elif 'TDRC' in audio:
                del audio['TDRC']
        
        if 'genre' in new_tags:
            if new_tags['genre']:
                audio['TCON'] = TCON(encoding=3, text=new_tags['genre'])
            elif 'TCON' in audio:
                del audio['TCON']
        
        # Update additional tags
        if 'album_artist' in new_tags:
            if new_tags['album_artist']:
                audio['TPE2'] = TPE2(encoding=3, text=new_tags['album_artist'])
            elif 'TPE2' in audio:
                del audio['TPE2']
        
        if 'composer' in new_tags:
            if new_tags['composer']:
                audio['TCOM'] = TCOM(encoding=3, text=new_tags['composer'])
            elif 'TCOM' in audio:
                del audio['TCOM']
        
        if 'comment' in new_tags:
            if new_tags['comment']:
                audio['COMM'] = COMM(encoding=3, lang='eng', desc='', text=new_tags['comment'])
            elif 'COMM' in audio:
                del audio['COMM']
        
        # Update cover art if provided
        if 'cover' in new_tags and new_tags['cover']:
            try:
                # Decode base64 cover data
                cover_data = base64.b64decode(new_tags['cover'])
                
                # Determine image format
                mime_type = 'image/jpeg'
                if cover_data[:4] == b'\x89PNG':
                    mime_type = 'image/png'
                
                # CRITICAL: Remove ALL existing APIC frames first!
                # Mutagen sometimes has issues updating existing frames
                apic_keys_to_remove = [key for key in audio.keys() if key.startswith('APIC')]
                for key in apic_keys_to_remove:
                    del audio[key]
                
                # Now add the new cover art with explicit key
                audio['APIC:Cover'] = APIC(
                    encoding=3,
                    mime=mime_type,
                    type=3,  # Cover (front)
                    desc='Cover',
                    data=cover_data
                )
                
            except Exception as e:
                print(f"Cover save error: {e}")
        
        # Save changes - use v2_version=3 for better compatibility
        try:
            audio.save(v2_version=3)
            
            # Force filesystem sync to ensure data is written to disk
            import subprocess
            try:
                subprocess.run(['sync'], check=False, capture_output=True)
            except:
                pass  # sync command might not be available
                
        except Exception as save_error:
            error_msg = str(save_error)
            # Check for common corrupt file errors
            if 'sync to MPEG frame' in error_msg or 'MPEG' in error_msg:
                return jsonify({
                    'error': f'Corrupt MP3 file: {os.path.basename(filepath)}. This file cannot be saved. Try re-downloading or fixing it with an audio repair tool.'
                }), 400
            else:
                raise  # Re-raise other errors
        
        # Return updated file info
        updated_info = get_file_info(filepath)
        return jsonify(updated_info)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clean-filenames', methods=['POST'])
def clean_filenames():
    """Remove track number prefixes from filenames"""
    data = request.json
    filepaths = data.get('filepaths', [])
    
    results = []
    errors = []
    
    for filepath in filepaths:
        try:
            if not os.path.exists(filepath):
                raise Exception('File not found')
            
            directory = os.path.dirname(filepath)
            old_filename = os.path.basename(filepath)
            new_filename = clean_filename(old_filename)
            
            # Only rename if the filename changed
            if old_filename != new_filename:
                new_filepath = os.path.join(directory, new_filename)
                
                # Check if target filename already exists
                if os.path.exists(new_filepath):
                    raise Exception(f'File already exists: {new_filename}')
                
                # Rename the file
                os.rename(filepath, new_filepath)
                
                results.append({
                    'old_filepath': filepath,
                    'new_filepath': new_filepath,
                    'old_filename': old_filename,
                    'new_filename': new_filename,
                    'status': 'renamed'
                })
            else:
                results.append({
                    'old_filepath': filepath,
                    'new_filepath': filepath,
                    'old_filename': old_filename,
                    'new_filename': new_filename,
                    'status': 'unchanged'
                })
        except Exception as e:
            errors.append({
                'filepath': filepath,
                'error': str(e)
            })
    
    return jsonify({
        'success': len([r for r in results if r['status'] == 'renamed']),
        'unchanged': len([r for r in results if r['status'] == 'unchanged']),
        'failed': len(errors),
        'results': results,
        'errors': errors
    })

@app.route('/api/batch-update', methods=['POST'])
def batch_update():
    """Update multiple MP3 files at once"""
    data = request.json
    updates = data.get('updates', [])
    
    results = []
    errors = []
    
    for update in updates:
        filepath = update.get('filepath')
        tags = update.get('tags')
        
        try:
            # Load and update file
            audio = MP3(filepath, ID3=ID3)
            
            try:
                audio.add_tags()
            except Exception:
                pass
            
            # Update tags
            if tags.get('title'):
                audio['TIT2'] = TIT2(encoding=3, text=tags['title'])
            if tags.get('artist'):
                audio['TPE1'] = TPE1(encoding=3, text=tags['artist'])
            if tags.get('album'):
                audio['TALB'] = TALB(encoding=3, text=tags['album'])
            if tags.get('year'):
                audio['TDRC'] = TDRC(encoding=3, text=str(tags['year']))
            if tags.get('genre'):
                audio['TCON'] = TCON(encoding=3, text=tags['genre'])
            
            # Update cover art if provided
            if tags.get('cover'):
                try:
                    # Decode base64 cover data
                    cover_data = base64.b64decode(tags['cover'])
                    
                    # Determine image format
                    mime_type = 'image/jpeg'
                    if cover_data[:4] == b'\x89PNG':
                        mime_type = 'image/png'
                    
                    # Add/update APIC frame
                    audio['APIC:'] = APIC(
                        encoding=3,
                        mime=mime_type,
                        type=3,  # Cover (front)
                        desc='Cover',
                        data=cover_data
                    )
                except Exception as e:
                    print(f"Cover save error: {e}")
            
            audio.save()
            
            results.append({
                'filepath': filepath,
                'status': 'success'
            })
        except Exception as e:
            errors.append({
                'filepath': filepath,
                'error': str(e)
            })
    
    return jsonify({
        'success': len(results),
        'failed': len(errors),
        'results': results,
        'errors': errors
    })

@app.route('/file/<path:filename>')
def serve_file(filename):
    """Serve audio file for playback"""
    print("\n" + "="*80)
    print("🎵 AUDIO FILE REQUEST")
    print("="*80)
    
    global current_folder
    
    print(f"📁 Current folder: {current_folder}")
    print(f"📄 Requested filename (raw): {filename}")
    
    if not current_folder:
        print("❌ ERROR: No folder selected")
        return "No folder selected - please scan a folder first", 404
    
    try:
        # Decode URL-encoded filename
        from urllib.parse import unquote
        filename_decoded = unquote(filename)
        print(f"📄 Decoded filename: {filename_decoded}")
        
        file_path = os.path.join(current_folder, filename_decoded)
        print(f"📂 Full file path: {file_path}")
        print(f"✅ File exists: {os.path.exists(file_path)}")
        
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            print(f"📊 File size: {file_size:,} bytes ({file_size/1024/1024:.2f} MB)")
        
        if not os.path.exists(file_path):
            print(f"❌ ERROR: File not found!")
            print(f"Looking in directory: {current_folder}")
            print(f"Files in directory:")
            try:
                for f in os.listdir(current_folder)[:10]:
                    print(f"  - {f}")
            except:
                pass
            return f"File not found: {filename_decoded}", 404
        
        print(f"✅ Serving file with mimetype: audio/mpeg")
        print("="*80 + "\n")
        
        return send_file(
            file_path, 
            mimetype='audio/mpeg',
            as_attachment=False,
            conditional=True
        )
    except Exception as e:
        print(f"❌ ERROR serving file: {e}")
        import traceback
        print("Stack trace:")
        traceback.print_exc()
        print("="*80 + "\n")
        return str(e), 500


def _eager_load_shazam_status():
    """Load status from file at startup so first request after Flask restart has data."""
    try:
        from shazam_cache import load_status_cache
        cached = load_status_cache()
        if cached and (cached.get('shazam_count', 0) > 0 or cached.get('have_locally') or cached.get('to_download')):
            app._shazam_sync_status = cached
    except Exception:
        pass


_eager_load_shazam_status()

if __name__ == '__main__':
    app.run(debug=True, port=5002, host='127.0.0.1', threaded=True)

