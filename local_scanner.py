"""
Scan local music folders and match against Shazam tracks.
Supports MP3, AIFF, WAV. Uses tags when available, else filename parsing.
"""
import os
import re
import json
from typing import List, Dict, Set, Tuple, Optional, Callable

# Debug: count unmatched so we only log first N (set by compute_to_download)
_unmatched_diagnostic_count = [0]

# Supported extensions for scanning
AUDIO_EXTENSIONS = ('.mp3', '.aiff', '.aif', '.wav')


def _get_audio_metadata(filepath: str) -> Dict:
    """Extract artist/title from MP3, AIFF, or WAV. Uses mutagen; fallback to empty dict."""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath, easy=True)
        if audio is None:
            return {}
        # Easy ID3: artist, title are lists
        artist = (audio.get('artist') or [''])[0] if isinstance(audio.get('artist'), list) else (audio.get('artist') or '')
        title = (audio.get('title') or [''])[0] if isinstance(audio.get('title'), list) else (audio.get('title') or '')
        return {'artist': str(artist).strip(), 'title': str(title).strip()}
    except Exception:
        return {}


# Common track suffixes to strip for matching (expand so Shazam "Title" matches file "Title (Extended Version)" etc.)
TRACK_SUFFIXES = re.compile(
    r'\s*[(\[]?(?:Extended Mix|Extended Version|Extended|Original Mix|Original Version|Radio Edit|Album Version|'
    r'Instrumental|Remix|Edit|Mix|Dub Mix|Dub|Vocal|Acoustic|Version|\(feat\.[^)]*\))[\s)\]]*',
    re.IGNORECASE
)


