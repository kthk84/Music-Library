"""
Scan local music folders and match against Shazam tracks.
Supports MP3, AIFF, WAV. Uses tags when available, else filename parsing.
"""
import os
import re
import json
from typing import List, Dict, Set, Tuple, Optional, Callable

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


_APOSTROPHE_CHARS = '\u0027\u2018\u2019\u201a\u201b\u2032\u2033'  # straight, curly, primes


def _maybe_fix_mojibake(s: str) -> str:
    """Fix common UTF-8-as-Latin1 mojibake in metadata/filenames for matching."""
    if not s:
        return s
    if not any(ch in s for ch in ("Ã", "Â", "â")):
        return s
    try:
        fixed = s.encode("latin-1").decode("utf-8")
        if fixed and fixed != s:
            return fixed
    except Exception:
        pass
    return s


def normalize(s: str) -> str:
    """Normalize for matching: lowercase, strip, collapse whitespace, remove suffixes. Normalize apostrophes so N'Juno matches NʼJuno."""
    if not s:
        return ""
    s = _maybe_fix_mojibake(s)
    s = s.lower().strip()
    for c in _APOSTROPHE_CHARS:
        s = s.replace(c, "'")
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


def _parenthetical_part(s: str) -> str:
    """Extract content of last parenthetical (e.g. ' (Tom Zeta Maccabi Remix)' -> 'tom zeta maccabi remix')."""
    if not s:
        return ""
    s = s.strip()
    # Find last '(' and matching ')'
    start = s.rfind("(")
    if start == -1:
        return ""
    end = s.find(")", start)
    if end == -1:
        return ""
    return s[start + 1:end].strip().lower()


def _different_remix_or_version(shazam_title: str, local_title: str, sim_threshold: float = 0.65) -> bool:
    """
    True if both titles have a parenthetical part (e.g. remix name) and those parts differ significantly.
    Used to avoid matching "1977 (Tom Zeta Maccabi Remix)" to "1977 (Other Remix)".
    """
    p_shazam = _parenthetical_part(shazam_title)
    p_local = _parenthetical_part(local_title)
    if not p_shazam or not p_local:
        return False  # one or both have no parenthetical – allow match to be decided by other logic
    from app import similarity_score
    sim = similarity_score(p_shazam, p_local)
    return sim < sim_threshold


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


def _prefer_extended_track(tracks: List[Dict]) -> Optional[Dict]:
    """Given multiple matching tracks, prefer one with 'Extended' in title (explicit rule)."""
    if not tracks:
        return None
    for lt in tracks:
        if "extended" in (lt.get("title") or "").lower():
            return lt
    return tracks[0]


def _build_exact_match_map(local_tracks: List[Dict]) -> Dict[Tuple[str, str], Dict]:
    """(normalized_artist, normalized_title) -> one local track. For O(1) exact match. Prefer Extended when same key."""
    out: Dict[Tuple[str, str], List[Dict]] = {}
    for lt in local_tracks:
        key = (normalize(lt['artist']), normalize(lt['title']))
        out.setdefault(key, []).append(lt)
    return {k: _prefer_extended_track(v) for k, v in out.items()}


def _track_matches(
    track: Dict,
    local_tracks: List[Dict],
    sim_threshold: float = 0.7,
    title_word_index: Optional[Dict[str, List[int]]] = None,
    exact_match_map: Optional[Dict[Tuple[str, str], Dict]] = None,
    local_canon: Optional[List[Tuple[str, str]]] = None,
) -> bool:
    """Check if track (artist, title) exists in local_tracks."""
    match, _ = _find_matching_local_track(
        track, local_tracks, sim_threshold, title_word_index, exact_match_map, local_canon
    )
    return match is not None


