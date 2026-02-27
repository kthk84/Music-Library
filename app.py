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
from typing import Dict, List, Optional, Tuple
import base64
import logging
import shlex
import shutil
import threading

from app_paths import get_project_root_for_data, get_resource_root

# Logger for Soundeo star/unstar: file only (no console), so agents can read it and users aren't spammed.
def _get_soundeo_star_logger():
    _log = logging.getLogger("soundeo_star")
    if _log.handlers:
        return _log
    _log.setLevel(logging.INFO)
    _log.propagate = False
    try:
        _dir = get_project_root_for_data(__file__)
        _path = os.path.join(_dir, "soundeo_star.log")
        _h = logging.FileHandler(_path, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(_h)
    except Exception:
        pass  # no file logging; agent can still inspect code
    return _log

_soundeo_star_log = _get_soundeo_star_logger()


def _get_search_favorite_logger():
    """Logger for Search (single/batch): what starred state we see on Soundeo and what we write to status. File only."""
    _log = logging.getLogger("search_favorite")
    if _log.handlers:
        return _log
    _log.setLevel(logging.INFO)
    _log.propagate = False
    try:
        _dir = get_project_root_for_data(__file__)
        _path = os.path.join(_dir, "search_favorite.log")
        _h = logging.FileHandler(_path, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(_h)
    except Exception:
        pass
    return _log


_search_favorite_log = _get_search_favorite_logger()


def _get_soundeo_download_logger():
    """Logger for Soundeo download flow (file only) for analysis and validation."""
    _log = logging.getLogger("soundeo_download")
    if _log.handlers:
        return _log
    _log.setLevel(logging.INFO)
    _log.propagate = False
    try:
        _dir = get_project_root_for_data(__file__)
        _path = os.path.join(_dir, "soundeo_download.log")
        _h = logging.FileHandler(_path, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(_h)
    except Exception:
        pass
    return _log


_soundeo_download_log = _get_soundeo_download_logger()


def _get_soundeo_download_log_path():
    """Path to soundeo_download.log (same dir as app.py, or Application Support when frozen) for UI / download-log API."""
    return os.path.join(get_project_root_for_data(__file__), "soundeo_download.log")


_APP_DIR = get_resource_root() or os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(_APP_DIR, 'templates'), static_folder=os.path.join(_APP_DIR, 'static'))
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max request size

# Global variable to store current folder path
current_folder = None

# AcoustID API for audio fingerprinting (free tier)
ACOUSTID_API_KEY = "8XaBELgH"  # Public demo key - replace with your own from acoustid.org
ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"

# MusicBrainz API (free, no key needed)
MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/"
MUSICBRAINZ_HEADERS = {
    'User-Agent': 'SoundBridge/1.0 (https://github.com/kthk84/Music-Library)'
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

def _normalize_word(w: str) -> str:
    """Strip leading/trailing punctuation so 'dox,' matches 'dox' and '(original' / 'mix)' normalize."""
    w = (w or "").strip()
    while w and w[-1] in ".,;:&()":
        w = w[:-1].strip()
    while w and w[0] in ".,;:&()":
        w = w[1:].strip()
    return w


def similarity_score(str1: str, str2: str) -> float:
    """Calculate similarity between two strings (0-1).

    Uses word-overlap (Jaccard) with normalized words (strip punctuation)
    so "Dox, DvirNuns" and "DvirNuns & Dox" share words dox, dvirnuns.
    """
    if not str1 or not str2:
        return 0.0

    s1 = str1.lower().strip()
    s2 = str2.lower().strip()

    if s1 == s2:
        return 1.0

    words1 = set(_normalize_word(w) for w in s1.split() if _normalize_word(w))
    words2 = set(_normalize_word(w) for w in s2.split() if _normalize_word(w))
    if not words1 or not words2:
        return 0.0

    overlap = len(words1 & words2)
    union = len(words1 | words2)
    jaccard = overlap / union if union else 0.0

    shorter, longer = (len(s1), len(s2)) if len(s1) <= len(s2) else (len(s2), len(s1))
    length_ratio = shorter / longer if longer else 0.0

    if s1 in s2 or s2 in s1:
        return min(0.85, jaccard * 0.5 + length_ratio * 0.5)

    return jaccard

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
    from config_shazam import load_config, get_config_path, get_soundeo_cookies_path, get_destination_folders_raw, get_soundeo_download_folder
    settings = dict(load_config())
    settings['soundeo_cookies_path_resolved'] = get_soundeo_cookies_path()
    settings['destination_folders_raw'] = get_destination_folders_raw()
    settings['soundeo_download_folder'] = get_soundeo_download_folder()
    settings['config_path'] = get_config_path()
    settings.update(_get_soundeo_chrome_profile_info())
    status = _get_best_available_status()
    status['compare_running'] = getattr(app, '_shazam_compare_running', False)
    resp = jsonify({'settings': settings, 'status': status})
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


@app.route('/api/app-state', methods=['GET'])
def get_app_state():
    """Return app-wide state to restore after load/refresh (last folder path, etc.)."""
    from shazam_cache import load_app_state
    return jsonify(load_app_state())


def _get_soundeo_chrome_profile_info():
    """Return effective Chrome profile path and directory (for UI feedback)."""
    try:
        from config_shazam import get_soundeo_browser_profile_dir, get_soundeo_browser_profile_directory
        user_data_dir = get_soundeo_browser_profile_dir()
        profile_dir = get_soundeo_browser_profile_directory()
        return {"soundeo_chrome_user_data_dir_effective": user_data_dir, "soundeo_chrome_profile_directory_effective": profile_dir or "(default)"}
    except Exception:
        return {}


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Return Shazam-Soundeo sync settings (same shape as bootstrap settings for fallback)."""
    from config_shazam import load_config, get_config_path, get_soundeo_cookies_path, get_destination_folders_raw, get_soundeo_download_folder
    out = dict(load_config())
    out['soundeo_cookies_path_resolved'] = get_soundeo_cookies_path()
    out['destination_folders_raw'] = get_destination_folders_raw()
    out['soundeo_download_folder'] = get_soundeo_download_folder()
    out['config_path'] = get_config_path()
    out.update(_get_soundeo_chrome_profile_info())
    return jsonify(out)


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
    if 'search_all_use_http' in data:
        config['search_all_use_http'] = bool(data['search_all_use_http'])
    if 'stream_to_ui' in data:
        config['stream_to_ui'] = bool(data['stream_to_ui'])
    if 'soundeo_chrome_user_data_dir' in data:
        config['soundeo_chrome_user_data_dir'] = str(data['soundeo_chrome_user_data_dir'] or '').strip()
    if 'soundeo_chrome_profile_directory' in data:
        config['soundeo_chrome_profile_directory'] = str(data['soundeo_chrome_profile_directory'] or '').strip()
    if 'soundeo_use_running_chrome' in data:
        config['soundeo_use_running_chrome'] = bool(data['soundeo_use_running_chrome'])
    if 'soundeo_chrome_debugger_address' in data:
        config['soundeo_chrome_debugger_address'] = str(data['soundeo_chrome_debugger_address'] or '127.0.0.1:9222').strip()
    if 'soundeo_download_folder' in data:
        config['soundeo_download_folder'] = str(data.get('soundeo_download_folder') or '').strip()
    save_config(config)
    out = dict(load_config())
    out.update(_get_soundeo_chrome_profile_info())
    return jsonify(out)


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


def _track_key_norm(t: Dict) -> tuple:
    """Normalized (artist, title) for deduplication and set lookups."""
    return (t.get('artist', '').strip().lower(), t.get('title', '').strip().lower())


def _dedupe_tracks_by_key(tracks: List[Dict], prefer_with_filepath: bool = False) -> List[Dict]:
    """Deduplicate by normalized (artist, title). If prefer_with_filepath, keep the one that has filepath."""
    by_key = {}
    for t in tracks:
        k = _track_key_norm(t)
        if k not in by_key:
            by_key[k] = t
        elif prefer_with_filepath and t.get('filepath') and not by_key[k].get('filepath'):
            by_key[k] = t
    return list(by_key.values())


def _apply_skip_list_to_status(out: Dict) -> Dict:
    """Enforce skip list on status: remove skipped from to_download, set skipped_tracks from skip list.
    Single source of truth for skipped is shazam_skip_list.json; this ensures status never shows skipped tracks in to_download."""
    from shazam_cache import load_skip_list
    skipped_set = load_skip_list()
    if not skipped_set:
        return out
    out = dict(out)
    to_download = list(out.get('to_download') or [])
    to_dl = [t for t in to_download if ((t.get('artist') or '').strip().lower(), (t.get('title') or '').strip().lower()) not in skipped_set]
    all_tracks = (out.get('to_download') or []) + (out.get('have_locally') or []) + (out.get('skipped_tracks') or [])
    by_key = {}
    for t in all_tracks:
        k = ((t.get('artist') or '').strip().lower(), (t.get('title') or '').strip().lower())
        if k not in by_key:
            by_key[k] = t
    skipped_tracks = [by_key[k] for k in by_key if k in skipped_set]
    out['to_download'] = to_dl
    out['to_download_count'] = len(to_dl)
    out['skipped_tracks'] = skipped_tracks
    return out


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
            to_dl = _dedupe_tracks_by_key(to_dl)
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
            to_download = _dedupe_tracks_by_key(to_download)
            skipped_tracks = [
                {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                for t in to_download_raw
                if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped
            ]
            to_dl_set = {_track_key_norm(s) for s in to_download}
            app._shazam_scan_progress = {}  # clear file-scan progress so UI shows match phase
            # Sort by shazamed_at desc so live-updated rows match final order
            sorted_tracks = sorted(shazam_tracks, key=lambda t: (t.get('shazamed_at') or 0), reverse=True)
            total_tracks = len(sorted_tracks)
            have_by_key = {}
            for idx, s in enumerate(sorted_tracks):
                if getattr(app, '_shazam_compare_cancel_requested', False):
                    app._shazam_sync_status = {'message': 'Compare cancelled.'}
                    app._shazam_scan_progress = {}
                    app._shazam_compare_running = False
                    return
                k = _track_key_norm(s)
                current_key = f"{s['artist']} - {s['title']}"
                # Emit partial status so frontend can live-update rows and show spinner on current
                partial = {
                    'shazam_count': len(shazam_tracks),
                    'local_count': len(local_tracks),
                    'to_download_count': len(to_download),
                    'to_download': to_download,
                    'have_locally': list(have_by_key.values()),
                    'folder_stats': _folder_scan_stats(folder_paths, local_tracks, list(have_by_key.values())),
                    'skipped_tracks': skipped_tracks,
                    'match_progress': {
                        'running': True,
                        'current': idx,
                        'total': total_tracks,
                        'current_key': current_key,
                    },
                }
                missing = [p for p in configured_folders if p not in folder_paths]
                if missing:
                    partial['folder_warning'] = f'{len(missing)} folder(s) not found or not accessible (not scanned): ' + ', '.join(os.path.basename(p.rstrip(os.sep)) or p for p in missing[:5])
                _merge_preserved_urls_into_status(partial)
                app._shazam_sync_status = partial
                if k in to_dl_set:
                    continue
                item = {'artist': s['artist'], 'title': s['title']}
                if s.get('shazamed_at') is not None:
                    item['shazamed_at'] = s['shazamed_at']
                match, match_score = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                if match and match.get('filepath'):
                    item['filepath'] = match['filepath']
                if match_score is not None:
                    item['match_score'] = match_score
                if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
                    have_by_key[k] = item
            have_locally = list(have_by_key.values())
            folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
            status = {
                'shazam_count': len(shazam_tracks),
                'local_count': len(local_tracks),
                'to_download_count': len(to_download),
                'to_download': to_download,
                'have_locally': have_locally,
                'folder_stats': folder_stats,
                'skipped_tracks': skipped_tracks,
            }
            missing = [p for p in configured_folders if p not in folder_paths]
            if missing:
                status['folder_warning'] = f'{len(missing)} folder(s) not found or not accessible (not scanned): ' + ', '.join(os.path.basename(p.rstrip(os.sep)) or p for p in missing[:5])
        _merge_preserved_urls_into_status(status)
        app._shazam_sync_status = status
        save_status_cache(status)
    except Exception:
        app._shazam_sync_status = {'error': 'Compare failed'}
    finally:
        app._shazam_compare_running = False
        try:
            _start_next_single_search()
        except Exception:
            pass


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
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
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
        to_dl = _dedupe_tracks_by_key(to_dl)
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
    if cache_valid:
        local_tracks = local_scan.get('tracks', [])
        skipped = load_skip_list()
        to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
        to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        to_download = _dedupe_tracks_by_key(to_download)
        skipped_tracks = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                          for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
        to_dl_set = {_track_key_norm(s) for s in to_download}
        have_by_key = {}
        for s in shazam_tracks:
            k = _track_key_norm(s)
            if k in to_dl_set:
                continue
            item = {'artist': s['artist'], 'title': s['title']}
            if s.get('shazamed_at') is not None:
                item['shazamed_at'] = s['shazamed_at']
            match, match_score = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
            if match and match.get('filepath'):
                item['filepath'] = match['filepath']
            if match_score is not None:
                item['match_score'] = match_score
            if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
                have_by_key[k] = item
        have_locally = list(have_by_key.values())
        folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
        status = {
            'shazam_count': len(shazam_tracks),
            'local_count': len(local_tracks),
            'to_download_count': len(to_download),
            'to_download': to_download,
            'have_locally': have_locally,
            'folder_stats': folder_stats,
            'skipped_tracks': skipped_tracks,
        }
        _merge_preserved_urls_into_status(status)
        app._shazam_sync_status = status
        from shazam_cache import save_status_cache
        save_status_cache(status)
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
    to_dl = _dedupe_tracks_by_key(to_dl)
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
        to_dl = _dedupe_tracks_by_key(to_dl)
        skipped_tracks = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                         for t in shazam_tracks if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
        out = {'shazam_count': len(shazam_tracks), 'local_count': 0, 'to_download_count': len(to_dl),
               'to_download': to_dl, 'have_locally': [], 'skipped_tracks': skipped_tracks, 'folder_stats': [], 'error': 'No destination folders configured.'}
        old = load_status_cache()
        if old:
            if old.get('urls'):
                out['urls'] = dict(old['urls'])
            if old.get('starred'):
                out['starred'] = dict(old['starred'])
            if old.get('not_found'):
                out['not_found'] = dict(old['not_found'])
            if old.get('soundeo_match_scores'):
                out['soundeo_match_scores'] = dict(old['soundeo_match_scores'])
            if old.get('track_ids'):
                out['track_ids'] = dict(old['track_ids'])
            if old.get('search_outcomes'):
                out['search_outcomes'] = list(old['search_outcomes'])
        return out
    local_scan = load_local_scan_cache()
    if not local_scan_cache_valid(local_scan, folder_paths):
        return None
    local_tracks = local_scan.get('tracks', [])
    skipped = load_skip_list()
    to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
    to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
    to_download = _dedupe_tracks_by_key(to_download)
    skipped_tracks = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                      for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
    to_dl_set = {_track_key_norm(s) for s in to_download}
    have_by_key = {}
    for s in shazam_tracks:
        k = _track_key_norm(s)
        if k in to_dl_set:
            continue
        item = {'artist': s['artist'], 'title': s['title']}
        if s.get('shazamed_at') is not None:
            item['shazamed_at'] = s['shazamed_at']
        match, match_score = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
        if match and match.get('filepath'):
            item['filepath'] = match['filepath']
        if match_score is not None:
            item['match_score'] = match_score
        if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
            have_by_key[k] = item
    have_locally = list(have_by_key.values())
    folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
    out = {'shazam_count': len(shazam_tracks), 'local_count': len(local_tracks), 'to_download_count': len(to_download),
           'to_download': to_download, 'have_locally': have_locally, 'folder_stats': folder_stats, 'skipped_tracks': skipped_tracks}
    old = load_status_cache()
    if old:
        if old.get('urls'):
            out['urls'] = dict(old['urls'])
        if old.get('starred'):
            out['starred'] = dict(old['starred'])
        if old.get('dismissed_manual_check'):
            out['dismissed_manual_check'] = list(old['dismissed_manual_check'])
        if old.get('soundeo_titles'):
            out['soundeo_titles'] = dict(old['soundeo_titles'])
        if old.get('soundeo_match_scores'):
            out['soundeo_match_scores'] = dict(old['soundeo_match_scores'])
        if old.get('dismissed'):
            out['dismissed'] = dict(old['dismissed'])
        if old.get('not_found'):
            out['not_found'] = dict(old['not_found'])
        if old.get('track_ids'):
            out['track_ids'] = dict(old['track_ids'])
        if old.get('search_outcomes'):
            out['search_outcomes'] = list(old['search_outcomes'])
    return out


def _merge_preserved_urls_into_status(status: Dict) -> None:
    """Merge existing favorited/synced URLs, starred, dismissed, search_outcomes, etc. from status cache file into status. Mutates status.
    search_outcomes is the single source of truth for urls/not_found (dots); always preserve so compare never drops purple/orange state."""
    from shazam_cache import load_status_cache
    old = load_status_cache()
    if old:
        if old.get('urls'):
            status['urls'] = {**(status.get('urls') or {}), **old['urls']}
        if old.get('starred'):
            status['starred'] = {**(status.get('starred') or {}), **old['starred']}
        if old.get('dismissed_manual_check'):
            status['dismissed_manual_check'] = list(old['dismissed_manual_check'])
        if old.get('soundeo_titles'):
            status['soundeo_titles'] = {**(status.get('soundeo_titles') or {}), **old['soundeo_titles']}
        if old.get('soundeo_match_scores'):
            status['soundeo_match_scores'] = {**(status.get('soundeo_match_scores') or {}), **old['soundeo_match_scores']}
        if old.get('dismissed'):
            status['dismissed'] = {**(status.get('dismissed') or {}), **old['dismissed']}
        if old.get('track_ids'):
            status['track_ids'] = {**(status.get('track_ids') or {}), **old['track_ids']}
        if old.get('download_last_run'):
            status['download_last_run'] = dict(old['download_last_run'])
        # not_found: replace from file only (single source of truth; never merge in-memory)
        status['not_found'] = dict(old.get('not_found') or {})
        # search_outcomes: preserve so save_status_cache never writes a version that loses purple/orange dots
        if old.get('search_outcomes'):
            status['search_outcomes'] = list(old['search_outcomes'])


def _status_is_stale(status: Optional[Dict]) -> bool:
    """True if status doesn't match current shazam_cache (e.g. Fetch added more tracks)."""
    if not status:
        return True
    from shazam_cache import load_shazam_cache
    current = load_shazam_cache()
    current_count = len(current) if current else 0
    return status.get('shazam_count', 0) != current_count


def _add_starred_lowercase_aliases(status: Dict) -> Dict:
    """Return a copy of status with lowercase + deep-normalized keys added to urls, starred, soundeo_titles so frontend matches Shazam vs Soundeo key variants.
    not_found: only add lowercase, never keyDeep — so orange (searched not found) is per exact track and grey dots appear for never-searched."""
    out = dict(status)
    for m in ('urls', 'starred', 'soundeo_titles', 'dismissed', 'not_found'):
        if m not in out or not out[m]:
            continue
        data = out[m]
        extra = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            lk = k.lower()
            if lk != k and lk not in data:
                extra[lk] = v
            if m != 'not_found':
                dk = _deep_norm_key(k)
                if dk != k and dk not in data:
                    extra[dk] = v
        if extra:
            out[m] = {**data, **extra}
    return out


def _sanitize_have_locally_filepaths(status: Dict) -> Dict:
    """Ensure have_locally only contains entries whose filepath still exists on disk.
    Entries with missing files are moved to to_download. Mutates and returns a copy; persists if changed."""
    from shazam_cache import save_status_cache
    out = dict(status)
    have = list(out.get('have_locally') or [])
    to_dl = list(out.get('to_download') or [])
    valid = []
    moved = []
    for h in have:
        fp = h.get('filepath') or ''
        if fp and os.path.exists(fp):
            valid.append(h)
        else:
            # Missing file or no filepath: treat as not having locally
            moved.append({'artist': h['artist'], 'title': h['title'], **({} if h.get('shazamed_at') is None else {'shazamed_at': h['shazamed_at']})})
    if not moved:
        return out
    out['have_locally'] = valid
    out['to_download'] = _dedupe_tracks_by_key(to_dl + moved)
    out['to_download_count'] = len(out['to_download'])
    app._shazam_sync_status = out
    save_status_cache(out)
    return out


def _get_best_available_status():
    """Return best available status. Prefer file over in-memory when file has full context (urls/search_outcomes) so first load after restart always shows purple/orange dots. Dots are derived from file search_outcomes when present."""
    from shazam_cache import (
        load_status_cache,
        save_status_cache,
        load_shazam_cache,
        load_skip_list,
        get_urls_and_not_found_from_log,
        _replay_search_outcomes,
    )

    def _apply_dot_state_from_file(out: Dict) -> Dict:
        """Overwrite urls and not_found from the cache file's search_outcomes (single source of truth). load_status_cache() already merges from .bak when file lacks context."""
        out = dict(out)
        file_status = load_status_cache()
        log = (file_status or {}).get("search_outcomes") or []
        if log:
            urls, nf = _replay_search_outcomes(log, out.get("urls"))
            out["urls"] = urls
            out["not_found"] = nf
        else:
            urls, nf = get_urls_and_not_found_from_log(out)
            out["urls"] = urls
            out["not_found"] = nf
        return out

    def _apply_dot_state_from_log(out: Dict) -> Dict:
        """Apply urls/not_found from the given status's search_outcomes (or keep existing). Used when we already have merged partial from file."""
        out = dict(out)
        urls, nf = get_urls_and_not_found_from_log(out)
        out["urls"] = urls
        out["not_found"] = nf
        return out

    cached = load_status_cache()
    has_cached_data = cached and (cached.get('shazam_count', 0) > 0 or cached.get('have_locally') or cached.get('to_download'))
    file_has_dot_state = bool((cached or {}).get('search_outcomes') or (cached or {}).get('urls'))
    if has_cached_data and file_has_dot_state and not _status_is_stale(cached):
        out = _sanitize_have_locally_filepaths(dict(cached))
        out = _apply_skip_list_to_status(out)
        app._shazam_sync_status = out
        out = _apply_dot_state_from_file(out)
        return _add_starred_lowercase_aliases(out)
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status and not _status_is_stale(app._shazam_sync_status):
        out = _sanitize_have_locally_filepaths(dict(app._shazam_sync_status))
        out = _apply_skip_list_to_status(out)
        out = _apply_dot_state_from_file(out)
        return _add_starred_lowercase_aliases(out)
    if has_cached_data and not _status_is_stale(cached):
        out = _sanitize_have_locally_filepaths(dict(cached))
        out = _apply_skip_list_to_status(out)
        app._shazam_sync_status = out
        out = _apply_dot_state_from_file(out)
        return _add_starred_lowercase_aliases(out)
    rebuilt = _rebuild_status_from_caches()
    if rebuilt:
        save_status_cache(rebuilt)
        out = _sanitize_have_locally_filepaths(dict(rebuilt))
        out = _apply_skip_list_to_status(out)
        app._shazam_sync_status = out
        out = _apply_dot_state_from_file(out)
        return _add_starred_lowercase_aliases(out)
    # Fallback: use cached status even if stale (e.g. Shazam count changed) - better than empty
    if has_cached_data:
        out = dict(cached)
        if _status_is_stale(cached):
            out['message'] = out.get('message') or 'Data may be outdated. Click Fetch Shazam or Compare to refresh.'
        out = _sanitize_have_locally_filepaths(out)
        out = _apply_skip_list_to_status(out)
        app._shazam_sync_status = out
        out = _apply_dot_state_from_file(out)
        return _add_starred_lowercase_aliases(out)
    shazam_tracks = load_shazam_cache()
    if shazam_tracks:
        skipped = load_skip_list()
        to_dl = [
            {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
            for t in shazam_tracks
            if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped
        ]
        to_dl = _dedupe_tracks_by_key(to_dl)
        skipped_tracks = [
            {'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
            for t in shazam_tracks
            if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped
        ]
        partial = {
            'shazam_count': len(shazam_tracks), 'local_count': 0, 'to_download_count': len(to_dl),
            'to_download': to_dl, 'have_locally': [], 'skipped_tracks': skipped_tracks,
            'folder_stats': [],
            'message': 'Local folders need rescan. Click Rescan to refresh.',
        }
        old = load_status_cache()
        if old:
            if old.get('urls'):
                partial['urls'] = dict(old['urls'])
            if old.get('starred'):
                partial['starred'] = dict(old['starred'])
            if old.get('dismissed_manual_check'):
                partial['dismissed_manual_check'] = list(old['dismissed_manual_check'])
            if old.get('soundeo_titles'):
                partial['soundeo_titles'] = dict(old['soundeo_titles'])
            if old.get('soundeo_match_scores'):
                partial['soundeo_match_scores'] = dict(old['soundeo_match_scores'])
            if old.get('dismissed'):
                partial['dismissed'] = dict(old['dismissed'])
            if old.get('not_found'):
                partial['not_found'] = dict(old['not_found'])
            if old.get('search_outcomes'):
                partial['search_outcomes'] = list(old['search_outcomes'])
        app._shazam_sync_status = partial
        save_status_cache(partial)
        out = _apply_skip_list_to_status(dict(partial))
        out = _apply_dot_state_from_log(out)
        return _add_starred_lowercase_aliases(out)
    return _add_starred_lowercase_aliases(_apply_skip_list_to_status({'shazam_count': 0, 'local_count': 0, 'to_download_count': 0, 'to_download': [], 'have_locally': [], 'folder_stats': []}))


@app.route('/api/shazam-sync/status', methods=['GET'])
def shazam_sync_status():
    """Return last comparison status. Never return empty when Shazam/local data exists."""
    compare_running = getattr(app, '_shazam_compare_running', False)
    out = _get_best_available_status()
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
    download_progress = getattr(app, '_shazam_download_progress', None)
    if download_progress:
        out['download_progress'] = download_progress
        out['download_log_path'] = _get_soundeo_download_log_path()
    # Download queue: pending keys + current (when running) so UI can show "Download queue: ..."
    download_pending = list(getattr(app, '_shazam_download_pending_queue', None) or [])
    if download_progress and download_progress.get('running'):
        current_queue = download_progress.get('queue') or []
        out['download_queue'] = list(current_queue) + download_pending
    else:
        out['download_queue'] = download_pending
    resp = jsonify(out)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return resp


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


# --- Temporary MP3 proxy for AIFF/WAV (instant playback + scrubbing) ---
_PROXY_MP3_DIR = os.environ.get('MP3_CLEANER_PROXY_DIR') or os.path.join(get_project_root_for_data(__file__), 'instance', 'audio_proxies')
_PROXY_TTL_SEC = 20 * 60  # 20 min since last access
_PROXY_CLEANUP_INTERVAL_SEC = 5 * 60  # every 5 min
_proxy_store: Dict[str, dict] = {}  # proxy_id -> { path, refcount, last_access }
_proxy_lock = threading.Lock()
_PROXY_CLEANUP_STARTED = False


def _shazam_resolve_path(allowed: List[str], dir_b64: str, file_param: str, path_b64: str):
    """Resolve to absolute path and check under allowed. Returns (path, None) or (None, (body, status))."""
    from urllib.parse import unquote
    if dir_b64 and file_param:
        dir_b64 = dir_b64.replace(' ', '+')
        try:
            raw = base64.b64decode(dir_b64)
            directory = os.path.abspath(raw.decode('utf-8'))
        except Exception:
            return None, ("Invalid dir", 400)
        filename = unquote(file_param)
        if not filename:
            return None, ("Invalid file", 400)
        path = os.path.normpath(os.path.join(directory, filename))
        path = os.path.abspath(path)
        if not path.startswith(directory + os.sep):
            return None, ("Invalid file", 400)
    elif path_b64:
        path_b64 = path_b64.replace(' ', '+')
        try:
            raw = base64.b64decode(path_b64)
            path = raw.decode('utf-8')
        except Exception:
            return None, ("Invalid path", 400)
        path = os.path.abspath(path)
    else:
        return None, ("Missing path or dir+file", 400)
    if not os.path.exists(path) or not os.path.isfile(path):
        return None, ("File not found", 404)
    if not allowed:
        return None, ("Access denied (no Sync folders configured)", 403)
    if not any(path == d or path.startswith(d + os.sep) for d in allowed):
        return None, ("Access denied", 403)
    return path, None


@app.route('/api/shazam-sync/prepare-proxy', methods=['POST'])
def shazam_prepare_proxy():
    """Generate or reuse a temporary 128k MP3 proxy for AIFF/WAV. Returns mp3_url and proxy_id for playback + release."""
    from config_shazam import get_destination_folders_raw
    from urllib.parse import unquote
    data = request.get_json() or {}
    dir_b64 = (data.get('dir_b64') or data.get('dir') or '').strip()
    file_param = (data.get('file') or '').strip()
    path_b64 = (data.get('path_b64') or data.get('path') or '').strip()
    allowed = [os.path.abspath(f).rstrip(os.sep) for f in get_destination_folders_raw() if f]
    path, err = _shazam_resolve_path(allowed, dir_b64, file_param, path_b64)
    if err:
        return err[0], err[1]
    ext = os.path.splitext(path)[1].lower()
    if ext not in ('.aiff', '.aif', '.wav'):
        return jsonify({'error': 'prepare-proxy is only for AIFF/WAV'}), 400
    ffmpeg_cmd = shutil.which('ffmpeg') or ('/opt/homebrew/bin/ffmpeg' if os.path.exists('/opt/homebrew/bin/ffmpeg') else None) or '/usr/local/bin/ffmpeg'
    if not ffmpeg_cmd or not os.path.isfile(ffmpeg_cmd):
        return jsonify({'error': 'ffmpeg not found. Install with e.g. brew install ffmpeg'}), 503
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return "File not found", 404
    proxy_id = hashlib.sha256(f"{path}:{mtime}".encode()).hexdigest()[:24]
    os.makedirs(_PROXY_MP3_DIR, exist_ok=True)
    mp3_path = os.path.join(_PROXY_MP3_DIR, f"{proxy_id}.mp3")
    now = time.time()
    with _proxy_lock:
        if proxy_id in _proxy_store:
            entry = _proxy_store[proxy_id]
            if os.path.isfile(entry['path']):
                entry['refcount'] += 1
                entry['last_access'] = now
                mp3_url = f"/api/shazam-sync/proxy/{proxy_id}.mp3"
                expires_at = now + _PROXY_TTL_SEC
                return jsonify({'mp3_url': mp3_url, 'proxy_id': proxy_id, 'expires_at': expires_at})
            else:
                del _proxy_store[proxy_id]
        # Generate
        try:
            subprocess.run(
                [ffmpeg_cmd, '-y', '-i', path, '-b:a', '128k', '-ar', '44100', '-ac', '2', '-f', 'mp3', mp3_path],
                capture_output=True,
                timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logging.warning("prepare-proxy ffmpeg failed: %s", e.stderr and e.stderr.decode()[:200])
            return jsonify({'error': 'Failed to generate MP3 proxy'}), 500
        except subprocess.TimeoutExpired:
            return jsonify({'error': 'Proxy generation timed out'}), 504
        except FileNotFoundError:
            return jsonify({'error': 'ffmpeg not found'}), 503
        if not os.path.isfile(mp3_path):
            return jsonify({'error': 'Proxy file was not created'}), 500
        _proxy_store[proxy_id] = {'path': mp3_path, 'refcount': 1, 'last_access': now}
    mp3_url = f"/api/shazam-sync/proxy/{proxy_id}.mp3"
    expires_at = now + _PROXY_TTL_SEC
    return jsonify({'mp3_url': mp3_url, 'proxy_id': proxy_id, 'expires_at': expires_at})


@app.route('/api/shazam-sync/proxy/<proxy_id>.mp3')
def shazam_proxy_mp3(proxy_id: str):
    """Serve temporary MP3 with Range support for scrubbing."""
    if not proxy_id or '..' in proxy_id or '/' in proxy_id or '\\' in proxy_id:
        return "Invalid proxy id", 400
    with _proxy_lock:
        if proxy_id not in _proxy_store:
            return "Proxy not found or expired", 404
        entry = _proxy_store[proxy_id]
        mp3_path = entry['path']
        entry['last_access'] = time.time()
    if not os.path.isfile(mp3_path):
        with _proxy_lock:
            if proxy_id in _proxy_store:
                del _proxy_store[proxy_id]
        return "File not found", 404
    return send_file(mp3_path, mimetype='audio/mpeg', as_attachment=False, conditional=True)


@app.route('/api/shazam-sync/release-proxy', methods=['POST'])
def shazam_release_proxy():
    """Release a proxy; delete file when refcount reaches 0."""
    data = request.get_json() or {}
    proxy_id = (data.get('proxy_id') or '').strip()
    if not proxy_id:
        return jsonify({'error': 'proxy_id required'}), 400
    with _proxy_lock:
        if proxy_id not in _proxy_store:
            return jsonify({'ok': True})
        entry = _proxy_store[proxy_id]
        entry['refcount'] -= 1
        if entry['refcount'] <= 0:
            path = entry['path']
            del _proxy_store[proxy_id]
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
    return jsonify({'ok': True})


def _proxy_cleanup_job():
    global _PROXY_CLEANUP_STARTED
    while True:
        time.sleep(_PROXY_CLEANUP_INTERVAL_SEC)
        now = time.time()
        with _proxy_lock:
            to_remove = [
                pid for pid, e in _proxy_store.items()
                if e['refcount'] <= 0 and (now - e['last_access']) >= _PROXY_TTL_SEC
            ]
            for pid in to_remove:
                path = _proxy_store[pid]['path']
                del _proxy_store[pid]
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    pass


def _start_proxy_cleanup():
    global _PROXY_CLEANUP_STARTED
    if _PROXY_CLEANUP_STARTED:
        return
    _PROXY_CLEANUP_STARTED = True
    t = threading.Thread(target=_proxy_cleanup_job, daemon=True)
    t.start()


@app.route('/api/shazam-sync/stream-file')
def shazam_stream_file():
    """Stream audio file for playback. Path must be under a destination folder.
    Accepts either: (path= base64 full path) or (dir= base64 directory, file= url-encoded filename).
    The dir+file form mirrors the MP3 Tags /file/ mechanic (folder + filename) and avoids path encoding issues."""
    from config_shazam import get_destination_folders_raw
    from urllib.parse import unquote
    import base64
    allowed = [os.path.abspath(f).rstrip(os.sep) for f in get_destination_folders_raw() if f]

    dir_b64 = (request.args.get('dir') or '').strip()
    file_param = (request.args.get('file') or '').strip()
    if dir_b64 and file_param:
        dir_b64 = dir_b64.replace(' ', '+')
        try:
            raw = base64.b64decode(dir_b64)
            directory = os.path.abspath(raw.decode('utf-8'))
        except Exception:
            return "Invalid dir", 400
        filename = unquote(file_param)
        if not filename:
            return "Invalid file", 400
        path = os.path.normpath(os.path.join(directory, filename))
        path = os.path.abspath(path)
        if not path.startswith(directory + os.sep):
            return "Invalid file", 400
    else:
        path_b64 = (request.args.get('path') or '').strip()
        if not path_b64:
            return "Missing path or dir+file", 400
        path_b64 = path_b64.replace(' ', '+')
        try:
            raw = base64.b64decode(path_b64)
            path = raw.decode('utf-8')
        except Exception:
            return "Invalid path", 400
        path = os.path.abspath(path)

    if not os.path.exists(path) or not os.path.isfile(path):
        logging.warning("stream-file: file not found %s", path)
        return "File not found", 404
    if not allowed:
        logging.warning("stream-file: no destination folders configured")
        return "Access denied (no Sync folders configured)", 403
    if not any(path == d or path.startswith(d + os.sep) for d in allowed):
        logging.warning("stream-file: path not under allowed folders: %s", path[:80])
        return "Access denied", 403
    ext = os.path.splitext(path)[1].lower()
    mimetypes = {'.mp3': 'audio/mpeg', '.aiff': 'audio/aiff', '.aif': 'audio/aiff', '.wav': 'audio/wav'}
    # AIFF/WAV in Chrome use prepare-proxy (temp MP3) for instant playback + scrubbing. stream-file serves raw only (MP3, WAV, Safari AIFF).
    mimetype = mimetypes.get(ext, 'application/octet-stream')
    return send_file(path, mimetype=mimetype, as_attachment=False, conditional=True)


@app.route('/api/shazam-sync/open-file-location', methods=['POST'])
def shazam_open_file_location():
    """Open the folder in the system file manager and reveal/select the file when a full path is given.
    Body: { dir_b64: "base64 dir path" } or { path_b64: "base64 full file path" }. Path must be under a destination folder."""
    from config_shazam import get_destination_folders_raw
    allowed = [os.path.abspath(f).rstrip(os.sep) for f in get_destination_folders_raw() if f]
    if not allowed:
        return jsonify({'error': 'No destination folders configured'}), 403
    data = request.get_json(silent=True) or {}
    dir_b64 = (data.get('dir_b64') or '').strip().replace(' ', '+')
    path_b64 = (data.get('path_b64') or '').strip().replace(' ', '+')
    folder = None
    file_path = None
    if dir_b64:
        try:
            raw = base64.b64decode(dir_b64)
            folder = os.path.abspath(raw.decode('utf-8'))
        except Exception:
            return jsonify({'error': 'Invalid path'}), 400
    elif path_b64:
        try:
            raw = base64.b64decode(path_b64)
            file_path = os.path.abspath(raw.decode('utf-8'))
            folder = os.path.dirname(file_path)
        except Exception:
            return jsonify({'error': 'Invalid path'}), 400
    else:
        return jsonify({'error': 'Missing dir_b64 or path_b64'}), 400
    if not os.path.isdir(folder):
        return jsonify({'error': 'Folder not found'}), 404
    if not any(folder == d or folder.startswith(d + os.sep) or d.startswith(folder + os.sep) for d in allowed):
        return jsonify({'error': 'Access denied'}), 403
    if file_path and not any(file_path == d or file_path.startswith(d + os.sep) for d in allowed):
        return jsonify({'error': 'Access denied'}), 403
    last_error = None
    try:
        if sys.platform == 'darwin':
            target = file_path if (file_path and os.path.isfile(file_path)) else None
            if target:
                target = os.path.normpath(os.path.realpath(target))
            else:
                target = os.path.normpath(os.path.realpath(folder))
            use_reveal = file_path and os.path.isfile(target)
            # 1) Try /usr/bin/open -R first (detached). Often works when app was started by you from Cursor.
            open_cmd = '/usr/bin/open' if os.path.isfile('/usr/bin/open') else 'open'
            args = [open_cmd, '-R', target] if use_reveal else [open_cmd, target]
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=os.environ,
            )
            # 2) Also try via login shell (can reach GUI when app was started from IDE)
            try:
                shell_cmd = f'{open_cmd} -R {shlex.quote(target)}' if use_reveal else f'{open_cmd} {shlex.quote(target)}'
                subprocess.Popen(
                    ['/bin/bash', '-l', '-c', shell_cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=os.environ,
                )
            except Exception:
                pass
            # 3) AppleScript (sometimes only this reaches Finder)
            if target and (not file_path or os.path.isfile(target)):
                try:
                    subprocess.run(
                        ['osascript', '-e', 'on run argv', '-e', 'set p to item 1 of argv',
                         '-e', 'tell application "Finder" to activate',
                         '-e', 'tell application "Finder" to reveal (POSIX file p as alias)',
                         '-e', 'end run', '--', target],
                        check=True, timeout=10,
                        env={**os.environ, 'PATH': os.environ.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')},
                        capture_output=True,
                        text=True,
                    )
                except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
                    last_error = str(e)
                    if isinstance(e, subprocess.CalledProcessError) and e.stderr:
                        raw = e.stderr.decode('utf-8', errors='replace') if isinstance(e.stderr, bytes) else e.stderr
                        if raw and raw.strip():
                            last_error = raw.strip()
                    logging.warning('open-file-location: AppleScript failed: %s', last_error)
        elif sys.platform == 'win32':
            if file_path and os.path.isfile(file_path):
                select_arg = '/select,"' + file_path.replace('"', '""') + '"'
                subprocess.run(['explorer', select_arg], check=True)
            else:
                subprocess.run(['explorer', folder], check=True)
        else:
            subprocess.run(['xdg-open', folder], check=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    out = {'ok': True}
    if last_error:
        out['warning'] = last_error
    return jsonify(out)


_soundeo_preview_cache: Dict[str, dict] = {}
_PREVIEW_CACHE_TTL = 24 * 3600


def _extract_soundeo_preview_url(track_page_url: str) -> Optional[str]:
    """Fetch a Soundeo track page and extract the audio preview MP3 URL."""
    import re
    import html as htmllib
    from config_shazam import get_soundeo_cookies_path

    cookies_path = get_soundeo_cookies_path()
    if not cookies_path or not os.path.exists(cookies_path):
        logging.warning("Soundeo preview: no cookies file at %s", cookies_path)
        return None

    track_id_match = re.search(r'-(\d+)\.html', track_page_url)
    if not track_id_match:
        logging.warning("Soundeo preview: could not parse track ID from %s", track_page_url)
        return None
    track_id = track_id_match.group(1)

    try:
        import requests as req
        with open(cookies_path, 'r', encoding='utf-8') as f:
            raw_cookies = json.load(f)
        session = req.Session()
        for c in raw_cookies:
            name = c.get('name', '')
            value = c.get('value', '')
            domain = c.get('domain', 'soundeo.com')
            if name and value:
                session.cookies.set(name, value, domain=domain.lstrip('.'))
        resp = session.get(track_page_url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }, timeout=10)
        if resp.status_code != 200:
            logging.warning("Soundeo preview: page returned %d for %s", resp.status_code, track_page_url)
            return None
        mp3_urls = re.findall(
            r'https?://[a-z0-9.\-]+/(?:preview|tracks)/[^\"\s\'&]+\.mp3[^\"\s\']*',
            resp.text,
        )
        mp3_urls = [htmllib.unescape(u) for u in mp3_urls]
        matching = [u for u in mp3_urls if track_id in u]
        result = matching[0] if matching else (mp3_urls[0] if mp3_urls else None)
        if not result:
            logging.warning("Soundeo preview: no MP3 URLs found on page for track %s", track_id)
        return result
    except Exception as exc:
        logging.warning("Soundeo preview extraction error: %s", exc)
        return None


@app.route('/api/soundeo/preview-url')
def soundeo_preview_url():
    """Get a playable audio preview URL for a Soundeo track. Cached for 24h."""
    track_url = request.args.get('track_url', '').strip()
    if not track_url or 'soundeo.com' not in track_url:
        return jsonify({'error': 'Missing or invalid track_url'}), 400

    cached = _soundeo_preview_cache.get(track_url)
    if cached and (time.time() - cached['ts']) < _PREVIEW_CACHE_TTL:
        return jsonify({'preview_url': cached['url']})

    preview = _extract_soundeo_preview_url(track_url)
    if not preview:
        return jsonify({'error': 'Could not extract preview URL'}), 404

    _soundeo_preview_cache[track_url] = {'url': preview, 'ts': time.time()}
    return jsonify({'preview_url': preview})


@app.route('/api/soundeo/stream-preview')
def soundeo_stream_preview():
    """Proxy-stream a Soundeo preview MP3 to avoid CORS issues."""
    track_url = request.args.get('track_url', '').strip()
    if not track_url or 'soundeo.com' not in track_url:
        return jsonify({'error': 'Missing or invalid track_url'}), 400

    cached = _soundeo_preview_cache.get(track_url)
    if cached and (time.time() - cached['ts']) < _PREVIEW_CACHE_TTL:
        preview_url = cached['url']
    else:
        preview_url = _extract_soundeo_preview_url(track_url)
        if not preview_url:
            return jsonify({'error': 'Could not extract preview URL'}), 404
        _soundeo_preview_cache[track_url] = {'url': preview_url, 'ts': time.time()}

    import requests as req
    try:
        upstream = req.get(preview_url, stream=True, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://soundeo.com/',
        })
        if upstream.status_code != 200:
            return jsonify({'error': 'Upstream returned ' + str(upstream.status_code)}), 502

        content_type = upstream.headers.get('Content-Type', 'audio/mpeg')
        content_length = upstream.headers.get('Content-Length')

        headers = {'Content-Type': content_type, 'Accept-Ranges': 'bytes'}
        if content_length:
            headers['Content-Length'] = content_length

        return Response(
            upstream.iter_content(chunk_size=8192),
            status=200,
            headers=headers,
        )
    except Exception:
        return jsonify({'error': 'Failed to fetch preview audio'}), 502


def _run_rescan_only_background():
    """Background thread: scan all folders, save cache, then rebuild status (to_download/have_locally) so list updates without needing Compare."""
    from config_shazam import get_destination_folders, get_destination_folders_raw
    from local_scanner import scan_folders, compute_to_download, _find_matching_local_track, ScanCancelled
    from shazam_cache import load_local_scan_cache, save_local_scan_cache, save_status_cache, load_shazam_cache, load_skip_list
    try:
        folder_paths = get_destination_folders()
        configured = get_destination_folders_raw()
        if not configured or not folder_paths:
            app._shazam_sync_status = dict(getattr(app, '_shazam_sync_status') or {})
            app._shazam_sync_status['message'] = 'No destination folders configured or paths not found.'
            app._shazam_compare_running = False
            app._shazam_scan_progress = {}
            return
        app._shazam_compare_cancel_requested = False
        app._shazam_scan_progress = {'scanning': True, 'current': 0, 'total': 0, 'message': 'Discovering files...'}

        def _on_progress(current: int, total: int):
            msg = 'Discovering files...' if total == 0 else 'Scanning files...'
            app._shazam_scan_progress = {'scanning': True, 'current': current, 'total': total, 'message': msg}

        def _should_cancel():
            return getattr(app, '_shazam_compare_cancel_requested', False)

        try:
            local_tracks = scan_folders(
                folder_paths,
                on_progress=_on_progress,
                should_cancel=_should_cancel,
                use_filename_only=True,
            )
        except ScanCancelled:
            app._shazam_sync_status = dict(getattr(app, '_shazam_sync_status') or {})
            app._shazam_sync_status['message'] = 'Rescan cancelled.'
            app._shazam_scan_progress = {}
            app._shazam_compare_running = False
            return
        save_local_scan_cache(folder_paths, local_tracks)
        shazam_tracks = load_shazam_cache()
        if shazam_tracks:
            app._shazam_scan_progress = {'scanning': True, 'current': len(local_tracks), 'total': len(local_tracks), 'message': 'Matching tracks...'}
            skipped = load_skip_list()
            to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, local_tracks)
            to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
            to_download = _dedupe_tracks_by_key(to_download)
            skipped_tracks = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                              for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
            to_dl_set = {_track_key_norm(s) for s in to_download}
            have_by_key = {}
            for s in shazam_tracks:
                k = _track_key_norm(s)
                if k in to_dl_set:
                    continue
                item = {'artist': s['artist'], 'title': s['title']}
                if s.get('shazamed_at') is not None:
                    item['shazamed_at'] = s['shazamed_at']
                match, match_score = _find_matching_local_track(s, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                if match and match.get('filepath'):
                    item['filepath'] = match['filepath']
                if match_score is not None:
                    item['match_score'] = match_score
                if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
                    have_by_key[k] = item
            have_locally = list(have_by_key.values())
            folder_stats = _folder_scan_stats(folder_paths, local_tracks, have_locally)
            status = {
                'shazam_count': len(shazam_tracks),
                'local_count': len(local_tracks),
                'to_download_count': len(to_download),
                'to_download': to_download,
                'have_locally': have_locally,
                'folder_stats': folder_stats,
                'skipped_tracks': skipped_tracks,
            }
            _merge_preserved_urls_into_status(status)
            app._shazam_sync_status = status
            save_status_cache(status)
        else:
            existing = dict(getattr(app, '_shazam_sync_status') or {})
            existing['message'] = 'Rescan complete. No Shazam tracks.'
            existing['local_count'] = len(local_tracks)
            app._shazam_sync_status = existing
    except Exception:
        app._shazam_sync_status = dict(getattr(app, '_shazam_sync_status') or {})
        app._shazam_sync_status['message'] = 'Rescan failed.'
    finally:
        app._shazam_compare_running = False
        app._shazam_scan_progress = {}
        try:
            _start_next_single_search()
        except Exception:
            pass


@app.route('/api/shazam-sync/rescan', methods=['POST'])
def shazam_sync_rescan():
    """Force rescan of all local folders. Optionally run compare after (default: yes). Body: { \"compare_after\": true|false }."""
    import os
    from shazam_cache import LOCAL_SCAN_CACHE_PATH
    compare_after = True
    try:
        data = request.get_json(silent=True) or {}
        compare_after = data.get('compare_after', True)
    except Exception:
        pass
    if os.path.exists(LOCAL_SCAN_CACHE_PATH):
        os.remove(LOCAL_SCAN_CACHE_PATH)
    if compare_after:
        return shazam_sync_compare()
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    app._shazam_compare_running = True
    thread = threading.Thread(target=_run_rescan_only_background, daemon=True)
    thread.start()
    return jsonify({'running': True, 'message': 'Rescanning folders...'})


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
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400

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
                to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, merged)
                to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
                to_download = _dedupe_tracks_by_key(to_download)
                skipped_tracks = [{'artist': t['artist'], 'title': t['title'], **({'shazamed_at': t['shazamed_at']} if t.get('shazamed_at') is not None else {})}
                                  for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
                to_dl_set = {_track_key_norm(s) for s in to_download}
                have_by_key = {}
                for s in shazam_tracks:
                    k = _track_key_norm(s)
                    if k in to_dl_set:
                        continue
                    item = {'artist': s['artist'], 'title': s['title']}
                    if s.get('shazamed_at') is not None:
                        item['shazamed_at'] = s['shazamed_at']
                    match, match_score = _find_matching_local_track(s, merged, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
                    if match and match.get('filepath'):
                        item['filepath'] = match['filepath']
                    if match_score is not None:
                        item['match_score'] = match_score
                    if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
                        have_by_key[k] = item
                have_locally = list(have_by_key.values())
                folders_with_data = list(set([folder_abs] + [os.path.abspath(f).rstrip(os.sep) for f in (cache.get('folders') or [])]))
                folder_stats = _folder_scan_stats(folders_with_data, merged, have_locally)
                status = {
                    'shazam_count': len(shazam_tracks),
                    'local_count': len(merged),
                    'to_download_count': len(to_download),
                    'to_download': to_download,
                    'have_locally': have_locally,
                    'folder_stats': folder_stats,
                    'skipped_tracks': skipped_tracks,
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
            try:
                _start_next_single_search()
            except Exception:
                pass

    app._shazam_compare_running = True
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({'running': True, 'message': f'Rescanning: {os.path.basename(folder_path) or folder_path}'})


@app.route('/api/shazam-sync/progress', methods=['GET'])
def shazam_sync_progress():
    """Return current automation progress. Includes single_search_queue and star_queue when used."""
    progress = getattr(app, '_shazam_sync_progress', None)
    if not progress:
        out = {'running': False, 'current': 0, 'total': 0}
    else:
        out = dict(progress)
    lock = getattr(app, '_shazam_single_search_queue_lock', None)
    queue = getattr(app, '_shazam_single_search_queue', None)
    if queue is not None and lock is not None:
        with lock:
            out['single_search_queue'] = [{'artist': q.get('artist', ''), 'title': q.get('title', '')} for q in queue]
    else:
        out['single_search_queue'] = []
    star_lock = getattr(app, '_shazam_single_star_queue_lock', None)
    star_queue = getattr(app, '_shazam_single_star_queue', None)
    if star_queue is not None and star_lock is not None:
        with star_lock:
            out['star_queue'] = [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in star_queue]
    else:
        out['star_queue'] = []
    unstar_lock = getattr(app, '_shazam_single_unstar_queue_lock', None)
    unstar_queue = getattr(app, '_shazam_single_unstar_queue', None)
    if unstar_queue is not None and unstar_lock is not None:
        with unstar_lock:
            out['unstar_queue'] = [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in unstar_queue]
    else:
        out['unstar_queue'] = []
    download_progress = getattr(app, '_shazam_download_progress', None)
    if download_progress:
        out['download_progress'] = download_progress
    download_pending = list(getattr(app, '_shazam_download_pending_queue', None) or [])
    if download_progress and download_progress.get('running'):
        current_queue = download_progress.get('queue') or []
        out['download_queue'] = list(current_queue) + download_pending
    else:
        out['download_queue'] = download_pending
    return jsonify(out)


def _shazam_any_job_running() -> bool:
    """True if Soundeo automation, compare/rescan, or download queue is running (so only one batch job at a time)."""
    if getattr(app, '_shazam_download_progress', {}).get('running'):
        return True
    if getattr(app, '_shazam_sync_progress', {}).get('running'):
        return True
    if getattr(app, '_shazam_compare_running', False):
        return True
    return False


def _apply_download_to_status_and_cache(key: str, filepath: str) -> None:
    """After download: we know exact key and filepath. Update status (have_locally, to_download) and local_scan_cache directly — no rescan or matching needed."""
    if not key or not filepath:
        return
    parts = key.split(' - ', 1)
    artist = parts[0].strip() if parts else ''
    title = parts[1].strip() if len(parts) > 1 else ''
    if not artist and not title:
        return
    from shazam_cache import load_status_cache, save_status_cache, load_local_scan_cache, save_local_scan_cache
    from config_shazam import get_destination_folders

    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    have = list(status.get('have_locally') or [])
    to_dl = list(status.get('to_download') or [])
    key_lower = (artist.strip().lower(), title.strip().lower())

    found = False
    for h in have:
        if _track_key_norm(h) == key_lower:
            h['filepath'] = filepath
            found = True
            break
    if not found:
        have.append({'artist': artist, 'title': title, 'filepath': filepath})
    to_dl = [t for t in to_dl if _track_key_norm(t) != key_lower]
    status['have_locally'] = have
    status['to_download'] = to_dl
    status['to_download_count'] = len(to_dl)
    _merge_preserved_urls_into_status(status)
    app._shazam_sync_status = status
    save_status_cache(status)

    folder_paths = get_destination_folders()
    if folder_paths:
        cache = load_local_scan_cache()
        tracks = list(cache.get('tracks', [])) if cache else []
        existing_paths = {t.get('filepath') for t in tracks}
        if filepath not in existing_paths:
            tracks.append({'artist': artist, 'title': title, 'filepath': filepath})
            save_local_scan_cache(folder_paths, tracks)


def _rescan_folder_and_update_status(folder_abs: str) -> None:
    """Rescan one folder, merge into local cache, rebuild to_download/have_locally and save status. Used when we need full rescan (e.g. files added outside the app)."""
    from config_shazam import get_destination_folders, get_destination_folders_raw
    from local_scanner import scan_folders, compute_to_download, _find_matching_local_track
    from shazam_cache import load_shazam_cache, load_local_scan_cache, save_local_scan_cache, save_status_cache, load_skip_list

    def _path_under(path: str, base: str) -> bool:
        p = os.path.abspath(path)
        return p == base or p.startswith(base + os.sep)

    cache = load_local_scan_cache()
    folder_paths = get_destination_folders()
    other_tracks = []
    if cache and cache.get('tracks'):
        for t in cache.get('tracks', []):
            if not _path_under(t.get('filepath', ''), folder_abs):
                other_tracks.append(t)
    new_tracks = scan_folders([folder_abs], use_filename_only=True)
    merged = other_tracks + [{"artist": t["artist"], "title": t["title"], "filepath": t["filepath"]} for t in new_tracks]
    save_local_scan_cache(folder_paths, merged)
    shazam_tracks = load_shazam_cache()
    if not shazam_tracks:
        return
    skipped = load_skip_list()
    to_download_raw, title_word_index, exact_match_map, local_canon = compute_to_download(shazam_tracks, merged)
    to_download = [t for t in to_download_raw if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
    to_download = _dedupe_tracks_by_key(to_download)
    to_dl_set = {_track_key_norm(s) for s in to_download}
    have_by_key = {}
    for s in shazam_tracks:
        k = _track_key_norm(s)
        if k in to_dl_set:
            continue
        item = {'artist': s['artist'], 'title': s['title']}
        if s.get('shazamed_at') is not None:
            item['shazamed_at'] = s['shazamed_at']
        match, match_score = _find_matching_local_track(s, merged, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon)
        if match and match.get('filepath'):
            item['filepath'] = match['filepath']
        if match_score is not None:
            item['match_score'] = match_score
        if k not in have_by_key or (item.get('filepath') and not have_by_key[k].get('filepath')):
            have_by_key[k] = item
    have_locally = list(have_by_key.values())
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


def _run_download_queue_worker():
    """Background: process download queue (sequential), then rescan and match after each success. Log and persist."""
    from config_shazam import get_soundeo_cookies_path, get_soundeo_download_folder
    from soundeo_automation import soundeo_download_aiff
    from shazam_cache import load_status_cache, save_status_cache

    dlog = _soundeo_download_log
    progress = getattr(app, '_shazam_download_progress', None)
    if not progress or not progress.get('running'):
        return
    keys = list(progress.get('queue', []))
    # Deduplicate so we don't download the same track multiple times in one run
    seen = set()
    keys = [k for k in keys if k not in seen and not seen.add(k)]
    total = len(keys)
    dest_dir = get_soundeo_download_folder()
    cookies_path = get_soundeo_cookies_path()
    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    urls = status.get('urls') or {}
    done = progress.get('done', 0)
    failed = progress.get('failed', 0)
    results = list(progress.get('results', []))
    no_credits = False
    for i, key in enumerate(keys):
        app._shazam_download_progress['current_key'] = key
        app._shazam_download_progress['message'] = f'Downloading {i + 1}/{total}: {key[:50]}...'
        app._shazam_download_progress['done'] = done
        app._shazam_download_progress['failed'] = failed
        dlog.info("download_queue: key=%s (%s/%s)", key[:60], i + 1, total)
        track_url = urls.get(key) or urls.get(key.lower() if key else '')
        if not track_url:
            dlog.warning("download_queue: no URL for key=%s, skip", key[:60])
            failed += 1
            results.append({'key': key, 'ok': False, 'error': 'No Soundeo URL'})
            app._shazam_download_progress['failed'] = failed
            app._shazam_download_progress['results'] = results
            continue
        if not dest_dir or not os.path.isdir(dest_dir):
            dlog.warning("download_queue: no download folder configured")
            app._shazam_download_progress['error'] = 'Download folder not set or invalid'
            app._shazam_download_progress['running'] = False
            save_status_cache(dict(getattr(app, '_shazam_sync_status', None) or {}))
            return
        out = soundeo_download_aiff(track_url, cookies_path, dest_dir, key)
        if not out.get('ok'):
            err = out.get('error', 'unknown')
            if err == 'no_credits':
                no_credits = True
                dlog.warning("download_queue: no credits (premium redirect), stop")
                app._shazam_download_progress['error'] = 'To be able to download you need to purchase premium account.'
                app._shazam_download_progress['running'] = False
                results.append({'key': key, 'ok': False, 'error': 'no_credits'})
                app._shazam_download_progress['results'] = results
                break
            failed += 1
            results.append({'key': key, 'ok': False, 'error': err})
            dlog.warning("download_queue: failed key=%s error=%s", key[:60], err)
        else:
            done += 1
            fp = out.get('filepath', '')
            results.append({'key': key, 'ok': True, 'filepath': fp, 'filename': out.get('filename')})
            dlog.info("download_queue: saved key=%s filepath=%s", key[:60], fp[:80] if fp else '')
            app._shazam_download_progress['message'] = f'Updating: {key[:40]}...'
            try:
                _apply_download_to_status_and_cache(key, fp)
            except Exception as e:
                dlog.warning("download_queue: apply download to status failed: %s", e)
        app._shazam_download_progress['done'] = done
        app._shazam_download_progress['failed'] = failed
        app._shazam_download_progress['results'] = results
        status = dict(getattr(app, '_shazam_sync_status', None) or {})
        status['download_last_run'] = {'done': done, 'failed': failed, 'results': results[-20:], 'no_credits': no_credits}
        save_status_cache(status)
    app._shazam_download_progress['running'] = False
    app._shazam_download_progress['message'] = f'Done. {done} downloaded, {failed} failed.'
    if no_credits:
        app._shazam_download_progress['error'] = 'To be able to download you need to purchase premium account.'
    elif failed > 0 and results:
        # Show the last failure reason so the user sees why (e.g. "Download returned 403", "No saved session")
        last_failed = next((r for r in reversed(results) if not r.get('ok') and r.get('error')), None)
        if last_failed:
            app._shazam_download_progress['error'] = last_failed.get('error', 'Download failed')
    dlog.info("download_queue: finished done=%s failed=%s", done, failed)
    # Start next in queue if any (one-by-one)
    if not no_credits:
        _shazam_download_start_next()


def _shazam_download_pending_append(key: str) -> list:
    """Append key to download pending queue (dedupe); return current list. Caller holds no lock."""
    if not hasattr(app, '_shazam_download_pending_queue'):
        app._shazam_download_pending_queue = []
    if not hasattr(app, '_shazam_download_queue_lock'):
        app._shazam_download_queue_lock = threading.Lock()
    with app._shazam_download_queue_lock:
        q = getattr(app, '_shazam_download_pending_queue', None) or []
        if key not in q:
            q.append(key)
            app._shazam_download_pending_queue = q
        return list(app._shazam_download_pending_queue)


def _shazam_download_full_queue() -> list:
    """Return full download queue for API: current (when running) + pending."""
    progress = getattr(app, '_shazam_download_progress', None)
    pending = list(getattr(app, '_shazam_download_pending_queue', None) or [])
    if progress and progress.get('running'):
        current = list(progress.get('queue') or [])
        return current + pending
    return pending


def _shazam_download_start_next() -> bool:
    """If download not running and no other job, pop one from pending and start worker. Returns True if started."""
    if _shazam_any_job_running():
        return False
    lock = getattr(app, '_shazam_download_queue_lock', None)
    if not lock:
        return False
    with lock:
        pending = getattr(app, '_shazam_download_pending_queue', None) or []
        if not pending:
            return False
        key = pending.pop(0)
        app._shazam_download_pending_queue = pending
        remaining = len(pending)
    prev = getattr(app, '_shazam_download_progress', None) or {}
    # Full batch size: this item + remaining in queue
    batch_total = 1 + remaining
    # When continuing after a previous run (e.g. second of two), preserve done/failed and keep total = full batch
    prev_done = prev.get('done', 0)
    prev_failed = prev.get('failed', 0)
    prev_total = prev.get('total', 0)
    total = max(prev_total, batch_total) if (prev_done or prev_failed) else batch_total
    if not getattr(app, '_shazam_download_progress', None):
        app._shazam_download_progress = {}
    prev_results = list(prev.get('results') or [])
    app._shazam_download_progress.update({
        'running': True, 'queue': [key], 'done': prev_done, 'failed': prev_failed, 'total': total,
        'current_key': key, 'message': f'Downloading {prev_done + 1}/{total}: {key[:50]}...', 'results': prev_results,
    })
    thread = threading.Thread(target=_run_download_queue_worker, daemon=True)
    thread.start()
    return True


@app.route('/api/shazam-sync/download-track', methods=['POST'])
def shazam_sync_download_track():
    """Download AIFF for one track (per-row). Body: { key: 'Artist - Title' }. Adds to queue; runs one-by-one. Queued if another op is running."""
    from config_shazam import get_soundeo_download_folder
    from shazam_cache import load_status_cache
    data = request.get_json() or {}
    key = (data.get('key') or '').strip()
    if not key:
        return jsonify({'error': 'Missing key'}), 400
    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    urls = status.get('urls') or {}
    if not (urls.get(key) or urls.get((key or '').lower())):
        return jsonify({'error': 'No Soundeo URL for this track'}), 400
    dest_dir = get_soundeo_download_folder()
    if not dest_dir or not os.path.isdir(dest_dir):
        return jsonify({'error': 'Download folder not set. Choose one in Settings.'}), 400
    _shazam_download_pending_append(key)
    started = _shazam_download_start_next()
    download_queue = _shazam_download_full_queue()
    return jsonify({
        'ok': True,
        'status': 'started' if started else 'queued',
        'message': 'Downloading...' if started else f'Download queued ({len(download_queue)} in queue)',
        'download_queue': download_queue,
    })


@app.route('/api/shazam-sync/download-queue', methods=['POST'])
def shazam_sync_download_queue():
    """Queue bulk download (one-by-one). Body: { keys: ['Artist - Title', ...] }. Adds to pending queue; only keys with Soundeo URL."""
    from config_shazam import get_soundeo_download_folder
    from shazam_cache import load_status_cache
    data = request.get_json() or {}
    keys = data.get('keys') or []
    if not keys:
        return jsonify({'error': 'Missing keys'}), 400
    dest_dir = get_soundeo_download_folder()
    if not dest_dir or not os.path.isdir(dest_dir):
        return jsonify({'error': 'Download folder not set. Choose one in Settings.'}), 400
    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    urls = status.get('urls') or {}
    for k in keys:
        if urls.get(k) or urls.get((k or '').lower()):
            _shazam_download_pending_append(k)
    download_queue = _shazam_download_full_queue()
    if not download_queue:
        return jsonify({'error': 'No tracks with Soundeo URL in the list'}), 400
    started = _shazam_download_start_next()
    return jsonify({
        'ok': True,
        'status': 'started' if started else 'queued',
        'total': len(download_queue),
        'message': f'Downloading {len(download_queue)} tracks...' if started else f'Download queued ({len(download_queue)} in queue)',
        'download_queue': download_queue,
    })


@app.route('/api/shazam-sync/remove-from-download-queue', methods=['POST'])
def shazam_remove_from_download_queue():
    """Remove one key from the download pending queue. Body: { key: 'Artist - Title' }. Cannot remove the track currently downloading."""
    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing key'}), 400
    lock = getattr(app, '_shazam_download_queue_lock', None)
    if not lock or not hasattr(app, '_shazam_download_pending_queue'):
        return jsonify({'ok': True, 'download_queue': []})
    key_lower = key.lower()
    with lock:
        pending = list(getattr(app, '_shazam_download_pending_queue', None) or [])
        new_pending = [k for k in pending if k != key and (k or '').lower() != key_lower]
        app._shazam_download_pending_queue = new_pending
    download_queue = _shazam_download_full_queue()
    return jsonify({'ok': True, 'download_queue': download_queue})


@app.route('/api/shazam-sync/download-start-next', methods=['POST'])
def shazam_sync_download_start_next():
    """Start the next download from the pending queue if no job is running. Called by frontend when e.g. Search finishes and download queue has items."""
    started = _shazam_download_start_next()
    return jsonify({'ok': True, 'started': started})


@app.route('/api/shazam-sync/download-log', methods=['GET'])
def shazam_sync_download_log():
    """Return the last N lines of soundeo_download.log for debugging (default 100). Query: ?lines=100."""
    log_path = _get_soundeo_download_log_path()
    try:
        lines = int(request.args.get('lines', 100))
        lines = min(max(1, lines), 500)
    except (TypeError, ValueError):
        lines = 100
    try:
        if not os.path.isfile(log_path):
            return Response("Log file not created yet. Run a download first.\n", mimetype='text/plain')
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
        last = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return Response(''.join(last), mimetype='text/plain')
    except Exception as e:
        return Response(f"Could not read log: {e}\n", status=500, mimetype='text/plain')


def _strip_all_parens(key: str) -> str:
    """Remove all (...) and [...] segments, collapse spaces. For cross-matching e.g. 'mOat (UK) - Guard Your Joy (Extended Mix)' -> 'mOat - Guard Your Joy'."""
    import re
    s = (key or "").strip()
    s = re.sub(r'\s*\([^)]*\)\s*', ' ', s)
    s = re.sub(r'\s*\[[^\]]*\]\s*', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s or key or ""


def _deep_norm_key(key: str) -> str:
    """Deep normalize: strip parens, lowercase, unify '&'/','  separators, sort artists."""
    s = _strip_all_parens(key).lower().replace(' & ', ', ')
    if ' - ' in s:
        artist_part, title_part = s.split(' - ', 1)
        artists = sorted(a.strip() for a in artist_part.split(', ') if a.strip())
        s = ', '.join(artists) + ' - ' + title_part
    return s


def _merge_crawled_favorites_into_status(status: Dict, favorites: List[Dict], full_scan: bool = False, cookies_path: Optional[str] = None) -> None:
    """Merge crawled /account/favorites into status (starred, urls, soundeo_titles).

    Source of truth for starred.  Matches crawl keys to app track keys via
    multiple normalizations (case, parenthetical, & vs , , artist order) so
    Shazam keys reliably find their Soundeo counterpart.

    When full_scan=True, any starred key whose normalized form is NOT in the crawl is set False.
    When cookies_path is set, also resolves and stores track_id for each URL.
    """
    status.setdefault('urls', {})
    status.setdefault('soundeo_titles', {})
    status.setdefault('starred', {})

    # Build multi-level lookup: norm → [actual app key, ...]. Include dot-stripped variants so
    # "R.E.Zarin" (Shazam) matches "REZarin" (Soundeo favorites page).
    app_keys_by_norm: Dict[str, list] = {}
    app_keys_by_deep: Dict[str, list] = {}
    for track in (status.get('to_download') or []) + (status.get('have_locally') or []):
        k = f"{track.get('artist', '')} - {track.get('title', '')}"
        norm = _strip_all_parens(k).lower()
        deep = _deep_norm_key(k)
        app_keys_by_norm.setdefault(norm, []).append(k)
        app_keys_by_deep.setdefault(deep, []).append(k)
        norm_no_dots = norm.replace('.', '')
        deep_no_dots = deep.replace('.', '')
        if norm_no_dots != norm:
            app_keys_by_norm.setdefault(norm_no_dots, []).append(k)
        if deep_no_dots != deep:
            app_keys_by_deep.setdefault(deep_no_dots, []).append(k)

    def _store(k: str, url: Optional[str], soundeo_title: Optional[str]):
        """Set starred/url/soundeo_title under key and its lowercase; resolve track_id when url is set."""
        status['starred'][k] = True
        status['starred'][k.lower()] = True
        if url:
            _set_url_and_track_id(status, k, url, cookies_path)
        if soundeo_title:
            status['soundeo_titles'][k] = soundeo_title
            status['soundeo_titles'][k.lower()] = soundeo_title

    crawled_norms_lower: set = set()
    crawled_deep_norms: set = set()

    for item in favorites:
        key = item.get('key') or ''
        if not key:
            continue
        url = item.get('url')
        soundeo_title = item.get('soundeo_title')

        norm_lower = _strip_all_parens(key).lower()
        deep = _deep_norm_key(key)
        crawled_norms_lower.add(norm_lower)
        crawled_deep_norms.add(deep)
        crawled_deep_norms.add(deep.replace('.', ''))

        # Store under crawl key (+ lowercase)
        _store(key, url, soundeo_title)

        # Match against app keys by basic normalization
        matched_app_keys: set = set()
        for app_key in app_keys_by_norm.get(norm_lower, []):
            matched_app_keys.add(app_key)
        for app_key in app_keys_by_norm.get(key.lower(), []):
            matched_app_keys.add(app_key)
        # Match by deep normalization (handles & vs , and artist order)
        for app_key in app_keys_by_deep.get(deep, []):
            matched_app_keys.add(app_key)

        for app_key in matched_app_keys:
            _store(app_key, url, soundeo_title)

    # Clear starred for any app track not found in this crawl (so we don't show stale "starred" after user unstars on Soundeo).
    # Do this for every sync, not only full_scan, so limited-range syncs still clear starred for tracks not seen.
    all_app_keys = set()
    for track in (status.get('to_download') or []) + (status.get('have_locally') or []):
        k = f"{track.get('artist', '')} - {track.get('title', '')}"
        if k.strip():
            all_app_keys.add(k)
    for app_key in all_app_keys:
        norm = _strip_all_parens(app_key).lower()
        norm_no_dots = norm.replace('.', '')
        deep = _deep_norm_key(app_key)
        deep_no_dots = deep.replace('.', '')
        in_crawl = (
            norm in crawled_norms_lower or norm_no_dots in crawled_norms_lower
            or deep in crawled_deep_norms or deep_no_dots in crawled_deep_norms
        )
        if not in_crawl:
            status.setdefault('starred', {})[app_key] = False
            status['starred'][app_key.lower()] = False


def _run_soundeo_automation(tracks: list):
    """Background thread: run Soundeo automation for tracks. Crawls favorites first (source of truth), then syncs."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import run_favorite_tracks

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    headed = config.get('headed_mode', True)
    time_range = getattr(app, '_shazam_sync_run_time_range', None)
    max_favorites_pages = _time_range_to_max_pages(time_range)

    def on_progress(current: int, total: int, msg: str, url: Optional[str], current_key: Optional[str] = None):
        prog = {
            'running': True,
            'current': current,
            'total': total,
            'message': msg,
            'last_url': url,
        }
        if current_key is not None:
            prog['current_key'] = current_key
        app._shazam_sync_progress = prog

    try:
        results = run_favorite_tracks(
            tracks, cookies_path,
            headed=headed,
            on_progress=on_progress,
            crawl_favorites_first=True,
            max_favorites_pages=max_favorites_pages,
        )
        app._shazam_sync_progress = {
            'running': False,
            'done': results.get('done', 0),
            'failed': results.get('failed', 0),
            'error': results.get('error'),
            'urls': results.get('urls', {}),
            'stopped': results.get('stopped', False),
        }
        # Persist: merge crawled favorites (source of truth for starred), then merge this run's new URLs/starred
        from shazam_cache import save_status_cache
        status = dict(getattr(app, '_shazam_sync_status', None) or {})
        _merge_crawled_favorites_into_status(status, results.get('crawled_favorites') or [], cookies_path=cookies_path)
        new_urls = results.get('urls') or {}
        new_titles = results.get('soundeo_titles') or {}
        new_scores = results.get('soundeo_match_scores') or {}
        for k, url in new_urls.items():
            _set_url_and_track_id(status, k, url, cookies_path)
            status.setdefault('starred', {})[k] = True
            status['starred'][k.lower()] = True
        for k, title in new_titles.items():
            status.setdefault('soundeo_titles', {})[k] = title
            status['soundeo_titles'][k.lower()] = title
        for k, sc in new_scores.items():
            status.setdefault('soundeo_match_scores', {})[k] = sc
            status['soundeo_match_scores'][k.lower()] = sc
        app._shazam_sync_status = status
        save_status_cache(status)
    except Exception as e:
        app._shazam_sync_progress = {
            'running': False,
            'error': str(e),
        }


def _time_range_to_max_pages(time_range: Optional[str]) -> Optional[int]:
    """Map UI time range to max favorites pages to scan (newest-first). Avoids scanning old pages when only recent Shazams matter."""
    if not time_range or time_range == 'all':
        return None  # no limit
    if time_range == '1_month':
        return 3
    if time_range == '2_months':
        return 6
    if time_range == '3_months':
        return 10
    return None


_MAX_CROSS_CHECK = 30


def _build_verify_list(status: Dict, crawled_favorites: List[Dict]) -> List[Dict]:
    """Find app tracks that were starred but NOT found in the crawl — candidates for cross-check.
    Returns at most _MAX_CROSS_CHECK entries (prioritising to_download over have_locally)."""
    crawled_deep = set()
    for item in crawled_favorites:
        k = item.get('key') or ''
        crawled_deep.add(_deep_norm_key(k))

    verify = []
    seen_deep: set = set()
    starred = status.get('starred') or {}
    urls = status.get('urls') or {}
    for track in (status.get('to_download') or []) + (status.get('have_locally') or []):
        k = f"{track.get('artist', '')} - {track.get('title', '')}"
        deep = _deep_norm_key(k)
        if deep in seen_deep:
            continue
        seen_deep.add(deep)
        is_starred = starred.get(k) or starred.get(k.lower())
        if not is_starred:
            continue
        if deep in crawled_deep:
            continue
        url = urls.get(k) or urls.get(k.lower())
        if url:
            verify.append({"key": k, "url": url})
        if len(verify) >= _MAX_CROSS_CHECK:
            break
    return verify


def _run_sync_favorites_from_soundeo():
    """Background thread: crawl /account/favorites, cross-check our tracks, log mutations."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import crawl_soundeo_favorites
    from shazam_cache import save_status_cache, log_starred_mutations

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    headed = config.get('headed_mode', True)
    time_range = getattr(app, '_shazam_sync_favorites_time_range', None)
    max_pages = _time_range_to_max_pages(time_range)

    def on_progress(msg: str, current_page: Optional[int] = None):
        if hasattr(app, '_shazam_sync_progress') and app._shazam_sync_progress:
            prog = dict(app._shazam_sync_progress, message=msg)
            if current_page is not None:
                prog['current_page'] = current_page
            app._shazam_sync_progress = prog
        else:
            app._shazam_sync_progress = {
                'running': True, 'message': msg, 'current': 0, 'total': 0,
                **({'current_page': current_page} if current_page is not None else {}),
            }

    try:
        status = dict(getattr(app, '_shazam_sync_status', None) or {})
        old_starred = {k: v for k, v in (status.get('starred') or {}).items() if v}

        # Build verify list BEFORE crawling — tracks previously starred with a URL
        verify_list = _build_verify_list(status, [])

        # Crawl + cross-check in one browser session
        app._shazam_sync_progress = {'running': True, 'message': 'Crawling Soundeo favorites...', 'current': 0, 'total': 0, 'mode': 'sync_favorites'}
        result = crawl_soundeo_favorites(
            cookies_path, headed=headed, on_progress=on_progress,
            max_pages=max_pages, verify_tracks=verify_list,
        )
        if not result.get('ok'):
            app._shazam_sync_progress = {'running': False, 'error': result.get('error', 'Crawl failed'), 'mode': 'sync_favorites'}
            return
        favorites = result.get('favorites') or []
        verified = result.get('verified') or []

        # Merge crawl results into status
        is_full_scan = max_pages is None and not result.get('stopped', False)
        _merge_crawled_favorites_into_status(status, favorites, full_scan=is_full_scan, cookies_path=cookies_path)

        # Apply cross-check results — unstar tracks confirmed not favorited
        newly_unstarred = []
        for v in verified:
            if v.get('still_favorited') is False:
                key = v['key']
                status.setdefault('starred', {})[key] = False
                status['starred'][key.lower()] = False
                newly_unstarred.append(key)

        # Detect mutations (compare old vs new starred state)
        new_starred = {k: v for k, v in (status.get('starred') or {}).items() if v}
        old_deep = {_deep_norm_key(k) for k in old_starred}
        new_deep = {_deep_norm_key(k) for k in new_starred}
        added_deep = new_deep - old_deep

        newly_starred_keys = [k for k in new_starred if _deep_norm_key(k) in added_deep and k == k.strip() and k != k.lower()]
        newly_unstarred_keys = [k for k in newly_unstarred if k == k.strip() and k != k.lower()]
        mutations = log_starred_mutations(
            newly_starred_keys, newly_unstarred_keys,
            source="sync_favorites",
        )

        app._shazam_sync_status = status
        save_status_cache(status)

        msg_parts = [f'{len(favorites)} favorites crawled']
        if verified:
            msg_parts.append(f'{len(verified)} cross-checked')
        if newly_unstarred_keys:
            msg_parts.append(f'{len(newly_unstarred_keys)} unstarred')
        if newly_starred_keys:
            msg_parts.append(f'{len(newly_starred_keys)} newly starred')
        app._shazam_sync_progress = {
            'running': False,
            'message': f'Favorites synced ({", ".join(msg_parts)}).',
            'favorites_count': len(favorites),
            'mutations': len(mutations),
            'mode': 'sync_favorites',
        }
    except Exception as e:
        app._shazam_sync_progress = {'running': False, 'error': str(e), 'mode': 'sync_favorites'}


def _run_sync_single_track_browser(artist: str, title: str):
    """Background thread: find track on Soundeo and favorite it (browser, same as Run Soundeo)."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import _get_driver, load_cookies, _is_redirected_to_login, find_and_favorite_track, _graceful_quit
    from shazam_cache import save_status_cache

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    headed = config.get('headed_mode', True)
    key = f"{artist} - {title}"

    try:
        app._shazam_sync_progress = {'running': True, 'message': f'Finding & starring: {artist} - {title}', 'mode': 'sync_single'}
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
        if not load_cookies(driver, cookies_path):
            app._shazam_sync_progress = {'running': False, 'error': 'No saved session. Save Soundeo session first.', 'mode': 'sync_single'}
            _graceful_quit(driver)
            return
        out = find_and_favorite_track(driver, artist, title, already_starred=set())
        not_logged_in = not out and _is_redirected_to_login(driver)
        _graceful_quit(driver)

        if out:
            status = dict(getattr(app, '_shazam_sync_status', None) or {})
            status.setdefault('urls', {})
            status.setdefault('soundeo_titles', {})
            status.setdefault('soundeo_match_scores', {})
            status.setdefault('starred', {})
            url_val = out[0] if isinstance(out, tuple) else out
            title_val = (out[1] if isinstance(out, tuple) and len(out) > 1 else '') or key
            match_sc = (out[2] if isinstance(out, tuple) and len(out) > 2 else None)
            _set_url_and_track_id(status, key, url_val, cookies_path)
            status['soundeo_titles'][key] = status['soundeo_titles'][key.lower()] = title_val
            if match_sc is not None:
                status['soundeo_match_scores'][key] = round(match_sc, 3)
                status['soundeo_match_scores'][key.lower()] = round(match_sc, 3)
            status['starred'][key] = status['starred'][key.lower()] = True
            app._shazam_sync_status = status
            save_status_cache(status)
            app._shazam_sync_progress = {
                'running': False, 'done': 1, 'key': key, 'url': url_val,
                'soundeo_title': title_val, 'message': f'Starred: {artist} - {title}', 'mode': 'sync_single',
            }
        else:
            err = 'Soundeo session expired. Save session again.' if not_logged_in else 'Not found on Soundeo'
            app._shazam_sync_progress = {
                'running': False, 'done': 0, 'failed': 1, 'key': key,
                'error': err, 'message': err if not_logged_in else f'Not found: {artist} - {title}', 'mode': 'sync_single',
            }
    except Exception as e:
        app._shazam_sync_progress = {'running': False, 'error': str(e), 'mode': 'sync_single'}


def _start_next_single_search():
    """If single-search queue has items, pop one and run it in a background thread. Call with _shazam_single_search_queue_lock held only when called from request handler; from worker we acquire the lock."""
    queue = getattr(app, '_shazam_single_search_queue', None) or []
    lock = getattr(app, '_shazam_single_search_queue_lock', None)
    if lock is None:
        return
    with lock:
        queue = getattr(app, '_shazam_single_search_queue', None) or []
        if not queue:
            return
        item = queue.pop(0)
        app._shazam_single_search_queue = queue
    artist, title = item.get('artist', ''), item.get('title', '')
    if not artist and not title:
        _start_next_single_search()
        return
    app._shazam_sync_progress = {'running': True, 'message': f'Searching: {artist} - {title}', 'mode': 'search_single'}
    thread = threading.Thread(target=_run_search_soundeo_single, args=(artist, title), daemon=True)
    thread.start()


def _run_search_soundeo_single(artist: str, title: str):
    """Background thread: search one track on Soundeo (no favorite), merge url/title into status."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import _get_driver, load_cookies, _is_redirected_to_login, find_track_on_soundeo, get_track_starred_state, soundeo_api_get_favorite_state, _graceful_quit
    from shazam_cache import save_status_cache

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    headed = config.get('headed_mode', True)
    key = f"{artist} - {title}"

    try:
        app._shazam_sync_progress = {'running': True, 'message': f'Searching: {artist} - {title}', 'mode': 'search_single'}
        driver = _get_driver(headless=not headed, use_persistent_profile=True)
        if not load_cookies(driver, cookies_path):
            app._shazam_sync_progress = {'running': False, 'error': 'No saved session. Save Soundeo session first.', 'mode': 'search_single'}
            _graceful_quit(driver)
            _start_next_single_search()
            return
        out = find_track_on_soundeo(driver, artist, title)
        starred = False
        if out and out[0]:
            # Use same starred-state mechanism as star/unstar: HTTP first, crawler fallback
            starred_val = None
            try:
                starred_val = soundeo_api_get_favorite_state(out[0], cookies_path)
            except Exception as e:
                _search_favorite_log.debug("search_single key=%s soundeo_api_get_favorite_state failed: %s", key[:70], e)
            if starred_val is not None:
                starred = bool(starred_val)
                _search_favorite_log.info(
                    "search_single key=%s found=True url=%s starred=%s (via HTTP, same as star/unstar)",
                    key[:70], (out[0] or "")[:60], starred,
                )
            else:
                try:
                    starred = get_track_starred_state(driver, out[0])
                    _search_favorite_log.info(
                        "search_single key=%s found=True url=%s get_track_starred_state=%s (crawler fallback)",
                        key[:70], (out[0] or "")[:60], starred,
                    )
                except Exception as e:
                    _search_favorite_log.warning("search_single key=%s get_track_starred_state failed: %s", key[:70], e)
        else:
            _search_favorite_log.info("search_single key=%s found=False (not found or not logged in)", key[:70])
        not_logged_in = not out and _is_redirected_to_login(driver)
        _graceful_quit(driver)

        if out:
            from shazam_cache import load_status_cache, log_search_outcome
            status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
            status.setdefault('urls', {})
            status.setdefault('soundeo_titles', {})
            status.setdefault('soundeo_match_scores', {})
            status.setdefault('not_found', {})
            _set_url_and_track_id(status, key, out[0], cookies_path)
            title_val = (out[1] if len(out) > 1 else '') or key
            status['soundeo_titles'][key] = status['soundeo_titles'][key.lower()] = title_val
            match_sc = out[2] if len(out) > 2 else None
            if match_sc is not None:
                status['soundeo_match_scores'][key] = round(match_sc, 3)
                status['soundeo_match_scores'][key.lower()] = round(match_sc, 3)
            status['not_found'].pop(key, None)
            status['not_found'].pop(key.lower(), None)
            app._shazam_sync_status = status
            log_search_outcome(key, found=True, url=out[0], status_to_update=status)
            save_status_cache(status)
        else:
            if not not_logged_in:
                from shazam_cache import load_status_cache
                status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
                status.setdefault('not_found', {})
                status['not_found'][key] = True
                status['not_found'][key.lower()] = True
                app._shazam_sync_status = status
                from shazam_cache import log_search_outcome
                log_search_outcome(key, found=False, status_to_update=status)
                save_status_cache(status)

        if out:
            status.setdefault('starred', {})
            # Trust search result: we use same HTTP get_favorite_state as star/unstar, so when user unstars on Soundeo and searches again we must show unstarred
            status['starred'][key] = status['starred'][key.lower()] = bool(starred)
            save_status_cache(status)
            _search_favorite_log.info(
                "search_single key=%s status.starred updated to %s (UI should show filled star if True)",
                key[:70], starred,
            )
            app._shazam_sync_progress = {
                'running': False, 'done': 1, 'key': key, 'url': out[0],
                'soundeo_title': (out[1] if len(out) > 1 else '') or key,
                'starred': starred,
                'message': f'Found: {artist} - {title}', 'mode': 'search_single',
            }
        else:
            err = 'Soundeo session expired. Save session again.' if not_logged_in else 'Not found on Soundeo'
            app._shazam_sync_progress = {
                'running': False, 'done': 0, 'failed': 1, 'key': key,
                'error': err, 'message': err if not_logged_in else f'Not found: {artist} - {title}', 'mode': 'search_single',
            }
        _start_next_single_search()
    except Exception as e:
        app._shazam_sync_progress = {'running': False, 'error': str(e), 'mode': 'search_single'}
        _start_next_single_search()


def _run_search_soundeo_global(search_mode: Optional[str] = None):
    """Background thread: Search all (global) — only tracks without a Soundeo URL.
    search_mode: 'unfound' = only orange-dot (searched, not found); 'new' = only grey-dot (not yet searched); None = both.
    We skip any track that already has a URL. For re-search of a single track, use the per-row Search. No favorite.
    Saves to status cache as soon as each track result is known (found/not found) so restart mid-batch doesn't lose progress."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import run_search_tracks, run_search_tracks_http
    from shazam_cache import load_status_cache, save_status_cache, log_search_outcome

    config = load_config()
    cookies_path = get_soundeo_cookies_path()
    use_http = config.get('search_all_use_http', False)
    headed = config.get('headed_mode', True)
    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    status.setdefault('urls', {})
    status.setdefault('soundeo_titles', {})
    status.setdefault('soundeo_match_scores', {})
    status.setdefault('not_found', {})
    status.setdefault('search_outcomes', [])
    urls = status.get('urls') or {}
    not_found = status.get('not_found') or {}
    skip_keys = set(urls.keys())

    tracks = []
    seen = set()
    for t in (status.get('to_download') or []) + (status.get('have_locally') or []):
        k = f"{t.get('artist', '')} - {t.get('title', '')}"
        k_lower = k.lower()
        if k_lower in seen:
            continue
        seen.add(k_lower)
        if k in skip_keys or k_lower in skip_keys:
            continue
        in_not_found = k in not_found or k_lower in not_found
        if search_mode == 'unfound' and not in_not_found:
            continue
        if search_mode == 'new' and in_not_found:
            continue
        tracks.append({'artist': t.get('artist', ''), 'title': t.get('title', '')})

    if not tracks:
        if search_mode == 'unfound':
            msg = 'No unfound tracks to search (no orange-dot rows).'
        elif search_mode == 'new':
            msg = 'No new tracks to search (no grey-dot rows).'
        else:
            msg = 'No tracks to search (all have links).'
        app._shazam_sync_progress = {'running': False, 'message': msg, 'done': 0, 'total': 0}
        return

    def on_progress(current: int, total: int, msg: str, url: Optional[str], current_key: Optional[str] = None, **kwargs):
        existing = getattr(app, '_shazam_sync_progress', None) or {}
        prog = {
            'running': True, 'current': current, 'total': total, 'message': msg,
            'last_url': url, 'mode': 'search_global', 'search_mode': search_mode,
            'urls': dict(existing.get('urls', {})),
            'not_found': dict(existing.get('not_found', {})),
            'soundeo_titles': dict(existing.get('soundeo_titles', {})),
            'soundeo_match_scores': dict(existing.get('soundeo_match_scores', {})),
            'starred': dict(existing.get('starred', {})),
        }
        if current_key is not None:
            prog['current_key'] = current_key
        # Paper trail: record outcome and save immediately so restart mid-batch doesn't lose progress
        if current_key and 'Skipped' not in msg:
            if 'Found:' in msg and url:
                log_search_outcome(current_key, found=True, url=url, status_to_update=status)
                _set_url_and_track_id(status, current_key, url, cookies_path)
                soundeo_title = kwargs.get('soundeo_title')
                soundeo_match_score = kwargs.get('soundeo_match_score')
                if soundeo_title is not None:
                    status.setdefault('soundeo_titles', {})[current_key] = soundeo_title
                    status['soundeo_titles'][current_key.lower()] = soundeo_title
                    prog['soundeo_titles'][current_key] = soundeo_title
                    prog['soundeo_titles'][current_key.lower()] = soundeo_title
                if soundeo_match_score is not None:
                    status.setdefault('soundeo_match_scores', {})[current_key] = round(soundeo_match_score, 3)
                    status['soundeo_match_scores'][current_key.lower()] = round(soundeo_match_score, 3)
                    prog['soundeo_match_scores'][current_key] = round(soundeo_match_score, 3)
                    prog['soundeo_match_scores'][current_key.lower()] = round(soundeo_match_score, 3)
                starred_val = kwargs.get('starred')
                if starred_val is not None:
                    status.setdefault('starred', {})[current_key] = bool(starred_val)
                    status['starred'][current_key.lower()] = bool(starred_val)
                    prog['starred'][current_key] = bool(starred_val)
                    prog['starred'][current_key.lower()] = bool(starred_val)
                    _search_favorite_log.info(
                        "search_global key=%s url=%s starred=%s (blue star on Soundeo) status.starred updated",
                        current_key[:70], url[:60] if url else "", starred_val,
                    )
                save_status_cache(status)
                prog['urls'][current_key] = url
                prog['urls'][current_key.lower()] = url
            elif 'Not found' in msg or 'not found' in msg.lower():
                log_search_outcome(current_key, found=False, status_to_update=status)
                save_status_cache(status)
                prog['not_found'][current_key] = True
                prog['not_found'][current_key.lower()] = True
        app._shazam_sync_progress = prog

    try:
        total_count = len(tracks)
        if search_mode == 'unfound':
            label = 'unfound'
        elif search_mode == 'new':
            label = 'new'
        else:
            label = 'all'
        start_msg = f'Searching {label}: 0/{total_count}'
        if use_http:
            start_msg = f'Searching {label} (HTTP): 0/{total_count}'
        elif not headed:
            start_msg = f'Searching {label} (headless): 0/{total_count}'
        app._shazam_sync_progress = {
            'running': True, 'message': start_msg, 'current': 0, 'total': total_count, 'mode': 'search_global', 'search_mode': search_mode,
            'urls': {}, 'not_found': {}, 'soundeo_titles': {}, 'soundeo_match_scores': {}, 'starred': {},
        }
        if use_http:
            result = run_search_tracks_http(tracks, cookies_path, on_progress=on_progress, skip_keys=skip_keys)
        else:
            result = run_search_tracks(
                tracks, cookies_path, headed=headed,
                on_progress=on_progress, skip_keys=skip_keys,
            )
        if result.get('error'):
            app._shazam_sync_progress = {'running': False, 'error': result['error'], 'mode': 'search_global'}
            return
        # Outcomes already logged and saved per-track in on_progress; only merge titles/scores and final save
        for k, v in (result.get('soundeo_titles') or {}).items():
            status['soundeo_titles'][k] = v
            status['soundeo_titles'][k.lower()] = v
        for k, sc in (result.get('soundeo_match_scores') or {}).items():
            status['soundeo_match_scores'][k] = sc
            status['soundeo_match_scores'][k.lower()] = sc
        for k in list(status.get('not_found') or {}):
            if status['urls'].get(k) or status['urls'].get(k.lower() if isinstance(k, str) else None):
                del status['not_found'][k]
        app._shazam_sync_status = status
        save_status_cache(status)
        # Include not_found/urls/titles/scores in final progress so frontend can update without refetch
        done_count = result.get('done', 0)
        failed_count = result.get('failed', 0)
        app._shazam_sync_progress = {
            'running': False,
            'done': done_count, 'failed': failed_count,
            'message': f"Search done: {done_count} found, {failed_count} not found (of {total_count}).",
            'mode': 'search_global', 'search_mode': search_mode, 'total': total_count,
            'not_found': dict(status.get('not_found') or {}),
            'urls': dict(status.get('urls') or {}),
            'soundeo_titles': dict(status.get('soundeo_titles') or {}),
            'soundeo_match_scores': dict(status.get('soundeo_match_scores') or {}),
            'starred': dict(status.get('starred') or {}),
        }
    except Exception as e:
        app._shazam_sync_progress = {'running': False, 'error': str(e), 'mode': 'search_global'}


@app.route('/api/shazam-sync/sync-favorites-from-soundeo', methods=['POST'])
def shazam_sync_favorites_from_soundeo():
    """Crawl https://soundeo.com/account/favorites and sync starred state into app (source of truth). Runs in background. Body: { time_range: 'all'|'1_month'|'2_months'|'3_months' } to limit pages scanned (uses selected time range)."""
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    time_range = 'all'
    if request.get_data():
        try:
            data = request.get_json(silent=True) or {}
            time_range = data.get('time_range') or 'all'
        except Exception:
            pass
    app._shazam_sync_favorites_time_range = time_range
    app._shazam_sync_progress = {'running': True, 'message': 'Starting...', 'current': 0, 'total': 0, 'mode': 'sync_favorites'}
    thread = threading.Thread(target=_run_sync_favorites_from_soundeo, daemon=True)
    thread.start()
    return jsonify({'status': 'started', 'message': 'Syncing favorites from Soundeo. Poll /api/shazam-sync/progress for status.', 'time_range': time_range})


@app.route('/api/shazam-sync/search-soundeo-single', methods=['POST'])
def shazam_search_soundeo_single():
    """Search one track on Soundeo (no favorite). Body: { artist, title } or { track_key }. Queued if another op is running. Poll /api/shazam-sync/progress."""
    if not hasattr(app, '_shazam_single_search_queue'):
        app._shazam_single_search_queue = []
    if not hasattr(app, '_shazam_single_search_queue_lock'):
        app._shazam_single_search_queue_lock = threading.Lock()
    try:
        data = request.get_json(silent=True) or {}
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
        track_key = (data.get('track_key') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if track_key and ' - ' in track_key:
        parts = track_key.split(' - ', 1)
        artist, title = (parts[0] or '').strip(), (parts[1] or '').strip()
    if not artist and not title:
        return jsonify({'error': 'Missing artist/title or track_key'}), 400
    item = {'artist': artist, 'title': title}
    with app._shazam_single_search_queue_lock:
        app._shazam_single_search_queue.append(item)
        queue = list(app._shazam_single_search_queue)
    running = _shazam_any_job_running()
    if running:
        return jsonify({
            'status': 'queued',
            'position': len(queue),
            'message': f'Queued: {artist} - {title}',
            'single_search_queue': [{'artist': q['artist'], 'title': q['title']} for q in queue],
        })
    # Not running: pop the one we just added (it is the first) and start it, unless compare is running
    with app._shazam_single_search_queue_lock:
        if not app._shazam_single_search_queue:
            return jsonify({'status': 'started', 'message': f'Searching: {artist} - {title}', 'single_search_queue': []})
        next_item = app._shazam_single_search_queue.pop(0)
        queue_after = list(app._shazam_single_search_queue)
        if getattr(app, '_shazam_compare_running', False):
            app._shazam_single_search_queue.insert(0, next_item)
            return jsonify({
                'status': 'queued',
                'position': len(app._shazam_single_search_queue),
                'message': f'Queued: {next_item["artist"]} - {next_item["title"]} (compare/rescan in progress)',
                'single_search_queue': [{'artist': q['artist'], 'title': q['title']} for q in app._shazam_single_search_queue],
            })
        app._shazam_single_search_queue = queue_after
    a, t = next_item['artist'], next_item['title']
    app._shazam_sync_progress = {'running': True, 'message': f'Searching: {a} - {t}', 'mode': 'search_single'}
    thread = threading.Thread(target=_run_search_soundeo_single, args=(a, t), daemon=True)
    thread.start()
    return jsonify({'status': 'started', 'message': f'Searching Soundeo for: {a} - {t}', 'single_search_queue': [{'artist': q['artist'], 'title': q['title']} for q in queue_after]})


@app.route('/api/shazam-sync/search-soundeo-global', methods=['POST'])
def shazam_search_soundeo_global():
    """Search all (global). Body: { search_mode: 'unfound'|'new' } — unfound = orange-dot only, new = grey-dot only; omit = both. Runs in background. Poll /api/shazam-sync/progress."""
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    search_mode = None
    if request.get_data():
        try:
            data = request.get_json(silent=True) or {}
            search_mode = data.get('search_mode') or None
            if search_mode not in ('unfound', 'new', None):
                search_mode = None
        except Exception:
            pass
    app._shazam_sync_progress = {'running': True, 'message': 'Starting search…', 'current': 0, 'total': None, 'mode': 'search_global', 'search_mode': search_mode}
    thread = threading.Thread(target=_run_search_soundeo_global, args=(search_mode,), daemon=True)
    thread.start()
    return jsonify({'status': 'started', 'message': 'Searching Soundeo. Poll /api/shazam-sync/progress.', 'search_mode': search_mode})


def _filter_tracks_by_time_range(tracks: list, time_range: Optional[str]) -> list:
    """Filter tracks to shazamed_at >= cutoff for the given time range. Returns list unchanged if time_range is all or missing."""
    if not time_range or time_range == 'all' or not tracks:
        return tracks
    now = int(time.time())
    one_month = 30 * 86400
    two_months = 60 * 86400
    three_months = 91 * 86400
    if time_range == '1_month':
        cutoff = now - one_month
    elif time_range == '2_months':
        cutoff = now - two_months
    elif time_range == '3_months':
        cutoff = now - three_months
    else:
        return tracks
    return [t for t in tracks if (t.get('shazamed_at') or 0) >= cutoff]


@app.route('/api/shazam-sync/run-soundeo', methods=['POST'])
def shazam_sync_run_soundeo():
    """Start background Soundeo automation. Uses selected tracks from body, else to_download filtered by time_range."""
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    tracks = None
    time_range = 'all'
    if request.get_data():
        try:
            data = request.get_json(silent=True) or {}
            if data.get('tracks'):
                tracks = data['tracks']
            time_range = data.get('time_range') or 'all'
        except Exception:
            pass
    if not tracks:
        status = getattr(app, '_shazam_sync_status', None)
        if not status or not status.get('to_download'):
            return jsonify({'error': 'No tracks to sync. Run Compare first or select tracks.'}), 400
        tracks = _filter_tracks_by_time_range(status['to_download'], time_range)
        if not tracks:
            return jsonify({'error': f'No tracks to sync in the selected time range ({time_range}). Try "All time" or run Compare.'}), 400
    # So favorites crawl during sync only scans pages needed for this time range
    app._shazam_sync_run_time_range = time_range
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
    # Update in-memory status: remove skipped from to_download, add to skipped_tracks
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status:
        from shazam_cache import load_skip_list, save_status_cache
        to_download = app._shazam_sync_status.get('to_download', [])
        key_to_track = {(t['artist'].strip().lower(), t['title'].strip().lower()): t for t in to_download}
        newly_skipped = []
        for t in tracks:
            key = (t.get('artist') or '').strip().lower(), (t.get('title') or '').strip().lower()
            full = key_to_track.get(key)
            entry = {'artist': full['artist'], 'title': full['title']} if full else {'artist': t.get('artist', ''), 'title': t.get('title', '')}
            if full and full.get('shazamed_at') is not None:
                entry['shazamed_at'] = full['shazamed_at']
            newly_skipped.append(entry)
        skipped = load_skip_list()
        to_dl = [t for t in to_download if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        app._shazam_sync_status['to_download'] = to_dl
        app._shazam_sync_status['to_download_count'] = len(to_dl)
        app._shazam_sync_status['skipped_tracks'] = (app._shazam_sync_status.get('skipped_tracks') or []) + newly_skipped
        save_status_cache(app._shazam_sync_status)
    return jsonify({'skipped': added, 'message': f'Added {added} track(s) to skip list'})


@app.route('/api/shazam-sync/unskip', methods=['POST'])
def shazam_sync_unskip():
    """Remove tracks from skip list. Body: { tracks: [{artist, title}, ...] }."""
    try:
        data = request.get_json(silent=True) or {}
        tracks = data.get('tracks', [])
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not tracks:
        return jsonify({'error': 'No tracks to unskip'}), 400
    from shazam_cache import remove_from_skip_list, load_skip_list, save_status_cache
    removed = remove_from_skip_list(tracks)
    # Update in-memory status: move unskipped back to to_download, remove from skipped_tracks
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status:
        skipped = load_skip_list()
        to_download = list(app._shazam_sync_status.get('to_download', []))
        skipped_tracks = list(app._shazam_sync_status.get('skipped_tracks', []))
        still_skipped = [t for t in skipped_tracks if (t['artist'].strip().lower(), t['title'].strip().lower()) in skipped]
        now_to_dl = [t for t in skipped_tracks if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        merged = _dedupe_tracks_by_key(to_download + now_to_dl)
        app._shazam_sync_status['to_download'] = merged
        app._shazam_sync_status['to_download_count'] = len(merged)
        app._shazam_sync_status['skipped_tracks'] = still_skipped
        save_status_cache(app._shazam_sync_status)
    return jsonify({'unskipped': removed, 'message': f'Removed {removed} track(s) from skip list'})


@app.route('/api/shazam-sync/dismiss-track', methods=['POST'])
def shazam_sync_dismiss_track():
    """Dismiss a track: unstar on Soundeo via API + mark as dismissed locally.
    Body: { key: "Artist - Title", track_url: "https://soundeo.com/track/..." }"""
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import soundeo_api_set_favorite
    from shazam_cache import save_status_cache, load_status_cache

    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        track_url = (data.get('track_url') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing track key'}), 400

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = _status_url_for_key(status, key)

    soundeo_result = None
    cookies_path = get_soundeo_cookies_path()
    track_id = _resolve_track_id(status, key, track_url or '', cookies_path)
    if track_id:
        _soundeo_star_log.info("Soundeo dismiss: trying HTTP set_favorite favored=False for key=%s", key[:60] if key else "")
        soundeo_result = soundeo_api_set_favorite(track_id, cookies_path, favored=False, track_url=track_url or None)
        if soundeo_result and soundeo_result.get('ok'):
            _soundeo_star_log.info("Soundeo dismiss: HTTP succeeded")
        else:
            _soundeo_star_log.info("Soundeo dismiss: HTTP failed (%s), trying crawler", (soundeo_result or {}).get('error', 'unknown'))
    else:
        if track_url:
            _soundeo_star_log.info("Soundeo dismiss: skipping HTTP (no track_id for URL), using crawler")
    if track_url and not (soundeo_result and soundeo_result.get('ok')):
        from config_shazam import load_config
        from soundeo_automation import _get_driver, load_cookies, unfavorite_track_on_soundeo, _graceful_quit
        config = load_config()
        headed = config.get('headed_mode', True)
        driver = None
        try:
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path, initial_url=track_url):
                if unfavorite_track_on_soundeo(driver, track_url):
                    soundeo_result = {'ok': True, 'result': 'unfavored'}
                    _soundeo_star_log.info("Soundeo dismiss: crawler succeeded")
                else:
                    _soundeo_star_log.warning("Soundeo dismiss: crawler failed")
            else:
                _soundeo_star_log.warning("Soundeo dismiss: crawler skipped (cookies failed)")
        except Exception as e:
            _soundeo_star_log.warning("Soundeo dismiss: crawler error: %s", e)
        finally:
            _graceful_quit(driver)

    if soundeo_result and soundeo_result.get('ok') and track_url and cookies_path:
        _verify_soundeo_favorite_state(track_url, cookies_path, expected_favored=False, key=key)

    status.setdefault('dismissed', {})
    status['dismissed'][key] = True
    if key and key.lower() != key:
        status['dismissed'][key.lower()] = True
    status.setdefault('starred', {})
    status['starred'][key] = False
    app._shazam_sync_status = status
    save_status_cache(status)

    return jsonify({
        'ok': True,
        'key': key,
        'soundeo_ok': bool(soundeo_result and soundeo_result.get('ok')),
    })


def _unstar_one_track_impl(key: str, track_url: str):
    """Perform unstar on Soundeo for one track; update status cache. Returns (soundeo_ok: bool)."""
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import soundeo_api_set_favorite
    from shazam_cache import save_status_cache, load_status_cache

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = _status_url_for_key(status, key)
    soundeo_result = None
    cookies_path = get_soundeo_cookies_path()
    track_id = _resolve_track_id(status, key, track_url or '', cookies_path)
    if track_id:
        soundeo_result = soundeo_api_set_favorite(track_id, cookies_path, favored=False, track_url=track_url or None)
        if not (soundeo_result and soundeo_result.get('ok')):
            _soundeo_star_log.info("Soundeo unstar: HTTP failed (%s), trying crawler", (soundeo_result or {}).get('error', 'unknown'))
    else:
        if track_url:
            _soundeo_star_log.info("Soundeo unstar: skipping HTTP (no track_id for URL), using crawler")
    if track_url and not (soundeo_result and soundeo_result.get('ok')):
        from config_shazam import load_config
        from soundeo_automation import _get_driver, load_cookies, unfavorite_track_on_soundeo, _graceful_quit
        config = load_config()
        headed = config.get('headed_mode', True)
        driver = None
        try:
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path, initial_url=track_url):
                if unfavorite_track_on_soundeo(driver, track_url):
                    soundeo_result = {'ok': True, 'result': 'unfavored'}
        except Exception:
            pass
        finally:
            _graceful_quit(driver)
    soundeo_ok = bool(soundeo_result and soundeo_result.get('ok'))
    if soundeo_result and soundeo_result.get('ok') and track_url and cookies_path:
        _verify_soundeo_favorite_state(track_url, cookies_path, expected_favored=False, key=key)
    status.setdefault('starred', {})
    status['starred'][key] = False
    status['starred'][key.lower()] = False
    app._shazam_sync_status = status
    save_status_cache(status)
    return soundeo_ok


def _start_next_single_unstar() -> None:
    """If unstar queue has items, pop one and run in background."""
    unstar_lock = getattr(app, '_shazam_single_unstar_queue_lock', None)
    if unstar_lock is None:
        return
    with unstar_lock:
        queue = getattr(app, '_shazam_single_unstar_queue', None) or []
        if not queue:
            prog = getattr(app, '_shazam_sync_progress', None) or {}
            key = prog.get('key') or prog.get('current_key')
            app._shazam_sync_progress = dict(prog, running=False, mode='unstar_single', key=key)
            return
        item = queue.pop(0)
        app._shazam_single_unstar_queue = queue
    # Expose completed key with running=False so frontend can clear pending before next worker overwrites
    prog = getattr(app, '_shazam_sync_progress', None) or {}
    completed_key = prog.get('key') or prog.get('current_key')
    if completed_key:
        app._shazam_sync_progress = dict(prog, running=False, mode='unstar_single', key=completed_key)
    thread = threading.Thread(target=_run_unstar_queue_worker, args=(item,), daemon=True)
    thread.start()


def _run_unstar_queue_worker(item: Dict) -> None:
    """Background: unstar one track, then start next in queue."""
    key = (item.get('key') or '').strip() or f"{item.get('artist', '')} - {item.get('title', '')}"
    track_url = (item.get('track_url') or '').strip()
    artist = (item.get('artist') or '').strip()
    title = (item.get('title') or '').strip()
    try:
        app._shazam_sync_progress = {
            'running': True, 'mode': 'unstar_single', 'current_key': key,
            'message': f'Unstarring: {artist} - {title}', 'key': key,
        }
        soundeo_ok = _unstar_one_track_impl(key, track_url)
        app._shazam_sync_progress = {
            'running': True, 'mode': 'unstar_single', 'current_key': key,
            'message': f'Unstarring: {artist} - {title}', 'key': key,
            'done': 1 if soundeo_ok else 0, 'failed': 0 if soundeo_ok else 1,
        }
    except Exception as e:
        app._shazam_sync_progress = {
            'running': True, 'mode': 'unstar_single', 'current_key': key,
            'message': f'Unstarring: {artist} - {title}', 'key': key,
            'done': 0, 'failed': 1, 'error': str(e),
        }
    _start_next_single_unstar()


@app.route('/api/shazam-sync/unstar-track', methods=['POST'])
def shazam_sync_unstar_track():
    """Unstar a track on Soundeo only; add to queue like star. Body: { key, track_url?, artist?, title? }."""
    from shazam_cache import load_status_cache

    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        track_url = (data.get('track_url') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing track key'}), 400

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = _status_url_for_key(status, key)
    if not track_url:
        return jsonify({'error': 'No track URL. Run Search first to get a link.'}), 400

    if not hasattr(app, '_shazam_single_unstar_queue'):
        app._shazam_single_unstar_queue = []
    if not hasattr(app, '_shazam_single_unstar_queue_lock'):
        app._shazam_single_unstar_queue_lock = threading.Lock()

    item = {'key': key, 'track_url': track_url, 'artist': artist, 'title': title}
    with app._shazam_single_unstar_queue_lock:
        app._shazam_single_unstar_queue.append(item)
        queue = list(app._shazam_single_unstar_queue)

    running = _shazam_any_job_running()
    if running:
        return jsonify({
            'ok': True,
            'status': 'queued',
            'message': f'Queued unstar: {artist} - {title}',
            'unstar_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in queue],
        })

    with app._shazam_single_unstar_queue_lock:
        if not app._shazam_single_unstar_queue:
            return jsonify({'ok': True, 'status': 'started', 'unstar_queue': []})
        next_item = app._shazam_single_unstar_queue.pop(0)
        queue_after = list(app._shazam_single_unstar_queue)
        if getattr(app, '_shazam_compare_running', False):
            app._shazam_single_unstar_queue.insert(0, next_item)
            return jsonify({
                'status': 'queued',
                'message': f'Queued unstar (compare in progress)',
                'unstar_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in app._shazam_single_unstar_queue],
            })
        app._shazam_single_unstar_queue = queue_after

    thread = threading.Thread(target=_run_unstar_queue_worker, args=(next_item,), daemon=True)
    thread.start()
    return jsonify({
        'ok': True,
        'status': 'started',
        'message': f'Unstarring: {next_item.get("artist")} - {next_item.get("title")}',
        'unstar_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in queue_after],
    })


@app.route('/api/shazam-sync/clear-dismissed', methods=['POST'])
def shazam_sync_clear_dismissed():
    """Clear dismissed state for a track so the link shows again (no strikethrough). Does not change starred on Soundeo.
    Body: { key }."""
    from shazam_cache import save_status_cache, load_status_cache

    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing track key'}), 400

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    dismissed = dict(status.get('dismissed') or {})
    dismissed.pop(key, None)
    dismissed.pop(key.lower(), None)
    status['dismissed'] = dismissed
    app._shazam_sync_status = status
    save_status_cache(status)

    return jsonify({'ok': True, 'key': key})


def _status_url_for_key(status: Dict, key: str) -> str:
    """Resolve track URL from status urls using key and common variants (match frontend lookup)."""
    urls = status.get('urls') or {}
    key_lower = key.lower()
    key_endash = key.replace(' - ', ' – ')
    key_norm = key.split(' (')[0].strip() if ' (' in key else key
    key_norm_lower = key_norm.lower()
    for k in (key, key_lower, key_endash, key_norm, key_norm_lower):
        if k in urls and urls[k]:
            return (urls[k] or '').strip()
    return ''


def _resolve_track_id(status: Dict, key: str, track_url: str, cookies_path: str) -> Optional[str]:
    """Resolve Soundeo track ID from status cache, URL, or by fetching the track page. Mutates status to store ID when found (single source of truth)."""
    from soundeo_automation import extract_track_id, get_track_id_from_page
    key_lower = key.lower() if isinstance(key, str) else ''
    tid = (status.get('track_ids') or {}).get(key) or (status.get('track_ids') or {}).get(key_lower)
    if tid:
        return tid
    if not track_url:
        return None
    tid = extract_track_id(track_url)
    if tid:
        status.setdefault('track_ids', {})[key] = tid
        status['track_ids'][key_lower] = tid
        return tid
    tid = get_track_id_from_page(track_url, cookies_path)
    if tid:
        status.setdefault('track_ids', {})[key] = tid
        status['track_ids'][key_lower] = tid
        return tid
    return None


def _set_url_and_track_id(status: Dict, key: str, url: str, cookies_path: Optional[str] = None) -> None:
    """Set track URL and resolve/store track ID so we have both (URL for preview/crawler, ID for HTTP). Single source of truth: status cache."""
    if not url or not key:
        return
    status.setdefault('urls', {})
    status['urls'][key] = status['urls'][key.lower()] = url
    if not cookies_path:
        return
    key_lower = key.lower() if isinstance(key, str) else ''
    if (status.get('track_ids') or {}).get(key) or (status.get('track_ids') or {}).get(key_lower):
        return
    from soundeo_automation import extract_track_id, get_track_id_from_page
    tid = extract_track_id(url) or get_track_id_from_page(url, cookies_path)
    if tid:
        status.setdefault('track_ids', {})[key] = tid
        status['track_ids'][key_lower] = tid


def _verify_soundeo_favorite_state(
    track_url: str, cookies_path: str, expected_favored: bool, key: str = ""
) -> None:
    """After a star/unstar, verify state on Soundeo and log. expected_favored=True means we expect starred."""
    if not track_url or not cookies_path:
        return
    try:
        from soundeo_automation import soundeo_api_get_favorite_state
        state = soundeo_api_get_favorite_state(track_url, cookies_path)
        if state is None:
            _soundeo_star_log.info("Soundeo: could not verify state on site for %s", key or track_url[:60])
        elif state == expected_favored:
            _soundeo_star_log.info("Soundeo: verified on site favored=%s for %s", state, key or track_url[:60])
        else:
            _soundeo_star_log.warning(
                "Soundeo: state mismatch: expected favored=%s, got %s for %s",
                expected_favored, state, key or track_url[:60],
            )
    except Exception as e:
        _soundeo_star_log.warning("Soundeo: verification failed: %s", e)


@app.route('/api/shazam-sync/undismiss-track', methods=['POST'])
def shazam_sync_undismiss_track():
    """Undo dismiss: re-star on Soundeo via API (then browser fallback if needed) + remove dismissed state.
    Body: { key, track_url?, artist?, title? }"""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import (
        soundeo_api_set_favorite,
        soundeo_api_search_and_favorite,
        _get_driver, load_cookies, favorite_track_by_url, _graceful_quit,
    )
    from shazam_cache import save_status_cache, load_status_cache

    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        track_url = (data.get('track_url') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing track key'}), 400

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = _status_url_for_key(status, key)

    cookies_path = get_soundeo_cookies_path()
    soundeo_ok = False
    new_url = track_url

    # 1) HTTP first when we have a track URL/ID (from status cache, URL, or page fetch)
    track_id = _resolve_track_id(status, key, track_url or '', cookies_path)
    if track_id:
        _soundeo_star_log.info("Soundeo undismiss: trying HTTP set_favorite favored=True for key=%s", key[:60] if key else "")
        result = soundeo_api_set_favorite(track_id, cookies_path, favored=True, track_url=track_url or None)
        if result.get('ok') and (result.get('result') or '').lower() == 'favored':
            soundeo_ok = True
            _soundeo_star_log.info("Soundeo undismiss: HTTP succeeded")
        else:
            _soundeo_star_log.info("Soundeo undismiss: HTTP failed (%s), trying crawler", (result or {}).get('error', 'unknown'))
    else:
        if track_url:
            _soundeo_star_log.info("Soundeo undismiss: skipping HTTP (no track_id for URL), using crawler")
    # 2) Crawler fallback when we have URL but HTTP didn't succeed
    if not soundeo_ok and track_url:
        config = load_config()
        headed = config.get('headed_mode', True)
        driver = None
        try:
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path, initial_url=track_url):
                soundeo_ok = favorite_track_by_url(driver, track_url)
                if soundeo_ok:
                    _soundeo_star_log.info("Soundeo undismiss: crawler succeeded")
                else:
                    _soundeo_star_log.warning("Soundeo undismiss: crawler failed")
            else:
                _soundeo_star_log.warning("Soundeo undismiss: crawler skipped (cookies failed)")
        except Exception as e:
            _soundeo_star_log.warning("Soundeo undismiss: crawler error: %s", e)
        finally:
            _graceful_quit(driver)
    if not soundeo_ok and artist and title and not track_url:
        result = soundeo_api_search_and_favorite(artist, title, cookies_path)
        if result.get('ok'):
            soundeo_ok = True
            new_url = result.get('url', track_url)
            if new_url:
                _set_url_and_track_id(status, key, new_url, cookies_path)
            if result.get('display_text'):
                status.setdefault('soundeo_titles', {})[key] = result['display_text']

    # 3) Browser find-and-favorite when we have artist/title but no URL or both above failed
    if not soundeo_ok and artist and title:
        config = load_config()
        headed = config.get('headed_mode', True)
        driver = None
        try:
            from soundeo_automation import find_and_favorite_track
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path):
                out = find_and_favorite_track(driver, artist, title, already_starred=set())
                if out:
                    soundeo_ok = True
                    new_url = out[0] if isinstance(out, tuple) else out
                    if new_url:
                        _set_url_and_track_id(status, key, new_url, cookies_path)
                    if isinstance(out, tuple) and len(out) > 1 and out[1]:
                        status.setdefault('soundeo_titles', {})[key] = out[1]
        except Exception:
            pass
        finally:
            _graceful_quit(driver)

    if soundeo_ok and cookies_path:
        verify_url = new_url or track_url
        if verify_url:
            _verify_soundeo_favorite_state(verify_url, cookies_path, expected_favored=True, key=key)

    dismissed = dict(status.get('dismissed') or {})
    dismissed.pop(key, None)
    if key:
        dismissed.pop(key.lower(), None)
    status['dismissed'] = dismissed
    status.setdefault('starred', {})
    status['starred'][key] = True
    app._shazam_sync_status = status
    save_status_cache(status)

    return jsonify({
        'ok': True,
        'key': key,
        'soundeo_ok': soundeo_ok,
        'url': new_url,
    })


def _star_one_track_impl(key: str, track_url: str, artist: str, title: str) -> Tuple[bool, str]:
    """Star one track on Soundeo. Uses app._shazam_sync_status; updates it and saves cache. Returns (success, new_url)."""
    from config_shazam import get_soundeo_cookies_path, load_config
    from soundeo_automation import (
        soundeo_api_set_favorite,
        soundeo_api_search_and_favorite,
        _get_driver, load_cookies, favorite_track_by_url, _graceful_quit,
    )
    from shazam_cache import save_status_cache, load_status_cache

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = (status.get('urls') or {}).get(key, '') or _status_url_for_key(status, key)
    cookies_path = get_soundeo_cookies_path()
    config = load_config()
    headed = config.get('headed_mode', True)
    soundeo_ok = False
    new_url = track_url

    # HTTP first when we have a track ID (from status cache, URL, or page fetch)
    track_id = _resolve_track_id(status, key, track_url or '', cookies_path)
    if track_id:
        _soundeo_star_log.info("Soundeo star: trying HTTP set_favorite favored=True for key=%s", key[:60] if key else "")
        result = soundeo_api_set_favorite(track_id, cookies_path, favored=True, track_url=track_url or None)
        if result.get('ok') and (result.get('result') or '').lower() == 'favored':
            soundeo_ok = True
            _soundeo_star_log.info("Soundeo star: HTTP succeeded")
        else:
            _soundeo_star_log.info("Soundeo star: HTTP failed (%s), trying crawler", (result or {}).get('error', 'unknown'))
    else:
        if track_url:
            _soundeo_star_log.info("Soundeo star: skipping HTTP (no track_id for URL), using crawler")
    # Crawler fallback when we have URL but HTTP didn't succeed
    if not soundeo_ok and track_url:
        driver = None
        try:
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path, initial_url=track_url):
                soundeo_ok = favorite_track_by_url(driver, track_url)
                if soundeo_ok:
                    _soundeo_star_log.info("Soundeo star: crawler succeeded")
                else:
                    _soundeo_star_log.warning("Soundeo star: crawler failed")
            else:
                _soundeo_star_log.warning("Soundeo star: crawler skipped (cookies failed)")
        except Exception as e:
            _soundeo_star_log.warning("Soundeo star: crawler error: %s", e)
        finally:
            _graceful_quit(driver)

    if not soundeo_ok and artist and title and not track_url:
        result = soundeo_api_search_and_favorite(artist, title, cookies_path)
        if result.get('ok'):
            soundeo_ok = True
            new_url = result.get('url', '')
            if new_url:
                _set_url_and_track_id(status, key, new_url, cookies_path)
            if result.get('display_text'):
                status.setdefault('soundeo_titles', {})[key] = result['display_text']

    if not soundeo_ok and artist and title:
        driver = None
        try:
            from soundeo_automation import find_and_favorite_track
            driver = _get_driver(headless=not headed, use_persistent_profile=True)
            if load_cookies(driver, cookies_path):
                out = find_and_favorite_track(driver, artist, title, already_starred=set())
                if out:
                    soundeo_ok = True
                    new_url = out[0] if isinstance(out, tuple) else out
                    if new_url:
                        _set_url_and_track_id(status, key, new_url, cookies_path)
                    if isinstance(out, tuple) and len(out) > 1 and out[1]:
                        status.setdefault('soundeo_titles', {})[key] = out[1]
        except Exception:
            pass
        finally:
            _graceful_quit(driver)

    if soundeo_ok and cookies_path:
        verify_url = new_url or track_url
        if verify_url:
            _verify_soundeo_favorite_state(verify_url, cookies_path, expected_favored=True, key=key)

    status.setdefault('starred', {})
    status['starred'][key] = True
    status['starred'][key.lower()] = True
    app._shazam_sync_status = status
    save_status_cache(status)
    return soundeo_ok, (new_url or track_url)


def _run_star_batch_background(tracks: List[Dict]) -> None:
    """Background thread: star each track, update progress, respect stop request."""
    from soundeo_automation import clear_sync_stop_request, is_sync_stop_requested

    clear_sync_stop_request()
    total = len(tracks)
    done = 0
    failed = 0
    for i, t in enumerate(tracks):
        if is_sync_stop_requested():
            app._shazam_sync_progress = {
                'running': False, 'stopped': True, 'done': done, 'failed': failed,
                'message': f'Stopped. Starred {done}, failed {failed}.',
            }
            return
        key = (t.get('key') or '').strip() or f"{t.get('artist', '')} - {t.get('title', '')}"
        artist = (t.get('artist') or '').strip()
        title = (t.get('title') or '').strip()
        track_url = (t.get('track_url') or '').strip()
        app._shazam_sync_progress = {
            'running': True, 'mode': 'star_batch', 'current': i + 1, 'total': total,
            'current_key': key, 'message': f'Starring {i + 1}/{total}: {artist} - {title}',
        }
        try:
            ok, _ = _star_one_track_impl(key, track_url, artist, title)
            if ok:
                done += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    app._shazam_sync_progress = {
        'running': False, 'done': done, 'failed': failed,
        'message': f'Done. Starred {done}, failed {failed}.',
    }


@app.route('/api/shazam-sync/star-batch', methods=['POST'])
def shazam_sync_star_batch():
    """Star multiple tracks on Soundeo (tracks must have URL). Runs in background. Poll /api/shazam-sync/progress. Body: { tracks: [{ key?, track_url?, artist, title }, ...] }."""
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    try:
        data = request.get_json(silent=True) or {}
        tracks = data.get('tracks') or []
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not tracks:
        return jsonify({'error': 'No tracks to star. Select tracks with a Soundeo link first.'}), 400
    status = getattr(app, '_shazam_sync_status', None) or {}
    urls = status.get('urls') or {}
    out = []
    for t in tracks:
        key = (t.get('key') or '').strip() or f"{t.get('artist', '')} - {t.get('title', '')}"
        url = (t.get('track_url') or '').strip() or urls.get(key, '') or urls.get(key.lower(), '')
        if not url:
            continue
        out.append({'key': key, 'track_url': url, 'artist': (t.get('artist') or '').strip(), 'title': (t.get('title') or '').strip()})
    if not out:
        return jsonify({'error': 'None of the selected tracks have a Soundeo link. Run Search first.'}), 400
    app._shazam_sync_progress = {'running': True, 'mode': 'star_batch', 'current': 0, 'total': len(out), 'message': f'Starring 0/{len(out)}…'}
    thread = threading.Thread(target=_run_star_batch_background, args=(out,), daemon=True)
    thread.start()
    return jsonify({'status': 'started', 'total': len(out), 'message': f'Starring {len(out)} track(s). Poll /api/shazam-sync/progress.'})


def _start_next_single_star() -> None:
    """If single-star queue has items, pop one and run it in a background thread."""
    star_queue = getattr(app, '_shazam_single_star_queue', None) or []
    star_lock = getattr(app, '_shazam_single_star_queue_lock', None)
    if star_lock is None:
        return
    with star_lock:
        star_queue = getattr(app, '_shazam_single_star_queue', None) or []
        if not star_queue:
            prog = getattr(app, '_shazam_sync_progress', None) or {}
            key = prog.get('key') or prog.get('current_key')
            app._shazam_sync_progress = dict(prog, running=False, mode='star_single', key=key)
            return
        item = star_queue.pop(0)
        app._shazam_single_star_queue = star_queue
    # Expose completed key with running=False so frontend can clear pending before next worker overwrites
    prog = getattr(app, '_shazam_sync_progress', None) or {}
    completed_key = prog.get('key') or prog.get('current_key')
    if completed_key:
        app._shazam_sync_progress = dict(prog, running=False, mode='star_single', key=completed_key)
    thread = threading.Thread(target=_run_star_queue_worker, args=(item,), daemon=True)
    thread.start()


def _run_star_queue_worker(item: Dict) -> None:
    """Background: star one track, update progress, then start next in queue."""
    key = (item.get('key') or '').strip() or f"{item.get('artist', '')} - {item.get('title', '')}"
    track_url = (item.get('track_url') or '').strip()
    artist = (item.get('artist') or '').strip()
    title = (item.get('title') or '').strip()
    star_lock = getattr(app, '_shazam_single_star_queue_lock', None)
    try:
        app._shazam_sync_progress = {
            'running': True, 'mode': 'star_single', 'current_key': key,
            'message': f'Starring: {artist} - {title}',
            'key': key,
        }
        soundeo_ok, new_url = _star_one_track_impl(key, track_url, artist, title)
        if star_lock:
            with star_lock:
                q = list(getattr(app, '_shazam_single_star_queue', None) or [])
            app._shazam_sync_progress = {
                'running': True, 'mode': 'star_single', 'current_key': key,
                'message': f'Starring: {artist} - {title}',
                'key': key, 'done': 1 if soundeo_ok else 0, 'failed': 0 if soundeo_ok else 1,
                'url': new_url, 'starred': soundeo_ok,
            }
    except Exception as e:
        app._shazam_sync_progress = {
            'running': True, 'mode': 'star_single', 'current_key': key,
            'message': f'Starring: {artist} - {title}', 'key': key,
            'done': 0, 'failed': 1, 'error': str(e),
        }
    _start_next_single_star()


@app.route('/api/shazam-sync/star-track', methods=['POST'])
def shazam_sync_star_track():
    """Star one track: add to queue, show progress/queue bar. Body: { key, track_url?, artist?, title? }."""
    from shazam_cache import load_status_cache

    if not hasattr(app, '_shazam_single_star_queue'):
        app._shazam_single_star_queue = []
    if not hasattr(app, '_shazam_single_star_queue_lock'):
        app._shazam_single_star_queue_lock = threading.Lock()

    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        track_url = (data.get('track_url') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing track key'}), 400

    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    if not track_url:
        track_url = _status_url_for_key(status, key)
    if not track_url and not (artist and title):
        return jsonify({'error': 'No track URL and no artist/title. Run Search on Soundeo first to get a link.'}), 400

    item = {'key': key, 'track_url': track_url, 'artist': artist, 'title': title}
    with app._shazam_single_star_queue_lock:
        app._shazam_single_star_queue.append(item)
        queue = list(app._shazam_single_star_queue)

    running = _shazam_any_job_running()
    if running:
        return jsonify({
            'status': 'queued',
            'message': f'Queued: {artist} - {title}',
            'star_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in queue],
        })

    with app._shazam_single_star_queue_lock:
        if not app._shazam_single_star_queue:
            return jsonify({'ok': True, 'status': 'started', 'star_queue': []})
        next_item = app._shazam_single_star_queue.pop(0)
        queue_after = list(app._shazam_single_star_queue)
        if getattr(app, '_shazam_compare_running', False):
            app._shazam_single_star_queue.insert(0, next_item)
            return jsonify({
                'status': 'queued',
                'message': f'Queued: {next_item.get("artist")} - {next_item.get("title")} (compare in progress)',
                'star_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in app._shazam_single_star_queue],
            })
        app._shazam_single_star_queue = queue_after

    thread = threading.Thread(target=_run_star_queue_worker, args=(next_item,), daemon=True)
    thread.start()
    return jsonify({
        'status': 'started',
        'message': f'Starring: {next_item.get("artist")} - {next_item.get("title")}',
        'star_queue': [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in queue_after],
    })


@app.route('/api/shazam-sync/remove-from-star-queue', methods=['POST'])
def shazam_remove_from_star_queue():
    """Remove one item from the star queue by key or artist+title. Body: { key } or { artist, title }."""
    if not hasattr(app, '_shazam_single_star_queue'):
        return jsonify({'ok': True, 'star_queue': []})
    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
        if not key and (artist or title):
            key = f"{artist} - {title}"
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing key or artist+title'}), 400
    key_lower = key.lower()
    with app._shazam_single_star_queue_lock:
        queue = getattr(app, '_shazam_single_star_queue', None) or []
        new_queue = []
        removed = False
        for q in queue:
            qk = (q.get('key') or '').strip()
            if not qk:
                qk = f"{q.get('artist', '')} - {q.get('title', '')}"
            if not removed and (qk == key or qk.lower() == key_lower):
                removed = True
                continue
            new_queue.append(q)
        app._shazam_single_star_queue = new_queue
        out = [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in new_queue]
    return jsonify({'ok': True, 'star_queue': out})


@app.route('/api/shazam-sync/remove-from-search-queue', methods=['POST'])
def shazam_remove_from_search_queue():
    """Remove one item from the single-search queue by artist+title. Body: { artist, title } or { key }."""
    if not hasattr(app, '_shazam_single_search_queue'):
        return jsonify({'ok': True, 'single_search_queue': []})
    try:
        data = request.get_json(silent=True) or {}
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
        key = (data.get('key') or '').strip()
        if not (artist and title) and key and ' - ' in key:
            parts = key.split(' - ', 1)
            artist, title = (parts[0] or '').strip(), (parts[1] or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not artist and not title:
        return jsonify({'error': 'Missing artist+title or key'}), 400
    with app._shazam_single_search_queue_lock:
        queue = getattr(app, '_shazam_single_search_queue', None) or []
        new_queue = []
        removed = False
        for q in queue:
            if not removed and (q.get('artist') or '').strip() == artist and (q.get('title') or '').strip() == title:
                removed = True
                continue
            new_queue.append(q)
        app._shazam_single_search_queue = new_queue
        out = [{'artist': q['artist'], 'title': q['title']} for q in new_queue]
    return jsonify({'ok': True, 'single_search_queue': out})


@app.route('/api/shazam-sync/remove-from-unstar-queue', methods=['POST'])
def shazam_remove_from_unstar_queue():
    """Remove one item from the unstar queue by key or artist+title. Body: { key } or { artist, title }."""
    if not hasattr(app, '_shazam_single_unstar_queue'):
        return jsonify({'ok': True, 'unstar_queue': []})
    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
        if not key and (artist or title):
            key = f"{artist} - {title}"
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key:
        return jsonify({'error': 'Missing key or artist+title'}), 400
    key_lower = key.lower()
    with app._shazam_single_unstar_queue_lock:
        queue = getattr(app, '_shazam_single_unstar_queue', None) or []
        new_queue = []
        removed = False
        for q in queue:
            qk = (q.get('key') or '').strip()
            if not qk:
                qk = f"{q.get('artist', '')} - {q.get('title', '')}"
            if not removed and (qk == key or qk.lower() == key_lower):
                removed = True
                continue
            new_queue.append(q)
        app._shazam_single_unstar_queue = new_queue
        out = [{'artist': q.get('artist', ''), 'title': q.get('title', ''), 'key': q.get('key', '')} for q in new_queue]
    return jsonify({'ok': True, 'unstar_queue': out})


@app.route('/api/shazam-sync/sync-single-track', methods=['POST'])
def shazam_sync_single_track():
    """Find and star a single track on Soundeo (browser, same as Run Soundeo). Runs in background. Poll /api/shazam-sync/progress. Body: { key, artist, title }"""
    if _shazam_any_job_running():
        return jsonify({'error': 'Another operation is already running. It has been queued.'}), 400
    try:
        data = request.get_json(silent=True) or {}
        key = (data.get('key') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not artist and not title:
        if key and ' - ' in key:
            artist, title = key.split(' - ', 1)
            artist, title = artist.strip(), title.strip()
        else:
            return jsonify({'error': 'Missing artist/title'}), 400
    if not key:
        key = f"{artist} - {title}"

    app._shazam_sync_progress = {'running': True, 'message': f'Starting: {artist} - {title}…', 'mode': 'sync_single'}
    thread = threading.Thread(target=_run_sync_single_track_browser, args=(artist, title), daemon=True)
    thread.start()
    return jsonify({'status': 'started', 'message': f'Finding & starring: {artist} - {title}. Poll /api/shazam-sync/progress.'})


@app.route('/api/shazam-sync/skip-track', methods=['POST'])
def shazam_sync_skip_single_track():
    """Skip a single track. Body: { artist, title }"""
    try:
        data = request.get_json(silent=True) or {}
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not artist and not title:
        return jsonify({'error': 'Missing artist/title'}), 400

    from shazam_cache import add_to_skip_list, load_skip_list, save_status_cache
    added = add_to_skip_list([{'artist': artist, 'title': title}])

    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status:
        to_download = app._shazam_sync_status.get('to_download', [])
        key = (artist.lower(), title.lower())
        full = next((t for t in to_download if t['artist'].strip().lower() == key[0] and t['title'].strip().lower() == key[1]), None)
        entry = {'artist': full['artist'], 'title': full['title']} if full else {'artist': artist, 'title': title}
        if full and full.get('shazamed_at') is not None:
            entry['shazamed_at'] = full['shazamed_at']
        skipped = load_skip_list()
        to_dl = [t for t in to_download if (t['artist'].strip().lower(), t['title'].strip().lower()) not in skipped]
        app._shazam_sync_status['to_download'] = to_dl
        app._shazam_sync_status['to_download_count'] = len(to_dl)
        app._shazam_sync_status['skipped_tracks'] = (app._shazam_sync_status.get('skipped_tracks') or []) + [entry]
        save_status_cache(app._shazam_sync_status)

    return jsonify({'ok': True, 'message': f'Skipped: {artist} - {title}'})


@app.route('/api/shazam-sync/remove-from-soundeo', methods=['POST'])
def shazam_sync_remove_from_soundeo():
    """Remove a track from Soundeo favorites (unfavorite on the site). Body: { track_url: "..." } or { track_key: "..." } or { artist, title }."""
    try:
        data = request.get_json(silent=True) or {}
        track_url = (data.get('track_url') or '').strip()
        track_key = (data.get('track_key') or '').strip()
        artist = (data.get('artist') or '').strip()
        title = (data.get('title') or '').strip()
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400

    status = getattr(app, '_shazam_sync_status', None) or {}
    urls = status.get('urls') or {}
    if not track_url and (artist or title):
        track_key = track_key or f"{artist} - {title}"
        track_url = urls.get(track_key) or urls.get(track_key.strip())
    if not track_url or not track_url.startswith(('https://soundeo.com/', 'http://soundeo.com/')):
        return jsonify({'error': 'No Soundeo track URL. Provide track_url or artist+title (track must be in status).'}), 400

    if not track_key and (artist or title):
        track_key = f"{artist} - {title}"
    if not track_key:
        for k, v in urls.items():
            if (v or '').strip() == track_url:
                track_key = k
                break

    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import _get_driver, load_cookies, unfavorite_track_on_soundeo, _is_redirected_to_login, _graceful_quit

    cookies_path = get_soundeo_cookies_path()
    if not os.path.exists(cookies_path):
        return jsonify({'error': 'No saved Soundeo session. Save session first in Settings.'}), 400

    driver = None
    try:
        driver = _get_driver(headless=False, use_persistent_profile=True)
    except Exception as e:
        return jsonify({'error': f'Could not start browser: {e}. Close any Chrome window using the Soundeo profile, then try again.'}), 500

    try:
        if not load_cookies(driver, cookies_path):
            _graceful_quit(driver)
            return jsonify({'error': 'No saved session. Save session first in Settings.'}), 400
        ok = unfavorite_track_on_soundeo(driver, track_url)
        if not ok and _is_redirected_to_login(driver):
            _graceful_quit(driver)
            return jsonify({'error': 'Soundeo session expired or you are logged out. Please use Save session to log in again.'}), 403
        _graceful_quit(driver)
    except Exception as e:
        _graceful_quit(driver)
        return jsonify({'error': str(e)}), 500

    if not ok:
        return jsonify({'error': 'Could not unfavorite on Soundeo (button not found or not favorited).'}), 400

    # Update local status: mark track as not starred and persist
    from shazam_cache import load_status_cache, save_status_cache
    status = dict(load_status_cache() or getattr(app, '_shazam_sync_status', None) or {})
    starred = dict(status.get('starred') or {})
    if track_key:
        starred[track_key] = False
        if isinstance(track_key, str):
            starred[track_key.lower()] = False
        status['starred'] = starred
        app._shazam_sync_status = status
        save_status_cache(status)
    return jsonify({'ok': True, 'message': 'Removed from Soundeo favorites', 'track_key': track_key or None})


@app.route('/api/shazam-sync/mutation-log', methods=['GET'])
def shazam_sync_mutation_log():
    """Return the starred/unstarred mutation log (most recent first)."""
    from shazam_cache import load_mutation_log
    log = load_mutation_log()
    log.reverse()
    limit = request.args.get('limit', 200, type=int)
    return jsonify({'mutations': log[:limit], 'total': len(log)})


@app.route('/api/shazam-sync/cleanup-matches', methods=['POST'])
def shazam_sync_cleanup_matches():
    """Re-score all cached Soundeo matches and remove those below the current threshold.

    Updates both in-memory status and file cache. Returns counts of kept/removed.
    """
    from shazam_cache import save_status_cache
    from soundeo_automation import _best_match_score, _extended_preference_bonus, _MATCH_THRESHOLD

    status = dict(getattr(app, '_shazam_sync_status', None) or {})
    if not status:
        from shazam_cache import load_status_cache
        status = dict(load_status_cache() or {})
    if not status:
        return jsonify({'error': 'No status to clean'}), 400

    urls = status.get('urls', {})
    titles = status.get('soundeo_titles', {})
    not_found = status.setdefault('not_found', {})
    new_scores = {}
    keys_to_remove = set()
    kept = 0
    removed = 0

    for key in list(urls.keys()):
        url = urls[key]
        soundeo_title = titles.get(key, '')
        if not soundeo_title or not url or ' - ' not in key:
            continue
        artist, title = key.split(' - ', 1)
        base_score = _best_match_score({}, soundeo_title, artist, title)
        bonus = _extended_preference_bonus(soundeo_title)
        total = base_score + bonus
        if total < _MATCH_THRESHOLD:
            keys_to_remove.add(key)
            keys_to_remove.add(key.lower())
            removed += 1
        else:
            new_scores[key] = round(total, 3)
            new_scores[key.lower()] = round(total, 3)
            kept += 1

    for k in keys_to_remove:
        urls.pop(k, None)
        titles.pop(k, None)
        not_found.pop(k, None)
        not_found.pop(k.lower() if isinstance(k, str) else k, None)

    status['urls'] = urls
    status['soundeo_titles'] = titles
    status['soundeo_match_scores'] = new_scores
    status['not_found'] = not_found
    app._shazam_sync_status = status
    save_status_cache(status)

    return jsonify({'kept': kept, 'removed': removed, 'threshold': _MATCH_THRESHOLD})


@app.route('/api/shazam-sync/reset-not-found', methods=['POST'])
def shazam_sync_reset_not_found():
    """One-time: clear the not_found paper trail so all no-link tracks show grey.
    From then on, only Search all / per-row Search will set orange (searched, not found)."""
    from shazam_cache import load_status_cache, save_status_cache
    status = dict(getattr(app, '_shazam_sync_status', None) or load_status_cache() or {})
    status['not_found'] = {}
    app._shazam_sync_status = status
    save_status_cache(status)
    return jsonify({'ok': True, 'message': 'Not-found state cleared. All no-link tracks will show grey until you run Search again.'})


@app.route('/api/shazam-sync/rebuild-from-search-log', methods=['POST'])
def shazam_sync_rebuild_from_search_log():
    """Replay search_outcomes in the status cache to rebuild urls and not_found. Use if state was corrupted. Single source of truth = status cache only."""
    from shazam_cache import load_status_cache, save_status_cache, rebuild_status_from_search_log
    existing = load_status_cache()
    log = (existing or {}).get("search_outcomes") or []
    if not log:
        return jsonify({'ok': False, 'error': 'No search_outcomes in status cache.'}), 400
    rebuilt = rebuild_status_from_search_log(existing)
    app._shazam_sync_status = rebuilt
    save_status_cache(rebuilt)
    return jsonify({
        'ok': True,
        'message': f'Rebuilt from {len(log)} search_outcomes. Refresh to see correct dots.',
        'log_entries': len(log),
        'urls_count': len(rebuilt.get('urls') or {}),
        'not_found_count': len(rebuilt.get('not_found') or {}),
    })


@app.route('/api/shazam-sync/dismiss-manual-check', methods=['POST'])
def shazam_sync_dismiss_manual_check():
    """Dismiss the Manual check message for a track (persisted in status cache)."""
    try:
        data = request.get_json(silent=True) or {}
        key = data.get('track_key')
        if not key and data.get('artist') is not None and data.get('title') is not None:
            key = f"{data['artist']} - {data['title']}"
    except Exception:
        return jsonify({'error': 'Invalid request'}), 400
    if not key or not key.strip():
        return jsonify({'error': 'track_key or artist+title required'}), 400
    key = key.strip()
    from shazam_cache import load_status_cache, save_status_cache
    status = load_status_cache() or {}
    dismissed = set(status.get('dismissed_manual_check') or [])
    dismissed.add(key)
    status['dismissed_manual_check'] = list(dismissed)
    save_status_cache(status)
    if hasattr(app, '_shazam_sync_status') and app._shazam_sync_status:
        app._shazam_sync_status['dismissed_manual_check'] = status['dismissed_manual_check']
    return jsonify({'dismissed': key, 'dismissed_manual_check': status['dismissed_manual_check']})



@app.route('/api/shazam-sync/check-login', methods=['POST'])
def shazam_sync_check_login():
    """
    Check if Soundeo session is logged in using the same browser automation as Sync/Scan.
    Opens browser, loads saved cookies, loads favorites page and infers login from redirect (no account page).
    Use this to confirm whether login is saved and the profile is active.
    """
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import _get_driver, load_cookies, check_logged_in, _graceful_quit

    cookies_path = get_soundeo_cookies_path()
    profile_info = _get_soundeo_chrome_profile_info()
    if not os.path.exists(cookies_path):
        return jsonify({
            'logged_in': False,
            'message': 'No saved session. Use Save session to log in and save cookies.',
            'cookies_path': cookies_path,
            'profile_used': None,
            **profile_info,
        })

    driver = None
    try:
        driver = _get_driver(headless=False, use_persistent_profile=True)
    except Exception as e:
        return jsonify({
            'logged_in': False,
            'message': f'Browser could not start: {e}. Close all Chrome windows (the app uses the same profile as Chrome), then try again.',
            'cookies_path': cookies_path,
            'profile_used': None,
            **profile_info,
        })

    try:
        if not load_cookies(driver, cookies_path):
            _graceful_quit(driver)
            return jsonify({
                'logged_in': False,
                'message': 'Cookies file exists but could not be loaded into browser.',
                'cookies_path': cookies_path,
                'profile_used': 'configured profile',
            })
        logged_in = check_logged_in(driver)
        _graceful_quit(driver)
        if logged_in:
            return jsonify({
                'logged_in': True,
                'message': 'You are logged in to Soundeo. Sync and Scan will use this session.',
                'cookies_path': cookies_path,
                'profile_used': 'configured profile',
                **profile_info,
            })
        return jsonify({
            'logged_in': False,
            'message': 'Session expired or logged out. Use Save session to log in again. (Same browser profile is used for Save and for Sync.)',
            'cookies_path': cookies_path,
            'profile_used': 'configured profile',
            **profile_info,
        })
    except Exception as e:
        _graceful_quit(driver)
        return jsonify({
            'logged_in': False,
            'message': str(e),
            'cookies_path': cookies_path,
            'profile_used': 'configured profile',
            **profile_info,
        }), 500


@app.route('/api/soundeo/start-save-session', methods=['POST'])
def soundeo_start_save_session():
    """Start save-session flow: opens browser for user to log in."""
    from config_shazam import get_soundeo_cookies_path
    from soundeo_automation import run_save_session_flow, create_save_session_event, signal_save_session_done
    import soundeo_automation as _snd

    old_thread = getattr(app, '_save_session_thread', None)
    if old_thread and old_thread.is_alive():
        signal_save_session_done()
        old_thread.join(timeout=5)

    cookies_path = get_soundeo_cookies_path()
    app._soundeo_save_session_cookies_path = cookies_path
    done_event = create_save_session_event()
    def run():
        run_save_session_flow(cookies_path, headed=True, done_event=done_event)
    app._save_session_thread = threading.Thread(target=run, daemon=True)
    app._save_session_thread.start()
    import time as _time
    _time.sleep(3)
    err = getattr(_snd, '_save_session_last_error', None)
    if err:
        return jsonify({
            'error': 'Browser could not start.',
            'detail': err,
        }), 500
    return jsonify({'status': 'started'})


@app.route('/api/soundeo/browser-check', methods=['GET'])
def soundeo_browser_check():
    """Try to create the Soundeo browser driver (headless). Returns config mode and success or the actual error for diagnosis. 20s timeout."""
    from config_shazam import get_soundeo_browser_config
    from soundeo_automation import _get_driver, _graceful_quit
    _result = []

    def _run():
        try:
            d = _get_driver(headless=True, use_persistent_profile=True)
            _result.append(("ok", d))
        except Exception as e:
            _result.append(("err", str(e)))

    cfg = get_soundeo_browser_config()
    mode = cfg.get("mode", "launch")
    out = {"mode": mode}
    if mode == "attach":
        out["debugger_address"] = cfg.get("debugger_address", "127.0.0.1:9222")
        out["hint"] = "Attach mode: no new window. Start Chrome with: chrome --remote-debugging-port=9222"
    else:
        out["user_data_dir"] = cfg.get("user_data_dir", "")
        out["hint"] = "Launch mode: Chrome should open. If not, see error below or close other Chrome windows using this profile."
    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=20)
    if not _result:
        out["ok"] = False
        out["error"] = "Timed out after 20s (Chrome may be slow or profile in use). Connect Soundeo now uses a temporary profile — try that; a window should open."
        return jsonify(out)
    status, val = _result[0]
    if status == "err":
        out["ok"] = False
        out["error"] = val
        return jsonify(out)
    try:
        _graceful_quit(val)
    except Exception:
        pass
    out["ok"] = True
    out["message"] = "Browser driver OK (headless test)."
    return jsonify(out)


@app.route('/api/soundeo/session-saved', methods=['POST'])
def soundeo_session_saved():
    """User has logged in - signal save-session flow to save cookies, verify login, persist path."""
    from soundeo_automation import signal_save_session_done
    from config_shazam import get_soundeo_cookies_path, load_config, save_config

    signal_save_session_done()
    cookies_path = getattr(app, '_soundeo_save_session_cookies_path', None) or get_soundeo_cookies_path()
    config = load_config()
    config['soundeo_cookies_path'] = cookies_path
    save_config(config)

    # Wait for the save-session thread to finish saving cookies
    save_thread = getattr(app, '_save_session_thread', None)
    if save_thread and save_thread.is_alive():
        save_thread.join(timeout=10)

    # Verify the saved session actually works
    logged_in = False
    if os.path.exists(cookies_path):
        try:
            from soundeo_automation import _get_driver, load_cookies, check_logged_in, _graceful_quit
            driver = _get_driver(headless=True, use_persistent_profile=True)
            try:
                if load_cookies(driver, cookies_path):
                    logged_in = check_logged_in(driver)
            finally:
                _graceful_quit(driver)
        except Exception:
            pass

    if logged_in:
        return jsonify({'status': 'ok', 'logged_in': True, 'message': 'Session saved and verified.'})
    return jsonify({'status': 'ok', 'logged_in': False, 'message': 'Cookies saved but login could not be verified. Try logging in again.'})


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
    from shazam_cache import STATUS_CACHE_PATH
    import logging
    logging.info("SoundBridge status cache (single source of truth): %s", STATUS_CACHE_PATH)
    app.run(debug=True, port=5002, host='127.0.0.1', threaded=True, use_reloader=False)