def normalize(s: str) -> str:
    """Normalize for matching: lowercase, strip, collapse whitespace, remove suffixes."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r'\s+', ' ', s)
    s = TRACK_SUFFIXES.sub('', s)
    return s.strip()


def _artist_tokens(artist: str) -> Set[str]:
    """Meaningful tokens from artist string (split on & , and space; min 2 chars)."""
    if not artist:
        return set()
    s = normalize(artist)
    for sep in ['&', ',', ' and ']:
        s = s.replace(sep, ' ')
    return set(w for w in s.split() if len(w) > 1)


def _artist_overlap_or_in_filename(shazam_artist: str, local_artist_norm: str, filename_lower: str) -> bool:
    """True if any Shazam artist token appears in local artist or in filename (e.g. 'CamelPhat' in 'CamelPhat - Cycles.aiff')."""
    tokens = _artist_tokens(shazam_artist)
    if not tokens:
        return False
    local_tokens = _artist_tokens(local_artist_norm) if local_artist_norm else set()
    if tokens & local_tokens:
        return True
    for t in tokens:
        if t in filename_lower:
            return True
    return False


def _canon(s: str) -> str:
    """Strip to letters, digits, spaces only (for simple name-vs-name matching). Dots removed so R.E.Z. matches REZ."""
    if not s:
        return ""
    s = normalize(s)
    s = s.replace('.', '')  # R.E.Zarin -> rezarin so it matches REZarin
    s = re.sub(r'[^a-z0-9\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _canon_match(a: str, b: str) -> bool:
    """True if canonical forms are equal or one contains the other (same name, different punctuation)."""
    ca, cb = _canon(a), _canon(b)
    if not ca or not cb:
        return ca == cb
    if ca == cb:
        return True
    return ca in cb or cb in ca


def parse_artist_title_from_filename(filename: str) -> Tuple[str, str]:
    """Parse 'Artist - Title' from filename. Returns (artist, title). Handles clean filenames."""
    name = os.path.splitext(filename)[0]
    # Prefer multi-char separators (space-dash-space, en/em dash, underscore)
    for sep in [' - ', ' – ', ' — ', ' _ ', '_-_']:
        if sep in name:
            parts = name.split(sep, 1)
            if len(parts) == 2:
                return (parts[0].strip(), parts[1].strip())
    # Clean filenames often use single hyphen: "Artist-Title" or "Artist - Title" (already handled above)
    if '-' in name:
        parts = name.split('-', 1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return (parts[0].strip(), parts[1].strip())
    return ('', name)


class ScanCancelled(Exception):
    """Raised when scan is cancelled by user."""


def scan_folders(
    folder_paths: List[str],
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    use_filename_only: bool = False,
) -> List[Dict]:
    """
    Scan folders for MP3, AIFF, WAV. Returns list of track info:
    {artist, title, filepath, filename, from_tags}
    use_filename_only=True: parse artist/title from filename only (fast, no file I/O).
    use_filename_only=False: read ID3/tags with mutagen, fallback to filename.
    on_progress(current, total) called every 50 files; (0, 0) at start before building file list. should_cancel() if True aborts (raises ScanCancelled).
    """
    if on_progress:
        on_progress(0, 0)  # "Discovering files..." phase before we know total
    file_list = []
    for folder in folder_paths:
        if not folder or not os.path.isdir(folder):
            continue
        for root, _, files in os.walk(folder):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in AUDIO_EXTENSIONS:
                    continue
                filepath = os.path.join(root, f)
                file_list.append((filepath, f))
    total = len(file_list)
    tracks = []
    last_n = [0]

    def _report(n: int):
        if on_progress and total > 0 and (n - last_n[0] >= 50 or n == total):
            last_n[0] = n
            on_progress(n, total)

    if on_progress and total > 0:
        on_progress(0, total)
    for i, (filepath, f) in enumerate(file_list):
        if should_cancel and should_cancel():
            raise ScanCancelled()
        if use_filename_only:
            artist, title = parse_artist_title_from_filename(f)
            tracks.append({
                'artist': artist,
                'title': title,
                'filepath': filepath,
                'filename': f,
                'from_tags': False,
            })
        else:
            try:
                info = _get_audio_metadata(filepath)
                artist = (info.get('artist') or '').strip()
                title = (info.get('title') or '').strip()
                if not artist and not title:
                    artist, title = parse_artist_title_from_filename(f)
                tracks.append({
                    'artist': artist,
                    'title': title,
                    'filepath': filepath,
                    'filename': f,
                    'from_tags': bool(info.get('artist') or info.get('title')),
                })
            except Exception:
                artist, title = parse_artist_title_from_filename(f)
                tracks.append({
                    'artist': artist,
                    'title': title,
                    'filepath': filepath,
                    'filename': f,
                    'from_tags': False,
                })
        _report(i + 1)
    return tracks


def _build_local_keys(local_tracks: List[Dict]) -> Set[Tuple[str, str]]:
    """Build set of normalized (artist, title) pairs for fast lookup."""
    keys = set()
    for t in local_tracks:
        a, ti = normalize(t['artist']), normalize(t['title'])
        if a or ti:
            keys.add((a, ti))
    return keys


def _build_title_word_index(local_tracks: List[Dict]) -> Dict[str, List[int]]:
    """Word -> list of local_tracks indices. Used to limit fuzzy match candidates."""
    index: Dict[str, List[int]] = {}
    for i, t in enumerate(local_tracks):
        ti = normalize(t['title'])
        for w in ti.split():
            if len(w) > 1:
                w = w.lower()
                index.setdefault(w, []).append(i)
    return index


def _build_exact_match_map(local_tracks: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    """(normalized_artist, normalized_title) -> one local track. For O(1) exact match."""
    out: Dict[Tuple[str, str], Dict] = {}
    for lt in local_tracks:
        key = (normalize(lt['artist']), normalize(lt['title']))
        if key not in out:
            out[key] = lt
    return out


def _track_matches(
    track: Dict,
    local_tracks: List[Dict],
    sim_threshold: float = 0.7,
    title_word_index: Optional[Dict[str, List[int]]] = None,
    exact_match_map: Optional[Dict[Tuple[str, str], Dict]] = None,
    local_canon: Optional[List[Tuple[str, str]]] = None,
) -> bool:
    """Check if track (artist, title) exists in local_tracks."""
    return _find_matching_local_track(
        track, local_tracks, sim_threshold, title_word_index, exact_match_map, local_canon
    ) is not None


def _find_matching_local_track(
    track: Dict,
    local_tracks: List[Dict],
    sim_threshold: float = 0.7,
    title_word_index: Optional[Dict[str, List[int]]] = None,
    exact_match_map: Optional[Dict[Tuple[str, str], Dict]] = None,
    local_canon: Optional[List[Tuple[str, str]]] = None,
) -> Optional[Dict]:
    """
    Find matching local track for shazam track. Returns local track dict (with filepath) or None.
    If title_word_index is provided, fuzzy match only checks local tracks that share a title word (much faster).
    If exact_match_map is provided, exact match is O(1) and avoids rebuilding local_keys per call.
    If local_canon is provided (list of (canon_title, canon_artist) per local track), run full canonical
    match first so we never miss a "name vs name" match when the candidate set was capped or empty.
    """
    from app import similarity_score
    n_artist = normalize(track['artist'])
    n_title = normalize(track['title'])

    # Exact normalized match (O(1) when exact_match_map provided)
    if exact_match_map is not None:
        hit = exact_match_map.get((n_artist, n_title))
        if hit is not None:
            return hit
    else:
        local_keys = _build_local_keys(local_tracks)
        if (n_artist, n_title) in local_keys:
            for lt in local_tracks:
                if (normalize(lt['artist']), normalize(lt['title'])) == (n_artist, n_title):
                    return lt

    # Full canonical "name vs name" pass so we never miss due to index/cap (script found 15 such misses)
    if local_canon is not None and len(local_canon) == len(local_tracks):
        st, sa = _canon(track['title']), _canon(track['artist'])
        for (lt, la), lt_dict in zip(local_canon, local_tracks):
            if (st == lt or st in lt or lt in st) and (sa == la or sa in la or la in sa):
                return lt_dict

    # Fuzzy: restrict candidates by title words when index available.
    # Use intersection of 2 smallest word-index sets to avoid huge candidate sets (e.g. "mix" in 10k tracks).
    was_capped = False
    title_words = [w.lower() for w in n_title.split() if len(w) > 1]
    if title_word_index and title_words:
        # Pairs (word, list of indices); sort by list size ascending
        word_lists = [(w, title_word_index.get(w, [])) for w in title_words]
        word_lists.sort(key=lambda x: len(x[1]))
        candidate_indices = set()
        if len(word_lists) >= 2:
            candidate_indices = set(word_lists[0][1]) & set(word_lists[1][1])
        if not candidate_indices and word_lists:
            # Fallback: union of all title words (e.g. no track had both words), then cap
            for _, idxs in word_lists:
                candidate_indices.update(idxs)
        # Cap to avoid slow fuzzy pass (take first 2000; set iteration order is arbitrary)
        if len(candidate_indices) > 2000:
            was_capped = True
            candidate_indices = set(list(candidate_indices)[:2000])
        candidates = [local_tracks[i] for i in candidate_indices]
    else:
        candidates = local_tracks

    # Compare: canonical "name vs name" first, then normalized + similarity
    for lt in candidates:
        if _canon_match(track['title'], lt['title']) and _canon_match(track['artist'], lt['artist']):
            return lt
        lt_artist_norm = normalize(lt['artist'])
        lt_title_norm = normalize(lt['title'])
        title_sim = similarity_score(n_title, lt_title_norm)
        artist_sim = similarity_score(n_artist, lt_artist_norm)
        fn_lower = (lt.get('filename') or lt.get('filepath', '').split(os.sep)[-1] or '').lower()
        artist_in_fn = _artist_overlap_or_in_filename(track['artist'], lt_artist_norm, fn_lower)

        if artist_sim >= sim_threshold and title_sim >= sim_threshold:
            return lt
        # Strong title: accept lower artist bar or artist token overlap (e.g. "Artist A & B" vs "Artist A" or filename)
        if title_sim >= 0.8 and artist_sim >= 0.5:
            return lt
        if title_sim >= 0.95 and artist_sim >= 0.4:
            return lt
        if title_sim >= 0.85 and artist_in_fn:
            return lt
        if title_sim >= 0.9 and (artist_sim >= 0.35 or artist_in_fn):
            return lt
        if n_artist and n_artist in fn_lower and title_sim >= 0.5:
            return lt
        if n_title and n_title in fn_lower and artist_sim >= 0.5:
            return lt

    # #region agent log
    _unmatched_diagnostic_count[0] += 1
    if _unmatched_diagnostic_count[0] <= 50:
        try:
            _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor')
            _log_path = os.path.join(_log_dir, 'debug.log')
            payload = {'location': 'local_scanner:_find_matching_local_track', 'message': 'unmatched', 'hypothesisId': 'H1H2H3', 'data': {'shazam_artist': track.get('artist', '')[:60], 'shazam_title': track.get('title', '')[:60], 'n_candidates': len(candidates), 'was_capped': was_capped}, 'timestamp': int(__import__('time').time() * 1000)}
            if candidates:
                best_raw_a = best_raw_t = best_norm_a = best_norm_t = 0.0
                for lt in candidates:
                    raw_a = similarity_score(n_artist, lt['artist'])
                    raw_t = similarity_score(n_title, lt['title'])
                    norm_a = similarity_score(n_artist, normalize(lt['artist']))
                    norm_t = similarity_score(n_title, normalize(lt['title']))
                    if raw_a > best_raw_a: best_raw_a = raw_a
                    if raw_t > best_raw_t: best_raw_t = raw_t
                    if norm_a > best_norm_a: best_norm_a = norm_a
                    if norm_t > best_norm_t: best_norm_t = norm_t
                payload['data'].update({'best_raw_artist': round(best_raw_a, 3), 'best_raw_title': round(best_raw_t, 3), 'best_norm_artist': round(best_norm_a, 3), 'best_norm_title': round(best_norm_t, 3)})
            with open(_log_path, 'a') as _f:
                _f.write(json.dumps(payload) + '\n')
        except Exception:
            pass
    # #endregion
    return None


def compute_to_download(
    shazam_tracks: List[Dict],
    local_tracks: List[Dict],
) -> Tuple[List[Dict], Dict[str, List[int]], Dict[Tuple[str, str], Dict], List[Tuple[str, str]]]:
    """
    Return (to_download, title_word_index, exact_match_map, local_canon). to_download = Shazam tracks NOT found locally.
    Pass title_word_index, exact_match_map, local_canon to _find_matching_local_track for matching.
    """
    # #region agent log
    _unmatched_diagnostic_count[0] = 0
    # #endregion
    title_word_index = _build_title_word_index(local_tracks)
    exact_match_map = _build_exact_match_map(local_tracks)
    local_canon = [(_canon(lt['title']), _canon(lt['artist'])) for lt in local_tracks]
    to_download = []
    for t in shazam_tracks:
        if not _track_matches(
            t, local_tracks, title_word_index=title_word_index, exact_match_map=exact_match_map, local_canon=local_canon
        ):
            item = {'artist': t['artist'], 'title': t['title']}
            if t.get('shazamed_at') is not None:
                item['shazamed_at'] = t['shazamed_at']
            to_download.append(item)
    # #region agent log
    try:
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cursor')
        _log_path = os.path.join(_log_dir, 'debug.log')
        with open(_log_path, 'a') as _f:
            _f.write(json.dumps({'location': 'local_scanner:compute_to_download', 'message': 'summary', 'hypothesisId': 'H1H2H3', 'data': {'to_download_count': len(to_download), 'shazam_count': len(shazam_tracks), 'local_count': len(local_tracks)}, 'timestamp': int(__import__('time').time() * 1000)}) + '\n')
    except Exception:
        pass
    # #endregion
    return to_download, title_word_index, exact_match_map, local_canon