def _find_matching_local_track(
    track: Dict,
    local_tracks: List[Dict],
    sim_threshold: float = 0.7,
    title_word_index: Optional[Dict[str, List[int]]] = None,
    exact_match_map: Optional[Dict[Tuple[str, str], Dict]] = None,
    local_canon: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[Optional[Dict], Optional[float]]:
    """
    Find matching local track for shazam track. Returns (local track dict with filepath, match_score) or (None, None).
    match_score is in [0, 1]; 1.0 = exact, lower = fuzzy. Used for "Manual check" when score < 0.8.
    """
    from app import similarity_score
    n_artist = normalize(track['artist'])
    n_title = normalize(track['title'])

    # Exact normalized match (O(1) when exact_match_map provided)
    if exact_match_map is not None:
        hit = exact_match_map.get((n_artist, n_title))
        if hit is not None:
            return (hit, 1.0)
    else:
        local_keys = _build_local_keys(local_tracks)
        if (n_artist, n_title) in local_keys:
            same_key = [lt for lt in local_tracks if (normalize(lt['artist']), normalize(lt['title'])) == (n_artist, n_title)]
            if same_key:
                return (_prefer_extended_track(same_key), 1.0)

    # Full canonical "name vs name" pass so we never miss due to index/cap (script found 15 such misses). Prefer Extended.
    # Require minimum length for containment to avoid false matches (e.g. "me" in "dimi mechero", "life" in "elysian life").
    _min_title_contain = 5
    _min_artist_contain = 3
    if local_canon is not None and len(local_canon) == len(local_tracks):
        st, sa = _canon(track['title']), _canon(track['artist'])
        def _canon_title_ok(s, t):
            if s == t:
                return True
            if s in t or t in s:
                return min(len(s), len(t)) >= _min_title_contain
            return False
        def _canon_artist_ok(s, t):
            if s == t:
                return True
            if s in t or t in s:
                return min(len(s), len(t)) >= _min_artist_contain
            return False
        canon_matches = [lt_dict for (lt, la), lt_dict in zip(local_canon, local_tracks)
                         if _canon_title_ok(st, lt) and _canon_artist_ok(sa, la)]
        if canon_matches:
            return (_prefer_extended_track(canon_matches), 0.95)

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

    # Compare: canonical "name vs name" first, then normalized + similarity.
    # Never match different remixes/versions: if both titles have parenthetical (e.g. remix name), they must agree.
    # Collect fuzzy matches then prefer Extended (explicit rule).
    fuzzy_matches: List[Tuple[Dict, float]] = []
    for lt in candidates:
        if _different_remix_or_version(track['title'], lt['title']):
            continue
        if _canon_match(track['title'], lt['title']) and _canon_match(track['artist'], lt['artist']):
            fuzzy_matches.append((lt, 0.95))
            continue
        lt_artist_norm = normalize(lt['artist'])
        lt_title_norm = normalize(lt['title'])
        title_sim = similarity_score(n_title, lt_title_norm)
        artist_sim = similarity_score(n_artist, lt_artist_norm)
        fn_lower = (lt.get('filename') or lt.get('filepath', '').split(os.sep)[-1] or '').lower()
        artist_in_fn = _artist_overlap_or_in_filename(track['artist'], lt_artist_norm, fn_lower)
        combined = 0.6 * title_sim + 0.4 * artist_sim
        score = round(combined, 2)

        if artist_sim >= sim_threshold and title_sim >= sim_threshold:
            fuzzy_matches.append((lt, score))
        elif title_sim >= 0.8 and artist_sim >= 0.5:
            fuzzy_matches.append((lt, score))
        elif title_sim >= 0.95 and artist_sim >= 0.4:
            fuzzy_matches.append((lt, score))
        elif title_sim >= 0.85 and artist_in_fn:
            fuzzy_matches.append((lt, score))
        elif title_sim >= 0.9 and (artist_sim >= 0.35 or artist_in_fn):
            fuzzy_matches.append((lt, score))
        elif n_artist and n_artist in fn_lower and title_sim >= 0.5:
            fuzzy_matches.append((lt, score))
        elif n_title and n_title in fn_lower and artist_sim >= 0.5:
            fuzzy_matches.append((lt, score))

    if fuzzy_matches:
        best = max(fuzzy_matches, key=lambda x: (x[1], 1 if "extended" in (x[0].get("title") or "").lower() else 0))
        return (best[0], best[1])

    return (None, None)


def compute_to_download(
    shazam_tracks: List[Dict],
    local_tracks: List[Dict],
) -> Tuple[List[Dict], Dict[str, List[int]], Dict[Tuple[str, str], Dict], List[Tuple[str, str]]]:
    """
    Return (to_download, title_word_index, exact_match_map, local_canon). to_download = Shazam tracks NOT found locally.
    Pass title_word_index, exact_match_map, local_canon to _find_matching_local_track for matching.
    """
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
    return to_download, title_word_index, exact_match_map, local_canon
