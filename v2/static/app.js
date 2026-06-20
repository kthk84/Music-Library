let currentFiles = [];
let processedCount = 0;
let successCount = 0;

const APP_STATE_KEY = 'mp3cleaner_app_state';
/** Bootstrap runs server-side reconcile + status merge; large libraries can exceed a short client timeout. */
const SHAZAM_BOOTSTRAP_TIMEOUT_MS = 120000;

/** Unified play/pause icon SVGs for consistent premium look (row = 12px, bar = 16px) */
const PLAY_ICON_ROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><use href="#icon-play"/></svg>';
const PAUSE_ICON_ROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><use href="#icon-pause"/></svg>';
const PLAY_ICON_BAR = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><use href="#icon-play"/></svg>';
const PAUSE_ICON_BAR = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><use href="#icon-pause"/></svg>';

function saveAppStateToStorage(state) {
    try {
        const existing = JSON.parse(localStorage.getItem(APP_STATE_KEY) || '{}');
        localStorage.setItem(APP_STATE_KEY, JSON.stringify({ ...existing, ...state }));
    } catch (e) {}
}

function loadAppStateFromStorage() {
    try {
        return JSON.parse(localStorage.getItem(APP_STATE_KEY) || '{}');
    } catch (e) { return {}; }
}

async function restoreAppState() {
    const input = document.getElementById('folderPath');
    if (!input) return;
    const fromStorage = loadAppStateFromStorage();
    if (fromStorage.last_folder_path) input.value = fromStorage.last_folder_path;
    try {
        const res = await fetch('/api/app-state');
        const data = await res.json();
        if (data && data.last_folder_path) input.value = data.last_folder_path;
    } catch (e) {}
}

// Browse for folder using native dialog
async function browseFolder() {
    showLoading('Opening folder browser...');
    
    try {
        const response = await fetch('/api/browse-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        const data = await response.json();
        hideLoading();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to select folder');
        }

        // Set the folder path in the input
        document.getElementById('folderPath').value = data.folder_path;
        
        // Auto-scan if folder was selected
        if (data.folder_path) {
            await scanFolder();
        }

    } catch (error) {
        hideLoading();
        alert(`Error: ${error.message}`);
    }
}

// Scan folder for MP3 files
async function scanFolder() {
    const folderPath = document.getElementById('folderPath').value.trim();
    
    if (!folderPath) {
        alert('Please enter a folder path');
        return;
    }

    showLoading('Scanning folder for MP3 files...');

    try {
        const response = await fetch('/api/scan-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ folder_path: folderPath })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to scan folder');
        }

        currentFiles = data.files;
        saveAppStateToStorage({ last_folder_path: folderPath, last_scan_count: data.files.length });

        // Store original state for each file (for revert)
        currentFiles.forEach(file => {
            file.original = {
                title: file.title,
                artist: file.artist,
                album: file.album,
                year: file.year,
                genre: file.genre,
                cover: file.cover
            };
        });
        
        processedCount = 0;
        successCount = 0;

        if (currentFiles.length === 0) {
            alert('No MP3 files found in the specified folder');
            hideLoading();
            return;
        }

        // Show step 2
        document.getElementById('step1').classList.remove('active');
        document.getElementById('step2').classList.add('active');

        // Check if any files have number prefixes
        const hasNumberPrefixes = currentFiles.some(f => f.has_number_prefix);
        if (hasNumberPrefixes) {
            document.getElementById('filenameAlert').style.display = 'flex';
        }

        // Check if any files have spam metadata
        const hasSpam = currentFiles.some(f => f.has_spam);
        if (hasSpam) {
            document.getElementById('spamAlert').style.display = 'flex';
        }

        updateStats();
        renderFileList();
        hideLoading();

    } catch (error) {
        hideLoading();
        alert(`Error: ${error.message}`);
    }
}

// Clean all filenames (remove number prefixes)
async function cleanAllFilenames() {
    const filesWithPrefixes = currentFiles.filter(f => f.has_number_prefix);
    
    if (filesWithPrefixes.length === 0) {
        alert('No files need filename cleaning!');
        return;
    }

    if (!confirm(`Remove track numbers from ${filesWithPrefixes.length} filename(s)?\n\nExample:\n"80. Beyoncé - Drunk in Love.mp3"\nwill become:\n"Beyoncé - Drunk in Love.mp3"`)) {
        return;
    }

    showLoading(`Cleaning ${filesWithPrefixes.length} filename(s)...`);

    try {
        const response = await fetch('/api/clean-filenames', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                filepaths: filesWithPrefixes.map(f => f.filepath)
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to clean filenames');
        }

        // Update file paths in currentFiles array
        data.results.forEach(result => {
            if (result.status === 'renamed') {
                const fileIndex = currentFiles.findIndex(f => f.filepath === result.old_filepath);
                if (fileIndex !== -1) {
                    currentFiles[fileIndex].filepath = result.new_filepath;
                    currentFiles[fileIndex].filename = result.new_filename;
                    currentFiles[fileIndex].has_number_prefix = false;
                }
            }
        });

        // Hide the alert
        document.getElementById('filenameAlert').style.display = 'none';

        // Re-render file list
        renderFileList();
        
        hideLoading();
        alert(`✅ Success!\n\nRenamed: ${data.success} file(s)\nUnchanged: ${data.unchanged} file(s)\nFailed: ${data.failed} file(s)`);

    } catch (error) {
        hideLoading();
        alert(`Error cleaning filenames: ${error.message}`);
    }
}

// Update statistics
function updateStats() {
    document.getElementById('totalFiles').textContent = currentFiles.length;
    document.getElementById('processedFiles').textContent = processedCount;
    document.getElementById('successFiles').textContent = successCount;
    
    // Calculate average confidence for files with lookup results
    const filesWithConfidence = currentFiles.filter(f => f.rank_score !== undefined || f.confidence !== undefined);
    if (filesWithConfidence.length > 0) {
        const avgScore = filesWithConfidence.reduce((sum, f) => {
            const score = f.rank_score !== undefined ? f.rank_score : f.confidence;
            return sum + score;
        }, 0) / filesWithConfidence.length;
        
        // Clamp to 0-100% range (scores can go above 1.0 due to bonuses)
        const avgPercentage = Math.round(Math.min(Math.max(avgScore * 100, 0), 100));
        document.getElementById('avgConfidence').textContent = `${avgPercentage}%`;
        
        // Color code the average
        const avgElement = document.getElementById('avgConfidence');
        if (avgPercentage >= 80) {
            avgElement.style.color = '#10b981';
        } else if (avgPercentage >= 60) {
            avgElement.style.color = '#f59e0b';
        } else {
            avgElement.style.color = '#ef4444';
        }
    } else {
        document.getElementById('avgConfidence').textContent = '—';
    }
}

// Render file list
function renderFileList() {
    const fileList = document.getElementById('fileList');
    fileList.innerHTML = '';

    // Add header
    const header = document.createElement('div');
    header.className = 'file-list-header';
    header.innerHTML = `
        <span style="text-align: center;">Cover</span>
        <span style="text-align: center;">Play</span>
        <span>Filename</span>
        <span>Title</span>
        <span>Artist</span>
        <span>Album</span>
        <span>Year</span>
        <span>Genre</span>
        <span style="text-align: center;" title="Match">%</span>
        <span>Actions</span>
    `;
    fileList.appendChild(header);

    currentFiles.forEach((file, index) => {
        const fileItem = createFileItem(file, index);
        fileList.appendChild(fileItem);
    });
}

// Create file item element
function createFileItem(file, index) {
    const div = document.createElement('div');
    div.className = 'file-item';
    div.id = `file-${index}`;

    const status = file.status || 'pending';
    const statusClass = status === 'success' ? 'success' : status === 'error' ? 'error' : status === 'processing' ? 'processing' : '';
    if (statusClass) {
        div.classList.add(statusClass);
    }

    if (file.has_spam || file.has_number_prefix) {
        div.classList.add('has-issues');
    }
    
    // Debug logging for cover
    if (file.cover || file.newCover) {
        console.log(`File ${index} (${file.filename}): has_cover=${file.has_cover}, cover_length=${(file.cover || file.newCover || '').substring(0, 50)}...`);
    }

    // Determine display values - show original above and new below if changed
    const titleHasChanged = file.newTitle && file.newTitle !== file.title;
    const artistHasChanged = file.newArtist && file.newArtist !== file.artist;
    const albumHasChanged = file.newAlbum && file.newAlbum !== file.album;
    const yearHasChanged = file.newYear && file.newYear !== file.year;
    const genreHasChanged = file.newGenre && file.newGenre !== file.genre;
    
    // Build display HTML with original (black, small) and new value below (green, bold)
    const titleDisplay = titleHasChanged 
        ? `<div style="font-size: 0.75rem; color: #1f2937; margin-bottom: 2px;">${file.title || '—'}</div><div style="color: var(--success); font-weight: 500;">${file.newTitle}</div>`
        : (file.newTitle || file.title || '—');
    
    const artistDisplay = artistHasChanged
        ? `<div style="font-size: 0.75rem; color: #1f2937; margin-bottom: 2px;">${file.artist || '—'}</div><div style="color: var(--success); font-weight: 500;">${file.newArtist}</div>`
        : (file.newArtist || file.artist || '—');
    
    const albumDisplay = albumHasChanged
        ? `<div style="font-size: 0.75rem; color: #1f2937; margin-bottom: 2px;">${file.album || '—'}</div><div style="color: var(--success); font-weight: 500;">${file.newAlbum}</div>`
        : (file.newAlbum || file.album || '—');
    
    const yearDisplay = yearHasChanged
        ? `<div style="font-size: 0.75rem; color: #1f2937; margin-bottom: 2px;">${file.year || '—'}</div><div style="color: var(--success); font-weight: 500;">${file.newYear}</div>`
        : (file.newYear || file.year || '—');
    
    const genreDisplay = genreHasChanged
        ? `<div style="font-size: 0.75rem; color: #1f2937; margin-bottom: 2px;">${file.genre || '—'}</div><div style="color: var(--success); font-weight: 500;">${file.newGenre}</div>`
        : (file.newGenre || file.genre || '—');

    const titleClass = titleHasChanged ? 'file-field updated' : ((file.newTitle || file.title) ? 'file-field' : 'file-field empty');
    const artistClass = artistHasChanged ? 'file-field updated' : ((file.newArtist || file.artist) ? 'file-field' : 'file-field empty');
    const albumClass = albumHasChanged ? 'file-field updated' : ((file.newAlbum || file.album) ? 'file-field' : 'file-field empty');
    const yearClass = yearHasChanged ? 'file-field updated' : ((file.newYear || file.year) ? 'file-field' : 'file-field empty');
    const genreClass = genreHasChanged ? 'file-field updated' : ((file.newGenre || file.genre) ? 'file-field' : 'file-field empty');

    let filenameDisplay = file.filename;
    if (file.has_number_prefix) {
        filenameDisplay = `<span style="text-decoration: line-through; opacity: 0.5; font-size: 0.75rem;">${file.filename.split('.')[0]}.</span> ${file.cleaned_filename}`;
    }

    // Build metadata tooltip
    let metadataDetails = [];
    if (file.comment) metadataDetails.push(`Comment: ${file.comment}`);
    if (file.publisher) metadataDetails.push(`Publisher: ${file.publisher}`);
    if (file.composer) metadataDetails.push(`Composer: ${file.composer}`);
    if (file.album_artist) metadataDetails.push(`Album Artist: ${file.album_artist}`);
    if (file.copyright) metadataDetails.push(`Copyright: ${file.copyright}`);
    if (file.url) metadataDetails.push(`URL: ${file.url}`);
    if (file.encoder) metadataDetails.push(`Encoder: ${file.encoder}`);
    
    const metadataTooltip = metadataDetails.length > 0 ? metadataDetails.join('\n') : 'No additional metadata';
    
    // Confidence badge (if lookup was done)
    let confidenceBadge = '';
    if (file.rank_score !== undefined || file.confidence !== undefined) {
        const score = file.rank_score !== undefined ? file.rank_score : file.confidence;
        const percentage = Math.round(Math.min(Math.max(score * 100, 0), 100));
        let badgeClass = 'confidence-low';
        if (percentage >= 80) badgeClass = 'confidence-high';
        else if (percentage >= 60) badgeClass = 'confidence-medium';
        confidenceBadge = `<span class="confidence-badge ${badgeClass}" title="Match: ${percentage}%">${percentage}%</span>`;
    } else if (status === 'processing') {
        confidenceBadge = '<span class="spinner-small" title="Looking up…">⋯</span>';
    } else if (status === 'lookup_error') {
        confidenceBadge = '<span class="confidence-low" style="font-size:0.75rem;" title="Not found">—</span>';
    } else if (status === 'success') {
        confidenceBadge = '<span class="confidence-high" style="font-size:0.75rem;" title="Saved">✓</span>';
    } else if (file.has_spam) {
        confidenceBadge = '<span class="confidence-medium" style="font-size:0.75rem;" title="Has spam">!</span>';
    }

    // Album cover thumbnail (36px compact)
    const cover = file.newCover || file.cover;
    let coverHtml = '';
    if (cover) {
        coverHtml = `<img src="data:image/jpeg;base64,${cover}" class="album-cover-thumb" title="Click to view full size" data-cover-index="${index}">`;
    } else {
        coverHtml = '<div class="no-cover" title="No cover – run Lookup">—</div>';
    }

    const revertDisplay = (file.newTitle || file.newArtist) ? 'inline-flex' : 'none';
    div.innerHTML = `
        <div class="cover-cell">${coverHtml}</div>
        <div style="display:flex;align-items:center;justify-content:center;">
            <button type="button" onclick="togglePlay(${index})" id="play-btn-${index}" class="play-btn" title="Play / Pause">${PLAY_ICON_ROW}</button>
            <audio id="audio-${index}" src="/file/${encodeURIComponent(file.filename)}" preload="metadata"></audio>
        </div>
        <div style="display:flex;flex-direction:column;gap:2px;justify-content:center;min-width:0;">
            <div class="file-name" title="${(file.filename || '').replace(/"/g, '&quot;')}">${filenameDisplay}</div>
            <div style="display:flex;align-items:center;gap:6px;">
                <div class="progress-container" onclick="scrubAudio(event, ${index})" style="flex:1;height:6px;background:var(--border);border-radius:3px;cursor:pointer;min-width:60px;">
                    <div id="progress-${index}" class="progress-bar" style="width:0%;height:100%;background:var(--accent);border-radius:3px;transition:width 0.1s;"></div>
                </div>
                <span id="time-${index}" style="font-size:0.7rem;color:var(--ink-subtle);min-width:36px;font-family:var(--font-mono);text-align:right;">0:00</span>
            </div>
        </div>
        <div class="${titleClass}">${titleDisplay}</div>
        <div class="${artistClass}">${artistDisplay}</div>
        <div class="${albumClass}">${albumDisplay}</div>
        <div class="${yearClass}">${yearDisplay}</div>
        <div class="${genreClass}">${genreDisplay}</div>
        <div id="confidence-${index}" style="text-align:center;display:flex;justify-content:center;align-items:center;">${confidenceBadge}</div>
        <div class="file-actions">
            <button type="button" onclick="lookupMetadata(${index}, true)" class="btn btn-primary btn-small" title="Auto lookup"><svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-search"/></svg></button>
            <button type="button" onclick="lookupMetadata(${index}, false)" class="btn btn-secondary btn-small" title="Choose result"><svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-edit"/></svg></button>
            <button type="button" onclick="revertLookup(${index})" class="btn btn-warning btn-small" title="Revert" style="display:${revertDisplay}"><svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-refresh"/></svg></button>
            <button type="button" onclick="viewMetadata(${index})" class="btn btn-secondary btn-small" title="Metadata"><svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-info"/></svg></button>
            <button type="button" onclick="saveFile(${index})" class="btn btn-success btn-small" title="Save"><svg class="icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><use href="#icon-save"/></svg></button>
        </div>
    `;
    
    // Add click event listener for cover image
    if (cover) {
        const coverImg = div.querySelector('.album-cover-thumb');
        if (coverImg) {
            coverImg.addEventListener('click', () => {
                showLargeCover(index);
            });
        }
    }

    return div;
}

// Lookup metadata for a single file
async function lookupMetadata(index, isBatch = false) {
    const file = currentFiles[index];
    
    // Update status to processing
    currentFiles[index].status = 'processing';
    updateFileItemStatus(index);

    try {
        const response = await fetch('/api/lookup-metadata', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                filepath: file.filepath,
                title: file.title,
                artist: file.artist
            })
        });

        let data;
        try {
            data = await response.json();
        } catch (jsonError) {
            throw new Error('Invalid response from server');
        }

        if (!response.ok) {
            const errorMsg = data.error || 'Metadata not found';
            throw new Error(errorMsg);
        }

        // Check if we have valid results
        if (!data.results || data.results.length === 0) {
            throw new Error('No metadata found in any database');
        }

        // If auto mode (isBatch = true), use best match automatically
        if (isBatch) {
            const bestMatch = data.best_match || data.results[0];
            applyMetadata(index, bestMatch, true);
        } else {
            // Manual mode: show all results and let user choose
            if (data.results.length === 1) {
                // Only one result, apply it automatically
                applyMetadata(index, data.results[0], true);
            } else if (data.results.length > 1) {
                // Multiple results: show modal for user to choose
                showResultsModal(index, data.results);
                return; // Don't update stats yet, wait for user choice
            } else {
                throw new Error('No valid metadata returned');
            }
        }

        processedCount++;
        updateStats();

    } catch (error) {
        // Mark as error but don't show alert during batch
        currentFiles[index].status = 'lookup_error';
        currentFiles[index].errorMessage = error.message;
        updateFileItemStatus(index);
        
        console.error(`Lookup error for ${file.filename}:`, error);
        
        if (!isBatch) {
            let errorDetails = error.message;
            if (error.message.includes('fetch')) {
                errorDetails = 'Network error - check if server is running';
            }
            alert(`Error looking up metadata:\n\nFile: ${file.filename}\nError: ${errorDetails}`);
        }
    }
}

// Show picker to choose from multiple results (rarely used now)
function showResultPicker(index, results) {
    const file = currentFiles[index];
    
    // Update modal title
    document.getElementById('modalTitle').textContent = `Choose metadata for: ${file.filename}`;
    
    // Build result options
    const modalBody = document.getElementById('modalBody');
    modalBody.innerHTML = '';
    
    // Add info message
    const infoDiv = document.createElement('div');
    infoDiv.style.padding = '10px';
    infoDiv.style.background = 'rgba(99, 102, 241, 0.1)';
    infoDiv.style.borderRadius = '6px';
    infoDiv.style.marginBottom = '15px';
    infoDiv.style.fontSize = '0.875rem';
    infoDiv.innerHTML = `<strong>💡 Tip:</strong> The best match is automatically selected (ranked by album type, confidence, and completeness).`;
    modalBody.appendChild(infoDiv);
    
    results.forEach((result, i) => {
        const option = document.createElement('div');
        option.className = 'result-option';
        option.onclick = () => selectResult(index, result);
        
        const compilation = result.is_compilation ? '<span class="result-compilation">COMPILATION</span>' : '';
        const single = result.album && result.album.toLowerCase().includes('single') ? '<span class="result-compilation">SINGLE</span>' : '';
        const source = result.source ? `<span class="result-source">${result.source}</span>` : '';
        const bestMatch = i === 0 ? '<span class="result-source" style="background: var(--success);">BEST MATCH</span>' : '';
        
        option.innerHTML = `
            <div class="result-title">
                ${i + 1}. ${result.title || '—'}
                ${bestMatch}
                ${source}
                ${compilation}
                ${single}
            </div>
            <div class="result-details">
                <strong>Artist:</strong> ${result.artist || '—'}<br>
                <strong>Album:</strong> ${result.album || '—'}<br>
                <strong>Year:</strong> ${result.year || '—'} | 
                <strong>Genre:</strong> ${result.genre || '—'}
            </div>
        `;
        
        modalBody.appendChild(option);
    });
    
    // Show modal
    document.getElementById('resultPickerModal').classList.add('active');
}

// Select a result from the picker
function selectResult(index, metadata) {
    applyMetadata(index, metadata, true);
    closeResultPicker();
}

// Close result picker modal
function closeResultPicker() {
    document.getElementById('resultPickerModal').classList.remove('active');
}

// Show modal with multiple results for user to choose
function showResultsModal(fileIndex, results) {
    const modal = document.getElementById('resultPickerModal');
    const modalBody = document.getElementById('modalBody');
    const file = currentFiles[fileIndex];
    
    // Update modal title
    document.getElementById('modalTitle').textContent = `Choose Metadata for: ${file.filename}`;
    
    // Build results HTML
    let html = '<div class="result-options">';
    
    results.forEach((result, resultIndex) => {
        const score = result.rank_score !== undefined ? result.rank_score : result.confidence || 0;
        const percentage = Math.round(Math.min(Math.max(score * 100, 0), 100));
        
        let badgeClass = 'confidence-low';
        let badgeColor = '#ef4444';
        if (percentage >= 80) {
            badgeClass = 'confidence-high';
            badgeColor = '#10b981';
        } else if (percentage >= 60) {
            badgeClass = 'confidence-medium';
            badgeColor = '#f59e0b';
        }
        
        const isCompilation = result.is_compilation ? '⚠️ Compilation' : '';
        const coverPreview = result.cover_url ? `<img src="${result.cover_url}" style="width: 60px; height: 60px; object-fit: cover; border-radius: 4px;">` : '<div style="width: 60px; height: 60px; background: #ccc; border-radius: 4px; display: flex; align-items: center; justify-content: center;">📀</div>';
        
        html += `
            <div class="result-option" onclick="selectResult(${fileIndex}, ${resultIndex})" style="cursor: pointer; padding: 15px; border: 2px solid #e5e7eb; border-radius: 8px; margin-bottom: 10px; display: grid; grid-template-columns: 60px 1fr auto; gap: 15px; align-items: center; transition: all 0.2s; hover: background: #f9fafb;">
                <div>${coverPreview}</div>
                <div style="min-width: 0;">
                    <div style="margin-bottom: 8px;">
                        <span style="font-size: 0.7rem; color: #9ca3af; text-transform: uppercase; font-weight: 600;">Title:</span>
                        <div style="font-weight: 600; font-size: 1rem; margin-top: 2px;">${result.title || '—'}</div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <span style="font-size: 0.7rem; color: #9ca3af; text-transform: uppercase; font-weight: 600;">Artist:</span>
                        <div style="color: #374151; font-size: 0.875rem; margin-top: 2px;">${result.artist || '—'}</div>
                    </div>
                    <div style="margin-bottom: 8px;">
                        <span style="font-size: 0.7rem; color: #9ca3af; text-transform: uppercase; font-weight: 600;">Album:</span>
                        <div style="color: #374151; font-size: 0.875rem; margin-top: 2px;">${result.album || '—'} ${result.year ? `(${result.year})` : ''}</div>
                    </div>
                    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #e5e7eb;">
                        <span style="color: #9ca3af; font-size: 0.75rem;">
                            <strong>Source:</strong> ${result.source || '—'} ${isCompilation}
                        </span>
                        ${result.genre ? `<span style="color: #9ca3af; font-size: 0.75rem; margin-left: 12px;"><strong>Genre:</strong> ${result.genre}</span>` : ''}
                    </div>
                </div>
                <div class="confidence-badge-large ${badgeClass}" 
                     style="background: ${badgeColor}15; color: ${badgeColor}; border: 2px solid ${badgeColor}; padding: 8px 12px; border-radius: 6px; font-weight: 600;">
                    ${percentage}%
                </div>
            </div>
        `;
    });
    
    html += '</div>';
    
    modalBody.innerHTML = html;
    modal.classList.add('active');
    
    // Store results for later use
    currentFiles[fileIndex]._resultsForSelection = results;
}

// User selected a result from the modal
async function selectResult(fileIndex, resultIndex) {
    const results = currentFiles[fileIndex]._resultsForSelection;
    if (!results || !results[resultIndex]) return;
    
    const selectedResult = results[resultIndex];
    
    // Download cover art if cover_url exists but cover data doesn't
    if (selectedResult.cover_url && !selectedResult.cover) {
        console.log('📥 Downloading cover art from:', selectedResult.cover_url);
        try {
            const response = await fetch(selectedResult.cover_url);
            const blob = await response.blob();
            
            // Convert blob to base64
            const reader = new FileReader();
            reader.onloadend = function() {
                const base64data = reader.result.split(',')[1]; // Remove data:image/jpeg;base64, prefix
                selectedResult.cover = base64data;
                console.log('✅ Cover art downloaded and converted to base64');
                
                // Apply the selected metadata with cover art
                applyMetadata(fileIndex, selectedResult, false);
                
                // Update status
                currentFiles[fileIndex].status = '';
                updateFileItemStatus(fileIndex);
                
                // Update stats
                processedCount++;
                updateStats();
                
                // Close modal
                closeResultPicker();
                
                // Clean up
                delete currentFiles[fileIndex]._resultsForSelection;
            };
            reader.readAsDataURL(blob);
        } catch (error) {
            console.error('❌ Error downloading cover art:', error);
            // Continue without cover art
            applyMetadataAndFinish();
        }
    } else {
        applyMetadataAndFinish();
    }
    
    function applyMetadataAndFinish() {
        // Apply the selected metadata
        applyMetadata(fileIndex, selectedResult, false);
        
        // Update status
        currentFiles[fileIndex].status = '';
        updateFileItemStatus(fileIndex);
        
        // Update stats
        processedCount++;
        updateStats();
        
        // Close modal
        closeResultPicker();
        
        // Clean up
        delete currentFiles[fileIndex]._resultsForSelection;
    }
}

// Apply metadata to a file
function applyMetadata(index, metadata, markSuccess = false) {
    currentFiles[index].newTitle = metadata.title || currentFiles[index].title;
    currentFiles[index].newArtist = metadata.artist || currentFiles[index].artist;
    currentFiles[index].newAlbum = metadata.album || currentFiles[index].album;
    currentFiles[index].newYear = metadata.year || currentFiles[index].year;
    currentFiles[index].newGenre = metadata.genre || currentFiles[index].genre;
    currentFiles[index].confidence = metadata.confidence || 0;
    currentFiles[index].rank_score = metadata.rank_score || metadata.confidence || 0;
    
    // Update cover if provided
    if (metadata.cover) {
        currentFiles[index].newCover = metadata.cover;
    }
    
    if (markSuccess) {
        currentFiles[index].status = 'lookup_success';
    }
    
    // Re-render this file item
    updateFileItemStatus(index);
}

// Show large cover in modal
function showLargeCover(index) {
    const file = currentFiles[index];
    const coverData = file.newCover || file.cover;
    
    if (!coverData) {
        return;
    }
    
    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0,0,0,0.9);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 10000;
        cursor: pointer;
    `;
    
    const img = document.createElement('img');
    img.src = `data:image/jpeg;base64,${coverData}`;
    img.style.cssText = 'max-width: 90%; max-height: 90%; border-radius: 8px; box-shadow: 0 20px 60px rgba(0,0,0,0.5);';
    
    modal.appendChild(img);
    modal.onclick = () => modal.remove();
    document.body.appendChild(modal);
}

// Update just the status and content of a file item (without full re-render)
function updateFileItemStatus(index) {
    const fileItem = document.getElementById(`file-${index}`);
    if (!fileItem) return;
    
    const newFileItem = createFileItem(currentFiles[index], index);
    fileItem.replaceWith(newFileItem);
}

// Lookup all files
async function lookupAll() {
    if (!confirm(`Lookup metadata for ${currentFiles.length} files?\n\nThis will search iTunes, Last.fm, and MusicBrainz for each track.\n\nThis may take a few minutes for large batches.`)) {
        return;
    }

    // Reset counters
    let successCount = 0;
    let errorCount = 0;
    const startTime = Date.now();

    // Process all files
    for (let i = 0; i < currentFiles.length; i++) {
        try {
            await lookupMetadata(i, true);  // true = batch mode
            
            // Count results
            if (currentFiles[i].status === 'lookup_success') {
                successCount++;
            } else if (currentFiles[i].status === 'lookup_error') {
                errorCount++;
            }
            
            // Update stats in real-time
            updateStats();
            
            // Small delay to show progress and respect rate limits
            await new Promise(resolve => setTimeout(resolve, 200));
        } catch (error) {
            console.error(`Fatal error processing file ${i}:`, error);
            errorCount++;
        }
    }

    const duration = Math.round((Date.now() - startTime) / 1000);

    // Show summary
    let summary = `✅ Lookup Complete!\n\n`;
    summary += `✓ Success: ${successCount} files\n`;
    summary += `✗ Not found: ${errorCount} files\n`;
    summary += `━━━━━━━━━━━━━━━\n`;
    summary += `Total: ${currentFiles.length} files\n`;
    summary += `Time: ${duration} seconds\n\n`;
    
    if (errorCount > 0) {
        summary += `Files with errors are marked with ❌\n`;
        summary += `You can manually edit or retry those tracks.`;
    } else {
        summary += `All tracks found! You can now save changes.`;
    }
    
    alert(summary);
}

// Save a single file
async function saveFile(index) {
    const file = currentFiles[index];
    
    // Set status to processing
    currentFiles[index].status = 'processing';
    updateFileItemStatus(index);

    const tags = {
        title: file.newTitle || file.title || '',
        artist: file.newArtist || file.artist || '',
        album: file.newAlbum || file.album || '',
        year: file.newYear || file.year || '',
        genre: file.newGenre || file.genre || '',
        cover: file.newCover || null  // Include cover if updated
    };
    
    // DEBUG: Check cover data
    console.log(`DEBUG saveFile: file.newCover exists? ${!!file.newCover}, length: ${file.newCover ? file.newCover.length : 0}`);
    console.log(`DEBUG saveFile: file.cover exists? ${!!file.cover}, length: ${file.cover ? file.cover.length : 0}`);
    console.log(`DEBUG saveFile: tags.cover exists? ${!!tags.cover}, length: ${tags.cover ? tags.cover.length : 0}`);

    try {
        const response = await fetch('/api/update-tags', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                filepath: file.filepath,
                tags: tags
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to update tags');
        }

        // Update file with saved data
        currentFiles[index].title = tags.title;
        currentFiles[index].artist = tags.artist;
        currentFiles[index].album = tags.album;
        currentFiles[index].year = tags.year;
        currentFiles[index].genre = tags.genre;
        if (tags.cover) {
            currentFiles[index].cover = tags.cover;
        }
        
        // Clear "new" fields after successful save
        currentFiles[index].newTitle = null;
        currentFiles[index].newArtist = null;
        currentFiles[index].newAlbum = null;
        currentFiles[index].newYear = null;
        currentFiles[index].newGenre = null;
        currentFiles[index].newCover = null;
        
        currentFiles[index].status = 'success';
        updateFileItemStatus(index);
        
        successCount++;
        updateStats();

    } catch (error) {
        currentFiles[index].status = 'error';
        updateFileItemStatus(index);
        console.error(`Error saving ${file.filename}:`, error.message);
        // Don't show individual alerts during batch save - let saveAll() show summary
        throw error;  // Re-throw so saveAll can count errors
    }
}

// Save all files
async function saveAll() {
    // Filter only files with changes (where lookup was done)
    const changedFiles = currentFiles
        .map((file, index) => ({ file, index }))
        .filter(({ file }) => 
            file.newTitle || file.newArtist || file.newAlbum || 
            file.newYear || file.newGenre || file.newCover
        );
    
    if (changedFiles.length === 0) {
        alert('No changes to save. Please lookup metadata first.');
        return;
    }
    
    if (!confirm(`Save changes to ${changedFiles.length} modified file(s)?`)) {
        return;
    }

    showLoading(`Saving ${changedFiles.length} file(s)...`);
    
    let savedCount = 0;
    let errorCount = 0;

    try {
        for (const { index } of changedFiles) {
            try {
                await saveFile(index);
                savedCount++;
            } catch (error) {
                errorCount++;
                console.error(`Error saving file ${index}:`, error);
            }
        }
    } finally {
        // Always hide loading, even if errors occurred
        hideLoading();
        
        if (errorCount > 0) {
            alert(`Save complete!\nSuccessfully saved: ${savedCount}\nFailed: ${errorCount}`);
        } else {
            alert(`✅ Save complete!\nSuccessfully saved ${savedCount} file(s)!`);
        }
    }
}

// Revert lookup to original tags
function revertLookup(index) {
    const file = currentFiles[index];
    
    if (!file.original) {
        alert('No original data to revert to');
        return;
    }
    
    if (!confirm(`Revert "${file.filename}" to original tags?`)) {
        return;
    }
    
    // Restore original data
    currentFiles[index].newTitle = null;
    currentFiles[index].newArtist = null;
    currentFiles[index].newAlbum = null;
    currentFiles[index].newYear = null;
    currentFiles[index].newGenre = null;
    currentFiles[index].newCover = null;
    currentFiles[index].status = 'pending';
    currentFiles[index].confidence = null;
    currentFiles[index].rank_score = null;
    
    // Re-render
    updateFileItemStatus(index);
}

// View all metadata for a file
function viewMetadata(index) {
    const file = currentFiles[index];
    
    let details = `📄 ${file.filename}\n\n`;
    details += `=== BASIC METADATA ===\n`;
    details += `Title: ${file.title || '—'}\n`;
    details += `Artist: ${file.artist || '—'}\n`;
    details += `Album: ${file.album || '—'}\n`;
    details += `Year: ${file.year || '—'}\n`;
    details += `Genre: ${file.genre || '—'}\n\n`;
    
    details += `=== ADDITIONAL METADATA ===\n`;
    details += `Album Artist: ${file.album_artist || '—'}\n`;
    details += `Composer: ${file.composer || '—'}\n`;
    details += `Publisher: ${file.publisher || '—'}\n`;
    details += `Comment: ${file.comment || '—'}\n`;
    details += `Copyright: ${file.copyright || '—'}\n`;
    details += `Encoder: ${file.encoder || '—'}\n`;
    details += `URL: ${file.url || '—'}\n`;
    details += `Track#: ${file.track_number || '—'}\n`;
    details += `Disc#: ${file.disc_number || '—'}\n\n`;
    
    details += `=== FILE INFO ===\n`;
    details += `Size: ${(file.size / 1024 / 1024).toFixed(2)} MB\n`;
    details += `Bitrate: ${file.bitrate} kbps\n`;
    details += `Duration: ${Math.floor(file.duration / 60)}:${(file.duration % 60).toString().padStart(2, '0')}\n\n`;
    
    if (file.has_spam) {
        details += `⚠️ WARNING: This file contains spam metadata!\n`;
    }
    
    alert(details);
}

// Clean spam metadata from files
async function cleanSpamMetadata() {
    const filesWithSpam = currentFiles.filter(f => f.has_spam);
    
    if (filesWithSpam.length === 0) {
        alert('No spam metadata detected!');
        return;
    }

    if (!confirm(`Remove spam metadata from ${filesWithSpam.length} file(s)?\n\nThis will remove:\n- Commercial comments\n- Spam URLs\n- Unwanted publisher/copyright info`)) {
        return;
    }

    showLoading(`Cleaning spam from ${filesWithSpam.length} file(s)...`);

    try {
        const response = await fetch('/api/clean-metadata', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                filepaths: filesWithSpam.map(f => f.filepath)
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || 'Failed to clean metadata');
        }

        // Update files
        data.results.forEach(result => {
            if (result.status === 'cleaned') {
                const fileIndex = currentFiles.findIndex(f => f.filepath === result.filepath);
                if (fileIndex !== -1) {
                    currentFiles[fileIndex].has_spam = false;
                    // Clear spam fields
                    if (result.cleaned_fields.includes('comment')) currentFiles[fileIndex].comment = '';
                    if (result.cleaned_fields.includes('publisher')) currentFiles[fileIndex].publisher = '';
                    if (result.cleaned_fields.includes('copyright')) currentFiles[fileIndex].copyright = '';
                    if (result.cleaned_fields.includes('url')) currentFiles[fileIndex].url = '';
                }
            }
        });

        // Hide alert if no more spam
        const hasSpam = currentFiles.some(f => f.has_spam);
        if (!hasSpam) {
            document.getElementById('spamAlert').style.display = 'none';
        }

        // Re-render
        renderFileList();
        
        hideLoading();
        alert(`✅ Success!\n\nCleaned: ${data.success} file(s)\nAlready clean: ${data.already_clean} file(s)\nFailed: ${data.failed} file(s)`);

    } catch (error) {
        hideLoading();
        alert(`Error cleaning metadata: ${error.message}`);
    }
}


// Reset and start over
function reset() {
    if (confirm('Start over? All unsaved changes will be lost.')) {
        currentFiles = [];
        processedCount = 0;
        successCount = 0;
        document.getElementById('folderPath').value = '';
        saveAppStateToStorage({ last_folder_path: '' });
        document.getElementById('step2').classList.remove('active');
        document.getElementById('step1').classList.add('active');
    }
}

// Loading overlay - safety timeout so it never blocks forever
let _loadingTimeoutId = null;
function showLoading(text) {
    var overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    if (_loadingTimeoutId) clearTimeout(_loadingTimeoutId);
    var loadingText = document.getElementById('loadingText');
    if (loadingText) loadingText.textContent = text || 'Processing...';
    overlay.classList.add('active');
    _loadingTimeoutId = setTimeout(() => { hideLoading(); _loadingTimeoutId = null; }, 60000);
}
function hideLoading() {
    if (_loadingTimeoutId) { clearTimeout(_loadingTimeoutId); _loadingTimeoutId = null; }
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('active');
}

// Audio Player Functions
let currentlyPlaying = null;
let timeUpdateListeners = {};
let endedListeners = {};

function togglePlay(index) {
    const audio = document.getElementById(`audio-${index}`);
    const playBtn = document.getElementById(`play-btn-${index}`);

    if (!audio) return;

    if (audio.error) {
        const errorMessages = {
            1: 'Loading was aborted',
            2: 'Network error',
            3: 'Decode error',
            4: 'Source not supported'
        };
        alert('Audio Error: ' + (errorMessages[audio.error.code] || 'Unknown'));
        return;
    }

    if (currentlyPlaying !== null && currentlyPlaying !== index) {
        const otherAudio = document.getElementById(`audio-${currentlyPlaying}`);
        const otherBtn = document.getElementById(`play-btn-${currentlyPlaying}`);
        if (otherAudio) { otherAudio.pause(); otherAudio.currentTime = 0; }
        if (otherBtn) { otherBtn.innerHTML = PLAY_ICON_ROW; otherBtn.classList.remove('playing'); }
        const otherProgress = document.getElementById(`progress-${currentlyPlaying}`);
        const otherTime = document.getElementById(`time-${currentlyPlaying}`);
        if (otherProgress) otherProgress.style.width = '0%';
        if (otherTime) otherTime.textContent = '0:00';
    }

    if (audio.paused) {
        audio.play().then(() => {
            playBtn.innerHTML = PAUSE_ICON_ROW;
            playBtn.classList.add('playing');
            currentlyPlaying = index;
        }).catch(() => {});

        if (timeUpdateListeners[index]) audio.removeEventListener('timeupdate', timeUpdateListeners[index]);
        if (endedListeners[index]) audio.removeEventListener('ended', endedListeners[index]);

        timeUpdateListeners[index] = function() { updateProgress(index); };
        endedListeners[index] = function() {
            playBtn.innerHTML = PLAY_ICON_ROW;
            playBtn.classList.remove('playing');
            currentlyPlaying = null;
            document.getElementById(`progress-${index}`).style.width = '0%';
            document.getElementById(`time-${index}`).textContent = '0:00';
        };

        audio.addEventListener('timeupdate', timeUpdateListeners[index]);
        audio.addEventListener('ended', endedListeners[index]);
    } else {
        audio.pause();
        playBtn.innerHTML = PLAY_ICON_ROW;
        playBtn.classList.remove('playing');
        currentlyPlaying = null;
    }
}

function updateProgress(index) {
    const audio = document.getElementById(`audio-${index}`);
    const progressBar = document.getElementById(`progress-${index}`);
    const timeDisplay = document.getElementById(`time-${index}`);
    
    if (audio.duration) {
        const percentage = (audio.currentTime / audio.duration) * 100;
        progressBar.style.width = percentage + '%';
        
        // Format time
        const currentMinutes = Math.floor(audio.currentTime / 60);
        const currentSeconds = Math.floor(audio.currentTime % 60);
        timeDisplay.textContent = `${currentMinutes}:${currentSeconds.toString().padStart(2, '0')}`;
    }
}

function scrubAudio(event, index) {
    const audio = document.getElementById(`audio-${index}`);
    const progressContainer = event.currentTarget;
    const clickX = event.offsetX;
    const width = progressContainer.offsetWidth;
    const percentage = clickX / width;
    
    if (audio.duration) {
        audio.currentTime = audio.duration * percentage;
        updateProgress(index);
    }
}

// --- Shazam to Soundeo Sync ---

const SHAZAM_COMPARE_POLL_TIMEOUT_MS = 30 * 60 * 1000;
/** Max duration for inline progress polls (sync single, search single/global); prevents leak if server hangs. */
const SHAZAM_INLINE_POLL_MAX_MS = 30 * 60 * 1000;
/** Shown when an action is rejected (e.g. another operation running) so the user gets context. */
const SHAZAM_ACTION_REJECTED_MSG = 'Another operation is already running. Wait for it to finish or click Stop.';
let shazamComparePollInterval = null;
let shazamFolderInputs = [];
let shazamProgressInterval = null;
let shazamDownloadPollInterval = null;
let shazamProgressRestoreInterval = null;

/** Start progress polling; always clears any existing interval first to avoid stacking (crash/loop). */
function shazamStartProgressPoll() {
    if (shazamProgressInterval) {
        clearInterval(shazamProgressInterval);
        shazamProgressInterval = null;
    }
    if (shazamProgressRestoreInterval) {
        clearInterval(shazamProgressRestoreInterval);
        shazamProgressRestoreInterval = null;
    }
    shazamProgressInterval = setInterval(shazamPollProgress, 500);
}

/** Start compare polling; always clears any existing interval first to avoid stacking. */
function shazamStartComparePoll(start) {
    if (shazamComparePollInterval) {
        clearInterval(shazamComparePollInterval);
        shazamComparePollInterval = null;
    }
    const t = start != null ? start : Date.now();
    setTimeout(function () { shazamComparePoll(t); }, 120);
    shazamComparePollInterval = setInterval(function () { shazamComparePoll(t); }, 750);
}

/** Start download progress polling; always clears any existing interval first to avoid stacking. */
function shazamStartDownloadPoll() {
    if (shazamDownloadPollInterval) {
        clearInterval(shazamDownloadPollInterval);
        shazamDownloadPollInterval = null;
    }
    shazamDownloadPollInterval = setInterval(shazamPollDownloadProgress, 500);
}

/** Shared poll for per-row Soundeo search queue (multiple tracks). One interval for the whole batch so each completion clears the correct row’s pending state. */
let shazamSingleSearchPollInterval = null;
let shazamSingleSearchPollStartedAt = 0;
let shazamSingleSearchPollInFlight = false;

function shazamStopSingleSearchProgressPoll() {
    if (shazamSingleSearchPollInterval) {
        clearInterval(shazamSingleSearchPollInterval);
        shazamSingleSearchPollInterval = null;
    }
    shazamSingleSearchPollStartedAt = 0;
    shazamSingleSearchPollInFlight = false;
}

function shazamEnsureSingleSearchProgressPoll() {
    if (shazamSingleSearchPollInterval) return;
    shazamSingleSearchPollStartedAt = Date.now();
    shazamSingleSearchPollInterval = setInterval(shazamSingleSearchProgressPollTick, 500);
    shazamSingleSearchProgressPollTick();
}

/** After hiding the main sync progress bar, re-sync queue banners from the server so star/unstar/search queues are not wiped. */
function shazamRefreshQueueBarsFromProgress(done) {
    fetch('/api/shazam-sync/progress')
        .then(r => r.json())
        .then(p => {
            shazamApplyQueueState(p.star_queue || [], p.single_search_queue || [], p.unstar_queue || []);
            if (p.download_queue && Array.isArray(p.download_queue)) {
                shazamCurrentDownloadQueue = p.download_queue;
                if (!shazamSingleBarActive) shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
            }
            shazamUpdateBatchJobsSectionVisibility();
            if (done) done();
        })
        .catch(function () {
            shazamUpdateBatchJobsSectionVisibility();
            if (done) done();
        });
}

function shazamSingleSearchProgressPollTick() {
    if (shazamSingleSearchPollInFlight) return;
    if (Date.now() - shazamSingleSearchPollStartedAt > SHAZAM_INLINE_POLL_MAX_MS) {
        shazamStopSingleSearchProgressPoll();
        shazamHideSyncProgress();
        shazamCurrentProgress = {};
        return;
    }
    shazamSingleSearchPollInFlight = true;
    fetch('/api/shazam-sync/progress')
        .then(r => r.json())
        .then(p => {
            shazamSingleSearchPollInFlight = false;
            shazamCurrentProgress = p;
            shazamApplyQueueState(shazamCurrentStarQueue, p.single_search_queue || [], p.unstar_queue !== undefined ? p.unstar_queue : shazamCurrentUnstarQueue);

            const el = document.getElementById('shazamProgress');
            const sq = p.single_search_queue || [];
            const nQueued = sq.length;
            const batchN = nQueued + (p.running && p.mode === 'search_single' ? 1 : 0);
            const baseMsg = p.running ? (p.message || 'Searching…') : (p.error || p.message || '');
            var line = baseMsg;
            if (batchN > 1 || nQueued > 0) {
                line = 'Manual search (' + batchN + ' track' + (batchN === 1 ? '' : 's') + ') — ' + (p.running ? (p.message || 'Searching…') : baseMsg);
            }
            if (el) el.textContent = line;
            var manualBatchActive = nQueued > 0 || (p.mode === 'search_single' && p.running);
            if (manualBatchActive) {
                var be = document.getElementById('shazamSyncProgress');
                if (be && be.style.display !== 'flex') shazamShowSyncProgress(line);
            }

            shazamSetProgressClickable(!!(p.running && p.current_key));

            if (p.running && p.mode === 'search_single' && shazamLastData) {
                shazamScheduleRenderTrackList(shazamLastData, true);
            }

            if (p.mode === 'search_single' && !p.running) {
                var trackKey = (p.key != null && String(p.key).trim() !== '') ? String(p.key).trim() : '';
                if (!trackKey && p.current_key) trackKey = String(p.current_key).trim();
                if (trackKey && p.done === 1 && p.url) {
                    shazamSetUrlLive(trackKey, p.url);
                    if (p.soundeo_title) {
                        shazamKeyVariants(trackKey).forEach(function (k) {
                            shazamSoundeoTitles[k] = p.soundeo_title;
                        });
                    }
                    if (p.soundeo_match_score != null && p.soundeo_match_score !== '') {
                        var scn = Number(p.soundeo_match_score);
                        if (!Number.isNaN(scn)) shazamSetSoundeoMatchScoreLive(trackKey, scn);
                    }
                    if (p.cover_hashes && typeof p.cover_hashes === 'object') {
                        shazamMergeCoverHashes(p.cover_hashes);
                    }
                    shazamSetStarredLive(trackKey, !!p.starred);
                    shazamSetNotFoundLive(trackKey, false);
                    shazamLoadStatus();
                } else if (trackKey && p.failed === 1) {
                    shazamSetNotFoundLive(trackKey, true);
                    shazamLoadStatus();
                } else if (trackKey && p.error) {
                    shazamLoadStatus();
                }
                if (trackKey) shazamClearActionPendingForKey(trackKey);
                if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);

                if (nQueued === 0) {
                    shazamStopSingleSearchProgressPoll();
                    setTimeout(function () {
                        fetch('/api/shazam-sync/progress')
                            .then(r => r.json())
                            .then(p2 => {
                                shazamCurrentProgress = p2;
                                var stillBusy = p2.running && p2.mode === 'search_single';
                                var q2 = (p2.single_search_queue || []).length;
                                if (stillBusy || q2 > 0) {
                                    shazamEnsureSingleSearchProgressPoll();
                                    return;
                                }
                                shazamCurrentProgress = {};
                                shazamHideSyncProgress();
                                shazamLoadStatus();
                            })
                            .catch(function () {
                                shazamCurrentProgress = {};
                                shazamHideSyncProgress();
                            });
                    }, 350);
                }
            }
        })
        .catch(function () {
            shazamSingleSearchPollInFlight = false;
        });
}

/** Latest sync/search progress from server (running, current, total, message, current_key). Used to show spinner in the row being processed. */
let shazamCurrentProgress = {};
/** Current star queue from progress API (list of { artist, title, key }). Used to show "Queued 2/5" in track rows. */
let shazamCurrentStarQueue = [];
/** Current single-search queue from progress API (list of { artist, title }). Used to show "Queued 2/5" in track rows. */
let shazamCurrentSearchQueue = [];
/** Current unstar queue from progress API (list of { artist, title, key }). Used to show "Unstar queued 2/5" in track rows. */
let shazamCurrentUnstarQueue = [];
/** Download queue (keys). From progress/status download_queue; shown in Download queue bar. */
let shazamCurrentDownloadQueue = [];
/** When true, keep scrolling the current processing row to center of viewport on each progress update. Toggled by "Follow row" / "Unfollow row". */
/** Counter for throttling status fetch during progress poll (fetch status every 2nd poll when batch running). */
let shazamProgressPollCount = 0;
let shazamFollowCurrentRow = false;
let shazamTrackUrls = {};
/** Per-track "starred in Soundeo" state (key: "Artist - Title"). Restored from status on load. */
let shazamStarred = {};
/** Per-track local-only "Maybe" flag — interest level between skip and star. NEVER syncs to Soundeo. Variant-aware: keys come pre-aliased from server. */
let shazamMaybe = {};
/** Per-track local-only "Listened" flag — auto-set when audio plays past threshold; manual toggle also. NEVER syncs to Soundeo. */
let shazamListened = {};
/** Per-track dismissed state (key: "Artist - Title"). Dismissed = unstarred on Soundeo + strikethrough. */
let shazamDismissed = {};
/** Track keys for which the user dismissed the "Manual check" message. Restored from status on load. */
let shazamDismissedManualCheck = {};
/** Per-track Soundeo display title (exact as listed on Soundeo). Key: "Artist - Title". Restored from status on load. */
let shazamSoundeoTitles = {};
/** Per-track "searched but not found on Soundeo" state. Restored from status on load; updated when Search completes with no result. */
let shazamNotFound = {};
/** Track keys currently being processed by a per-row action (dismiss/sync/skip). */
let shazamActionPending = {};
/** In-flight download request per track key (separate from star/search so download can queue while starring). */
let shazamPendingDownload = {};
/** Timestamp (ms) of the most recent download click. The poll's "server idle -> clear stuck pending"
 *  sweep must not wipe a spinner the user just triggered: there is a ~500ms window between the click
 *  and the worker flipping download_progress.running, during which an idle poll would otherwise clear
 *  the pending flag and make the spinner vanish instantly (looked like "no spinner shows"). */
let shazamLastDownloadClickAt = 0;
const SHAZAM_DOWNLOAD_PENDING_GRACE_MS = 4000;
/** Server download worker: which track key is actively downloading (from status download_progress). */
let shazamDownloadProgressSnapshot = { running: false, current_key: null };

/** Merge server status.cover_hashes into shazamCoverHashes (adds key variants so playbar lookup never misses). */
function shazamMergeCoverHashes(coverHashes) {
    if (!coverHashes || typeof coverHashes !== 'object') return;
    Object.keys(coverHashes).forEach(function (k) {
        var v = coverHashes[k];
        if (!k || !v) return;
        shazamCoverHashes[k] = v;
        try {
            var keys = shazamKeyVariants(k);
            for (var i = 0; i < keys.length; i++) {
                var kk = keys[i];
                if (!kk) continue;
                if (!shazamCoverHashes[kk]) shazamCoverHashes[kk] = v;
            }
        } catch (e) {
            var kl = String(k).toLowerCase();
            if (kl && !shazamCoverHashes[kl]) shazamCoverHashes[kl] = v;
        }
    });
}

/**
 * Build a cover-by-key URL that is safe to embed in CSS url() — quoted OR
 * unquoted — and in HTML attributes.
 *
 * encodeURIComponent leaves !'()* unescaped. Parentheses TERMINATE an unquoted
 * CSS url(...) token and apostrophes terminate a single-quoted url('...'), so a
 * key like "Track (Original Mix)" produced an invalid background-image that the
 * browser silently dropped — cover blank even though the file existed. Since
 * "(Original Mix)" / "(Remix)" / "(feat. …)" appear in most dance-track titles,
 * this broke cover art for roughly a third of the library. Percent-encoding
 * those five characters as well makes the URL inert in every embedding context.
 */
function shazamCoverByKeyUrl(key) {
    var enc = encodeURIComponent(key).replace(/[!'()*]/g, function (c) {
        return '%' + c.charCodeAt(0).toString(16).toUpperCase();
    });
    return '/api/shazam-sync/cover-by-key?key=' + enc;
}

/**
 * Does the cover map (now disk-complete from /status) know a cover for this key?
 * Same variant logic the row render uses, so existence here == a cover renders.
 */
function shazamHasCoverForKey(key) {
    if (!key) return false;
    if (shazamCoverHashes[key] || shazamCoverHashes[key.toLowerCase()]) return true;
    try {
        var vks = shazamKeyVariants(key);
        for (var i = 0; i < vks.length; i++) {
            if (vks[i] && shazamCoverHashes[vks[i]]) return true;
        }
    } catch (e) { /* ignore */ }
    return false;
}

/**
 * Surgically fill in covers that have appeared on disk since the last render —
 * without a full table rebuild (which would lose scroll position and hover).
 * Only placeholder cells whose track now has a cover get swapped, in place.
 * Used by the cover-backfill watcher so newly-cached covers populate live.
 * Returns the number of cells filled.
 */
function shazamRefreshVisibleCovers() {
    var list = document.getElementById('shazamTrackList');
    if (!list) return 0;
    var rows = list.querySelectorAll('tr[data-track-key]');
    var filled = 0;
    for (var i = 0; i < rows.length; i++) {
        var tr = rows[i];
        var cell = tr.querySelector('td.shazam-cover-col');
        if (!cell) continue;
        var span = cell.querySelector('.track-cover');
        // Only touch placeholders — rows that already show a cover are left alone.
        if (!span || !span.classList.contains('track-cover-placeholder')) continue;
        var key = tr.getAttribute('data-track-key');
        if (!key || !shazamHasCoverForKey(key)) continue;
        cell.innerHTML = '<span class="track-cover" style="background-image:url(' +
            shazamCoverByKeyUrl(key) + ');" aria-hidden="true"></span>';
        filled++;
    }
    return filled;
}

/**
 * Watch a running server-side cover backfill and populate covers as they land.
 *
 * There is no steady idle /status poll, so without this the frontend is blind to
 * a backfill: covers cached on the server never appear until a manual reload.
 * This is a lightweight, SELF-TERMINATING poll — it fetches /status only while a
 * backfill is running, merges the (disk-derived) cover map, and does a surgical
 * in-place cover refresh (no full re-render). It stops the moment the backfill
 * reports done, after one final refresh.
 */
let _shazamCoverBackfillInterval = null;
const SHAZAM_COVER_BACKFILL_POLL_MS = 4000;
function shazamStartCoverBackfillWatch() {
    if (_shazamCoverBackfillInterval) return;  // already watching
    var stop = function () {
        if (_shazamCoverBackfillInterval) { clearInterval(_shazamCoverBackfillInterval); _shazamCoverBackfillInterval = null; }
    };
    _shazamCoverBackfillInterval = setInterval(function () {
        fetch('/api/shazam-sync/status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                if (!data) return;
                if (data.cover_hashes) shazamMergeCoverHashes(data.cover_hashes);
                shazamRefreshVisibleCovers();
                var bf = data.cover_backfill;
                if (!bf || !bf.running) { stop(); shazamRefreshVisibleCovers(); }
            })
            .catch(function () { /* transient; keep watching */ });
    }, SHAZAM_COVER_BACKFILL_POLL_MS);
}

function shazamMergeDownloadProgressFromPayload(data) {
    if (!data || data.download_progress === undefined) return;
    var dp = data.download_progress;
    shazamDownloadProgressSnapshot = {
        running: !!(dp && dp.running),
        current_key: (dp && dp.running && dp.current_key) ? String(dp.current_key).trim() : null
    };
}

/**
 * current/total for "Downloading …" sync bar — must match the visible download queue, not dp.done
 * (done is cumulative across session; mixing done+1 with queue length produced e.g. 6/1).
 */
function shazamDownloadProgressFractionFromStatus(downloadQueue, currentKey, dp) {
    var q = (downloadQueue && Array.isArray(downloadQueue)) ? downloadQueue : [];
    var ck = (currentKey && String(currentKey).trim()) || '';
    if (q.length > 0) {
        var idx = 0;
        if (ck) {
            for (var i = 0; i < q.length; i++) {
                if (shazamTrackKeyMatches(ck, q[i]) || String(q[i]).trim() === ck) {
                    idx = i;
                    break;
                }
            }
        }
        return { current: idx + 1, total: q.length };
    }
    if (dp && dp.total > 0) {
        var batchTotal = dp.total;
        var done = dp.done != null ? dp.done : 0;
        return { current: Math.min(done + 1, batchTotal), total: batchTotal };
    }
    return { current: 1, total: 1 };
}

/** True if server download_progress.current_key refers to the same track as row key (variant-aware). */
function shazamTrackKeyMatches(serverKey, rowKey) {
    if (!serverKey || !rowKey) return false;
    var sk = String(serverKey).trim();
    var rk = String(rowKey).trim();
    if (sk === rk) return true;
    if (sk.toLowerCase() === rk.toLowerCase()) return true;
    var variants = shazamKeyVariants(rk);
    for (var i = 0; i < variants.length; i++) {
        var v = variants[i];
        if (v === sk || v.toLowerCase() === sk.toLowerCase()) return true;
    }
    return false;
}

/**
 * True if have_locally has an entry with the same Artist - Title as this key (case-insensitive).
 * Intentionally strict: no fuzzy/cross-variant match, so a To DL row is never shown as “have”
 * just because another local file looks similar (e.g. different remix).
 */
function shazamTrackKeyInHaveLocally(trackKey) {
    if (!trackKey || !shazamLastData || !Array.isArray(shazamLastData.have_locally)) return false;
    var tkl = String(trackKey).trim().toLowerCase();
    if (!tkl) return false;
    var list = shazamLastData.have_locally;
    for (var i = 0; i < list.length; i++) {
        var t = list[i];
        var rowKey = ((t.artist || '') + ' - ' + (t.title || '')).trim();
        if (rowKey.toLowerCase() === tkl) return true;
    }
    return false;
}

/** Base64-encode a filesystem path for open-file-location API (UTF-8). */
function shazamPathB64FromFilepath(filepath) {
    if (!filepath) return '';
    var pathNorm = String(filepath).replace(/\\/g, '/');
    try {
        return pathNorm ? btoa(unescape(encodeURIComponent(pathNorm))) : '';
    } catch (e) {
        return '';
    }
}

/** Find have_locally filepath for exact key match; return path_b64 or ''. */
function shazamPathB64ForHaveKey(trackKey) {
    if (!trackKey || !shazamLastData || !Array.isArray(shazamLastData.have_locally)) return '';
    var tkl = String(trackKey).trim().toLowerCase();
    var list = shazamLastData.have_locally;
    for (var i = 0; i < list.length; i++) {
        var t = list[i];
        var rowKey = ((t.artist || '') + ' - ' + (t.title || '')).trim();
        if (rowKey.toLowerCase() === tkl && t.filepath) return shazamPathB64FromFilepath(t.filepath);
    }
    return '';
}

function shazamRowActionPending(key) {
    return !!(shazamActionPending[key] || shazamPendingDownload[key]);
}

function shazamAnyRowActionPending() {
    return Object.keys(shazamActionPending || {}).length > 0 || Object.keys(shazamPendingDownload || {}).length > 0;
}

/** Clear per-row action pending for a track key (and lowercase alias keys). Matches progress poll behavior. */
function shazamClearActionPendingForKey(k) {
    if (!k) return;
    delete shazamActionPending[k];
    var kl = (k || '').toLowerCase();
    Object.keys(shazamActionPending).forEach(function (pk) {
        if ((pk || '').toLowerCase() === kl) delete shazamActionPending[pk];
    });
    // Sets rows render the same pending state — refresh them too so their
    // spinners clear the moment the action resolves (not on the next poll).
    try {
        var setsPanel = document.getElementById('tab-panel-sets');
        if (setsPanel && setsPanel.classList.contains('active') && typeof setsRender === 'function') setsRender();
    } catch (e) { /* ignore */ }
}

/** Omit "N failed" when N is 0 so the bar does not read "0 failed". */
function shazamFailedSuffix(failed) {
    var f = Number(failed) || 0;
    return f > 0 ? ', ' + f + ' failed' : '';
}
/** Pending batch jobs when one is already running. Each item: { id, type: 'search'|'star_batch'|'sync_favorites', label: string, payload: object }. */
let shazamJobQueue = [];
let shazamJobId = 0;
/** True for the entire lifecycle of a single-track star/unstar: from click until the bar is fully hidden and cleanup is done. While true, no other bar may appear and restore/status-apply skip bar-related work. */
let shazamSingleBarActive = false;

async function shazamLoadSettings() {
    try {
        const res = await fetch('/api/settings');
        const cfg = await res.json();
        if (res.ok) hideConnectionBanner();
        shazamApplySettings(cfg);
        return cfg;
    } catch (e) {
        console.error(e);
        shazamApplySettings({});
        return {};
    }
}

let shazamLastSettings = null;
function shazamApplySettings(cfg) {
    shazamLastSettings = cfg || null;
    shazamFolderInputs = (cfg.destination_folders || []).slice();
    shazamRenderFolderList();
    const downloadFolder = (cfg.soundeo_download_folder || '').trim();
    const destFolders = (cfg.destination_folders_raw || cfg.destination_folders || []).filter(Boolean);
    const downloadListEl = document.getElementById('shazamDownloadFolderList');
    if (downloadListEl) {
        if (destFolders.length === 0) {
            downloadListEl.innerHTML = '<span class="folder-hint">Add destination folders above first.</span>';
        } else {
            const currentNorm = downloadFolder.replace(/\/$/, '');
            downloadListEl.innerHTML = destFolders.map(path => {
                const norm = path.replace(/\/$/, '');
                const active = norm === currentNorm;
                const label = path.split(/[/\\]/).filter(Boolean).pop() || path.slice(0, 40);
                return `<button type="button" class="btn btn-small ${active ? 'btn-primary' : 'btn-secondary'}" data-download-folder="${(path || '').replace(/"/g, '&quot;')}" onclick="shazamSetDownloadFolder(this)" title="${(path || '').replace(/"/g, '&quot;')}">${active ? '✓ ' : ''}${label}${label.length >= (path || '').length ? '' : '…'}</button>`;
            }).join(' ');
        }
    }
    const headedToggle = document.getElementById('shazamHeadedModeToggle');
    if (headedToggle) headedToggle.checked = cfg.headed_mode !== false;
    const searchModeSel = document.getElementById('shazamSearchModeSelect');
    if (searchModeSel) searchModeSel.value = (cfg.search_mode === 'browser_hidden' || cfg.search_mode === 'browser_visible') ? cfg.search_mode : 'api';
    const statusEl = document.getElementById('soundeoSessionStatus');
    const pathEl = document.getElementById('soundeoSessionPath');
    const configPathEl = document.getElementById('configPathHint');
    const btn = document.getElementById('shazamSaveSessionBtn');
    const hasSession = !!(cfg.soundeo_cookies_path || cfg.soundeo_cookies_path_resolved);
    if (statusEl) statusEl.textContent = hasSession ? '· connected' : '· not connected';
    if (btn) btn.textContent = hasSession ? 'Reconnect' : 'Connect Soundeo';
    if (pathEl) pathEl.style.display = 'none';
    if (configPathEl) {
        if (cfg.config_path) {
            configPathEl.textContent = 'Config: ' + cfg.config_path;
            configPathEl.style.display = 'block';
        } else {
            configPathEl.style.display = 'none';
        }
    }
}

async function shazamBootstrapLoad() {
    const trackList = document.getElementById('shazamTrackList');
    if (trackList) trackList.innerHTML = '<p class="shazam-info-msg">Loading your library…</p>';
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), SHAZAM_BOOTSTRAP_TIMEOUT_MS);
        const res = await fetch('/api/shazam-sync/bootstrap', { signal: controller.signal });
        clearTimeout(timeoutId);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Bootstrap failed');
        hideConnectionBanner();
        const cfg = data.settings || {};
        const status = data.status || {};
        shazamApplySettings(cfg);
        shazamApplyStatus(status);
    } catch (e) {
        console.error('Bootstrap failed:', e);
        const msg = e.name === 'AbortError'
            ? 'Request timed out after ' + Math.round(SHAZAM_BOOTSTRAP_TIMEOUT_MS / 1000) + 's. Server may still be working — try Retry, or restart the app (python3 app.py).'
            : (e.message || 'Could not load settings and tracks.');
        if (trackList) trackList.innerHTML =
            '<p class="shazam-info-msg shazam-warning">' + escapeHtml(msg) +
            ' Is the server running? <button type="button" class="btn btn-small" onclick="shazamBootstrapLoad()">Retry</button></p>';
        shazamLoadSettings().then(function (cfg) {
            if (cfg && (cfg.destination_folders_raw || cfg.destination_folders || []).length) return;
            shazamLoadStatus();
        }).catch(function () {
            shazamLoadStatus();
        });
    }
}

function shazamRenderFolderList() {
    const el = document.getElementById('shazamFolderList');
    const rows = shazamFolderInputs.length ? shazamFolderInputs : [''];
    el.innerHTML = rows.map((path, i) =>
        `<div class="folder-list-item"><input type="text" value="${(path || '').replace(/"/g, '&quot;')}" placeholder="${i === 0 && !path ? 'Paste folder path or click Add Folder' : ''}" data-idx="${i}" onchange="shazamFolderChanged(this)" />${path ? `<button onclick="shazamRescanFolder(${i})" class="btn btn-small" title="Rescan this folder only">Rescan</button>` : ''}<button onclick="shazamRemoveFolder(${i})" class="btn btn-small" title="Remove folder" ${rows.length === 1 && !path ? 'style="visibility:hidden"' : ''}>✕</button></div>`
    ).join('');
}

async function shazamSetSearchMode(mode) {
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ search_mode: mode })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Save failed');
        if (shazamLastSettings) shazamLastSettings.search_mode = mode;
    } catch (e) {
        alert('Could not save search mode: ' + (e.message || e));
    }
}

async function shazamSetHeadedMode(showBrowser) {
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ headed_mode: !!showBrowser })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data && data.headed_mode !== undefined) {
            const toggle = document.getElementById('shazamHeadedModeToggle');
            if (toggle) toggle.checked = data.headed_mode;
            if (shazamLastSettings) shazamLastSettings.headed_mode = data.headed_mode;
        }
    } catch (e) { console.error(e); }
}

async function shazamSetDownloadFolder(btn) {
    const path = (btn.dataset.downloadFolder || '').trim();
    const current = (shazamLastSettings && shazamLastSettings.soundeo_download_folder) ? (shazamLastSettings.soundeo_download_folder || '').replace(/\/$/, '') : '';
    const newPath = (path.replace(/\/$/, '') === current) ? '' : path;
    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ soundeo_download_folder: newPath })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok)
            shazamApplySettings(data);
    } catch (e) { console.error(e); }
}

function shazamFolderChanged(input) {
    const idx = parseInt(input.dataset.idx, 10);
    const val = input.value.trim();
    if (shazamFolderInputs.length <= idx) {
        while (shazamFolderInputs.length <= idx) shazamFolderInputs.push('');
    }
    shazamFolderInputs[idx] = val;
    if (shazamFolderInputs.length === 1 && !val) shazamFolderInputs = [];
    shazamRenderFolderList();
    const folders = shazamFolderInputs.filter(Boolean);
    if (folders.length) {
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ destination_folders: folders })
        }).catch(() => {});
    }
}

function shazamRemoveFolder(idx) {
    shazamFolderInputs.splice(idx, 1);
    shazamRenderFolderList();
    const folders = shazamFolderInputs.filter(Boolean);
    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination_folders: folders })
    }).catch(() => {});
}

async function shazamRescanFolder(idx) {
    const path = (shazamFolderInputs[idx] || '').trim();
    if (!path) {
        alert('Enter a folder path first.');
        return;
    }
    try {
        const res = await fetch('/api/shazam-sync/rescan-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: path }),
        });
        const data = await res.json();
        if (data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                var folderName = path.split(/[/\\]/).filter(Boolean).pop() || path.slice(0, 30);
                if (folderName.length > 30) folderName = folderName.slice(0, 27) + '…';
                shazamJobQueue.push({ id: ++shazamJobId, type: 'rescan_folder', label: 'Rescan: ' + folderName, payload: { folder_path: path } });
                shazamRenderJobQueue();
            } else {
                alert(data.error);
            }
            return;
        }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0);
            shazamStartComparePoll(Date.now());
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        shazamRenderTrackList(data);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function shazamAddFolder() {
    showLoading('Select folder...');
    try {
        const res = await fetch('/api/browse-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        hideLoading();
        if (data.folder_path && !shazamFolderInputs.includes(data.folder_path)) {
            shazamFolderInputs.push(data.folder_path);
            shazamRenderFolderList();
            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ destination_folders: shazamFolderInputs.filter(Boolean) })
            }).catch(() => {});
        }
    } catch (e) {
        hideLoading();
        alert('Error: ' + e.message);
    }
}

async function shazamSaveSettings() {
    const inputs = document.querySelectorAll('#shazamFolderList input');
    shazamFolderInputs = Array.from(inputs).map(i => i.value.trim()).filter(Boolean);
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ destination_folders: shazamFolderInputs })
        });
        alert('Settings saved.');
        shazamRenderFolderList();
    } catch (e) {
        alert('Error saving: ' + e.message);
    }
}

async function shazamCheckBrowser() {
    try {
        const res = await fetch('/api/soundeo/browser-check');
        const data = await res.json();
        if (data.ok) {
            alert('Browser check OK.\nMode: ' + (data.mode || 'launch') + '\n' + (data.message || ''));
        } else {
            const msg = [data.error || 'Unknown error', data.hint ? '\n\n' + data.hint : ''].join('');
            alert('Browser check failed:\n\n' + msg);
        }
    } catch (e) {
        alert('Check failed: ' + e.message);
    }
}

async function shazamSaveSession() {
    const statusEl = document.getElementById('soundeoSessionStatus');
    const saveBtn = document.getElementById('shazamSaveSessionBtn');
    if (saveBtn) saveBtn.disabled = true;
    if (statusEl) statusEl.textContent = 'Opening browser…';
    try {
        const res = await fetch('/api/soundeo/start-save-session', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.error) {
            if (statusEl) statusEl.textContent = 'Soundeo session: not connected';
            if (saveBtn) saveBtn.disabled = false;
            const msg = data.detail ? data.error + '\n\n' + data.detail : data.error;
            alert(msg);
            return;
        }
        if (statusEl) statusEl.textContent = 'Waiting for login…';
        document.getElementById('shazamLoggedInBtn').style.display = 'inline-block';
        if (saveBtn) saveBtn.style.display = 'none';
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Soundeo session: not connected';
        if (saveBtn) saveBtn.disabled = false;
        alert('Error: ' + e.message);
    }
}

async function shazamSessionSaved() {
    const statusEl = document.getElementById('soundeoSessionStatus');
    const loggedInBtn = document.getElementById('shazamLoggedInBtn');
    const saveBtn = document.getElementById('shazamSaveSessionBtn');
    if (loggedInBtn) loggedInBtn.disabled = true;
    if (statusEl) statusEl.textContent = 'Saving session…';
    try {
        const res = await fetch('/api/soundeo/session-saved', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (loggedInBtn) { loggedInBtn.style.display = 'none'; loggedInBtn.disabled = false; }
        if (saveBtn) { saveBtn.style.display = ''; saveBtn.disabled = false; }
        if (data.logged_in) {
            if (statusEl) statusEl.textContent = 'Soundeo session: connected';
            if (saveBtn) saveBtn.textContent = 'Reconnect';
        } else {
            if (statusEl) statusEl.textContent = 'Soundeo session: login failed';
            alert(data.message || 'Could not verify login. Try again and make sure you are logged in before clicking the button.');
        }
    } catch (e) {
        if (loggedInBtn) { loggedInBtn.style.display = 'none'; loggedInBtn.disabled = false; }
        if (saveBtn) { saveBtn.style.display = ''; saveBtn.disabled = false; }
        if (statusEl) statusEl.textContent = 'Soundeo session: error';
        alert('Error: ' + e.message);
    }
}

async function shazamFetchShazam() {
    try {
        const pRes = await fetch('/api/shazam-sync/progress');
        const p = await pRes.json();
        if (p && p.running) {
            shazamJobQueue.push({ id: ++shazamJobId, type: 'fetch_shazam', label: 'Fetch Shazam', payload: {} });
            shazamRenderJobQueue();
            return;
        }
        const sRes = await fetch('/api/shazam-sync/status');
        const s = await sRes.json();
        if (s && s.compare_running) {
            shazamJobQueue.push({ id: ++shazamJobId, type: 'fetch_shazam', label: 'Fetch Shazam', payload: {} });
            shazamRenderJobQueue();
            return;
        }
    } catch (_) {}
    showLoading('Fetching Shazam tracks...');
    try {
        const res = await fetch('/api/shazam-sync/fetch-shazam', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        hideLoading();
        if (!res.ok) {
            alert(data.error || 'Fetch failed');
            return;
        }
        if (data.error || (data.total === 0 && data.added === 0)) {
            alert(data.error || 'No tracks found in Shazam database.');
            return;
        }
        alert(data.message || `Fetched. Total: ${data.total}, New: ${data.added}`);
        await shazamLoadStatus();
        shazamSearchAllOnSoundeo('new');
    } catch (e) {
        hideLoading();
        alert('Error: ' + e.message);
    }
}

function shazamShowCompareProgress(show, current, total, message) {
    const el = document.getElementById('shazamCompareProgress');
    const barWrap = el ? el.querySelector('.shazam-compare-progress-bar') : null;
    const fill = document.getElementById('shazamCompareProgressFill');
    const text = document.getElementById('shazamCompareProgressText');
    const rescanBtn = document.getElementById('shazamRescanDropdownBtn');
    if (!el) return;
    if (rescanBtn) rescanBtn.disabled = show;
    if (show) {
        el.style.display = 'flex';
        const indeterminate = total == null || total <= 0;
        if (barWrap) barWrap.classList.toggle('indeterminate', indeterminate);
        if (fill) {
            fill.style.width = indeterminate ? '0%' : (Math.round((current / total) * 100) + '%');
        }
        if (text) {
            text.textContent = message || (total > 0 ? 'Scanning: ' + current.toLocaleString() + ' / ' + total.toLocaleString() : 'Starting...');
        }
    } else {
        el.style.display = 'none';
        if (barWrap) barWrap.classList.remove('indeterminate');
    }
}

async function shazamCancelCompare() {
    try {
        await fetch('/api/shazam-sync/cancel-compare', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
    } catch (_) {}
}

async function shazamCompare() {
    const progressEl = document.getElementById('shazamCompareProgress');
    const progressText = document.getElementById('shazamCompareProgressText');
    try {
        if (progressEl) {
            progressEl.style.display = 'flex';
            progressEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            if (progressText) progressText.textContent = 'Starting compare...';
        }
        const inputs = document.querySelectorAll('#shazamFolderList input');
        const folders = Array.from(inputs).map(i => i.value.trim()).filter(Boolean);
        if (folders.length) {
            shazamFolderInputs = folders;
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ destination_folders: folders })
            });
        }
        const res = await fetch('/api/shazam-sync/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (!res.ok) {
            shazamShowCompareProgress(false);
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                shazamJobQueue.push({ id: ++shazamJobId, type: 'compare', label: 'Compare', payload: {} });
                shazamRenderJobQueue();
            } else {
                alert(data.error || 'Compare failed');
                shazamRenderTrackList(data);
            }
            return;
        }
        if (data.error) {
            shazamShowCompareProgress(false);
            alert(data.error);
            shazamRenderTrackList(data);
            return;
        }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0, 'Starting compare...');
            shazamStartComparePoll(Date.now());
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        shazamRenderTrackList(data);
    } catch (e) {
        shazamShowCompareProgress(false);
        alert('Error: ' + e.message);
    }
}

async function shazamComparePoll(startTime) {
    try {
        if (startTime != null && Date.now() - startTime > SHAZAM_COMPARE_POLL_TIMEOUT_MS) {
            if (shazamComparePollInterval) {
                clearInterval(shazamComparePollInterval);
                shazamComparePollInterval = null;
            }
            shazamShowCompareProgress(false);
            alert('Compare timed out. Try again.');
            return;
        }
        const res = await fetch('/api/shazam-sync/status');
        const data = await res.json();
        if (data.compare_running) {
            const sp = data.scan_progress || {};
            const mp = data.match_progress || {};
            let progressMsg;
            let barCurrent, barTotal;
            if (sp.total !== undefined || sp.current !== undefined) {
                barCurrent = sp.current || 0;
                barTotal = sp.total || 0;
                progressMsg = barTotal > 0 ? (barCurrent.toLocaleString() + ' / ' + barTotal.toLocaleString() + ' files') : (sp.message || 'Discovering files...');
            } else if (mp.running && mp.total > 0) {
                barCurrent = (mp.current || 0) + 1;
                barTotal = mp.total || 0;
                progressMsg = 'Matching: ' + barCurrent.toLocaleString() + ' / ' + barTotal.toLocaleString() + ' tracks';
            } else {
                barCurrent = 0;
                barTotal = 1;
                progressMsg = data.message || 'Comparing...';
            }
            shazamShowCompareProgress(true, barCurrent, barTotal, progressMsg);
            shazamCurrentProgress = mp.running && mp.current_key
                ? { running: true, current_key: mp.current_key }
                : {};
            shazamApplyStatus(data);
            return;
        }
        if (shazamComparePollInterval) {
            clearInterval(shazamComparePollInterval);
            shazamComparePollInterval = null;
        }
        shazamShowCompareProgress(false);
        shazamCurrentProgress = {};
        shazamApplyStatus(data);
        shazamMaybeStartQueuedJob();
    } catch (e) {
        if (shazamComparePollInterval) {
            clearInterval(shazamComparePollInterval);
            shazamComparePollInterval = null;
        }
        shazamShowCompareProgress(false);
        alert('Compare status check failed: ' + (e && e.message ? e.message : 'Unknown error'));
    }
}

async function shazamRescan(compareAfter) {
    if (compareAfter === undefined) compareAfter = true;
    const progressEl = document.getElementById('shazamCompareProgress');
    const progressText = document.getElementById('shazamCompareProgressText');
    try {
        if (progressEl) {
            progressEl.style.display = 'flex';
            progressEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            if (progressText) progressText.textContent = compareAfter ? 'Starting rescan & compare...' : 'Starting rescan...';
        }
        const res = await fetch('/api/shazam-sync/rescan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ compare_after: compareAfter })
        });
        const data = await res.json();
        if (!res.ok) {
            shazamShowCompareProgress(false);
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                shazamJobQueue.push({ id: ++shazamJobId, type: 'rescan', label: compareAfter ? 'Rescan & compare' : 'Rescan', payload: { compare_after: compareAfter } });
                shazamRenderJobQueue();
            } else {
                alert(data.error || 'Rescan failed');
            }
            return;
        }
        if (data.error) {
            shazamShowCompareProgress(false);
            alert(data.error);
            shazamRenderTrackList(data);
            return;
        }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0, compareAfter ? 'Starting rescan & compare...' : 'Rescanning folders...');
            shazamStartComparePoll(Date.now());
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        shazamRenderTrackList(data);
    } catch (e) {
        shazamShowCompareProgress(false);
        alert('Error: ' + e.message);
    }
}

async function shazamLoadStatus(retryCount = 0) {
    const maxRetries = 4;
    const retryDelay = 400;
    try {
        const res = await fetch('/api/shazam-sync/status');
        const data = await res.json();
        shazamApplyStatus(data);
    } catch (e) {
        if (retryCount < maxRetries) {
            await new Promise(r => setTimeout(r, retryDelay));
            return shazamLoadStatus(retryCount + 1);
        }
        document.getElementById('shazamTrackList').innerHTML =
            '<p class="shazam-info-msg shazam-warning">Failed to load. Check console. <button type="button" class="btn btn-small" onclick="shazamBootstrapLoad()">Retry</button></p>';
        console.warn('Shazam status load failed:', e.message);
    }
}

function shazamApplyStatus(data) {
    if (!data) data = {};
    const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setText('shazamCount', data.shazam_count ?? 0);
    setText('shazamLocalCount', data.local_count ?? 0);
    setText('shazamHaveCount', (data.have_locally && data.have_locally.length) ?? 0);
    setText('shazamToDownloadCount', data.to_download_count ?? 0);
    const warnEl = document.getElementById('shazamFolderWarning');
    if (warnEl) {
        if (data.folder_warning) {
            warnEl.textContent = data.folder_warning;
            warnEl.style.display = 'block';
        } else {
            warnEl.style.display = 'none';
        }
    }
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.dismissed) Object.assign(shazamDismissed, data.dismissed);
    if (data.dismissed_manual_check && Array.isArray(data.dismissed_manual_check)) {
        shazamDismissedManualCheck = {};
        data.dismissed_manual_check.forEach(k => { shazamDismissedManualCheck[k] = true; });
    }
    if (data.soundeo_titles && typeof data.soundeo_titles === 'object') {
        Object.assign(shazamSoundeoTitles, data.soundeo_titles);
    }
    if (data.cover_hashes && typeof data.cover_hashes === 'object') {
        shazamMergeCoverHashes(data.cover_hashes);
    }
    // If a cover backfill is running server-side, start the self-terminating
    // watcher so newly-cached covers populate in place (no manual reload needed).
    if (data.cover_backfill && data.cover_backfill.running) {
        shazamStartCoverBackfillWatch();
    }
    // Local-only personal-curation flags. MERGE (not replace) so a status poll
    // that races with an in-flight optimistic update doesn't wipe the just-set
    // maybe/listened state. Server is authoritative on full page load (initial
    // shazamMaybe/shazamListened start empty); subsequent merges add new keys
    // and overwrite existing ones with server truth, preserving optimistic
    // writes that the server hasn't acknowledged yet.
    if (data.maybe && typeof data.maybe === 'object') {
        Object.assign(shazamMaybe, data.maybe);
    }
    if (data.listened && typeof data.listened === 'object') {
        Object.assign(shazamListened, data.listened);
    }
    // not_found: only replace when applying fresh server data (so reset/refresh shows grey). Never replace inside shazamRenderTrackList or we wipe per-row search updates.
    if (data.hasOwnProperty('not_found') && typeof data.not_found === 'object') {
        shazamNotFound = {};
        Object.assign(shazamNotFound, data.not_found);
    }
    shazamMergeDownloadProgressFromPayload(data);
    // Sticky download-error banner — surfaces no_credits / subscription / network
    // errors that are easy to miss in the small progress bar.
    if (typeof shazamUpdateDownloadErrorBanner === 'function') {
        shazamUpdateDownloadErrorBanner(data && data.download_progress);
    }
    shazamScheduleRenderTrackList(data, !shazamShouldThrottleTrackListRender(data));
    if (data.download_queue && Array.isArray(data.download_queue)) {
        shazamCurrentDownloadQueue = data.download_queue;
        if (!shazamSingleBarActive) shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
    }
    if (data.compare_running && !shazamComparePollInterval) {
        const sp = data.scan_progress || {};
        const cur = sp.current || 0;
        const tot = sp.total || 0;
        const msg = sp.message || (tot > 0 ? null : 'Discovering files...');
        shazamShowCompareProgress(true, cur, tot, msg || (tot > 0 ? (cur.toLocaleString() + ' / ' + tot.toLocaleString()) : undefined));
        shazamStartComparePoll(Date.now());
    } else if (!data.compare_running) {
        shazamShowCompareProgress(false);
    }
    shazamRestoreProgressIfRunning();
    shazamBarUpdateActions();
}

/** True when a single-track star/unstar action is in flight. Uses the lifecycle flag, NOT shazamCurrentProgress (which gets cleared before the bar is hidden). */
function shazamIsSingleTrackProgress() {
    return !!shazamSingleBarActive;
}

/** If a sync/search job is still running on the server, show the progress bar and poll until done. */
function shazamRestoreProgressIfRunning() {
    if (shazamSingleBarActive) {
        shazamBarLog('RESTORE', 'skip entirely (shazamSingleBarActive)');
        return;
    }
    shazamBarLog('RESTORE', 'fetching progress to restore');
    fetch('/api/shazam-sync/progress')
        .then(r => r.json())
        .then(p => {
            if (shazamSingleBarActive) {
                shazamBarLog('RESTORE', 'skip (shazamSingleBarActive set while fetch was in flight)');
                return;
            }
            if (!p.running) {
                shazamBarLog('RESTORE', 'progress not running, skip');
                return;
            }
            var isSingleStarUnstar = p.mode === 'star_single' || p.mode === 'unstar_single';
            if (isSingleStarUnstar) {
                shazamBarLog('RESTORE', 'single-track: handler owns the bar, skip');
                shazamCurrentProgress = p;
                return;
            }
            shazamBarLog('RESTORE', 'job running, will show progress if bar hidden', { mode: p.mode });
            shazamCurrentProgress = p;
            shazamApplyQueueState(p.star_queue || [], p.single_search_queue || [], p.unstar_queue || []);
            if (p.download_queue && Array.isArray(p.download_queue)) {
                shazamCurrentDownloadQueue = p.download_queue;
                shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
            }
            shazamSetProgressClickable(!!p.current_key);
            if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
            const barEl = document.getElementById('shazamSyncProgress');
            if (barEl && barEl.style.display === 'flex') {
                shazamBarLog('RESTORE', 'progress bar already visible, skip SHOW_PROGRESS');
                return; /* already visible and likely already polling */
            }
            if (shazamProgressRestoreInterval) clearInterval(shazamProgressRestoreInterval);
            const stopBtn = document.getElementById('shazamSyncStopBtn');
            const total = p.total != null && p.total > 0 ? p.total : null;
            const cur = p.current != null ? p.current : 0;
            let initText;
            if (total != null && p.mode === 'search_global') {
                const label = p.search_mode === 'unfound' ? 'Unfound' : p.search_mode === 'new' ? 'New' : 'Search';
                initText = `${label}: ${cur}/${total}${p.message ? ' — ' + p.message : ''}`;
            } else {
                initText = (p.current != null && p.total != null) ? `${p.current}/${p.total}: ${p.message || ''}` : (p.message || 'Running…');
            }
            shazamBarLog('RESTORE', 'calling shazamShowSyncProgress');
            shazamShowSyncProgress(initText);
            if (stopBtn) stopBtn.disabled = false;
            let restorePollCount = 0;
            shazamProgressRestoreInterval = setInterval(function () {
                fetch('/api/shazam-sync/progress')
                    .then(r => r.json())
                    .then(p => {
                        shazamCurrentProgress = p;
                        /* Always mirror server queues — skipping during star_single left stale pills (server popped current item, UI still showed it). */
                        shazamApplyQueueState(p.star_queue || [], p.single_search_queue || [], p.unstar_queue || []);
                        if (p.download_queue && Array.isArray(p.download_queue)) {
                            shazamCurrentDownloadQueue = p.download_queue;
                            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
                        }
                        const el = document.getElementById('shazamProgress');
                        const stopBtn = document.getElementById('shazamSyncStopBtn');
                        if (el) {
                            if (p.running) {
                                const tot = p.total != null && p.total > 0 ? p.total : null;
                                const c = p.current != null ? p.current : 0;
                                let text;
                                if (tot != null && p.mode === 'search_global') {
                                    const label = p.search_mode === 'unfound' ? 'Unfound' : p.search_mode === 'new' ? 'New' : 'Search';
                                    text = `${label}: ${c}/${tot}${p.message ? ' — ' + p.message : ''}`;
                                } else if (p.mode === 'sync_favorites') {
                                    text = p.message || 'Sync favorites…';
                                } else if (p.mode === 'unstar_single') {
                                    text = p.message || 'Unstarring…';
                                } else {
                                    text = (p.current != null && p.total != null) ? `${p.current}/${p.total}: ${p.message || ''}` : (p.message || 'Running…');
                                }
                                if (p.last_url && p.mode !== 'unstar_single') {
                                    const urlDisplay = p.last_url.replace(/^https?:\/\//, '');
                                    text += ' — ' + urlDisplay.slice(0, 50) + (urlDisplay.length > 50 ? '…' : '');
                                }
                                el.textContent = text;
                            }
                        }
                        shazamSetProgressClickable(p.running && !!p.current_key);
                        if (p.running) {
                            restorePollCount++;
                            var restoreStatusEvery = shazamShouldThrottleTrackListRender() ? 4 : 2;
                            if (restorePollCount % restoreStatusEvery === 1) {
                                fetch('/api/shazam-sync/status').then(r => r.json()).then(data => {
                                    if (data && !data.compare_running) {
                                        shazamApplyStatus(data);
                                    }
                                }).catch(() => {});
                            }
                            var hasPendingRestoreRerender = shazamAnyRowActionPending();
                            var skipRestoreRerender = hasPendingRestoreRerender || (p.mode === 'star_single' || p.mode === 'unstar_single');
                            if (shazamLastData && !skipRestoreRerender) {
                                shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
                                if (shazamFollowCurrentRow && p.current_key) shazamScrollCurrentRowToCenter(false);
                            }
                        }
                        if (!p.running) {
                            shazamFollowCurrentRow = false;
                            shazamCurrentProgress = {};
                            if (shazamProgressRestoreInterval) {
                                clearInterval(shazamProgressRestoreInterval);
                                shazamProgressRestoreInterval = null;
                            }
                            if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stop'; }
                            const gotoBtn = document.getElementById('shazamProgressGotoBtn');
                            if (gotoBtn) gotoBtn.textContent = 'Follow row';
                            shazamHideSyncProgress();
                            shazamLoadStatus();
                            shazamMaybeStartQueuedJob();
                        }
                    })
                    .catch(() => {});
            }, 500);
        })
        .catch(() => {});
}

function shazamFormatRelativeTime(unixSec) {
    if (unixSec == null || typeof unixSec !== 'number') return '—';
    const sec = Math.floor(Date.now() / 1000) - unixSec;
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.floor(sec / 60) + 'm';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h';
    if (sec < 604800) return Math.floor(sec / 86400) + 'd';
    if (sec < 2592000) return Math.floor(sec / 604800) + 'wk';
    if (sec < 31536000) return Math.floor(sec / 2592000) + 'mo';
    return Math.floor(sec / 31536000) + 'y';
}

let shazamToDownloadTracks = [];
let shazamLastData = null;

/** Full table rebuild is expensive (~3k rows). Coalesce during long jobs so hover/cursor stay responsive. */
const SHAZAM_TRACK_LIST_RENDER_MIN_MS = 900;
let _shazamRenderListRaf = 0;
let _shazamRenderListTimer = null;
let _shazamRenderListLastAt = 0;
// Fallback timer that races requestAnimationFrame. rAF callbacks are PAUSED by
// the browser while the tab is hidden/backgrounded, so a render scheduled only
// via rAF never flushes until the tab is focused — the list gets stuck on
// "Loading…". This setTimeout fires regardless of visibility; whichever wins
// flushes and cancels the other.
let _shazamRenderListFallbackTimer = null;
const SHAZAM_RENDER_RAF_FALLBACK_MS = 200;

function shazamShouldThrottleTrackListRender(statusPayload) {
    var d = statusPayload || shazamLastData;
    if (d && d.compare_running) return true;
    var p = shazamCurrentProgress || {};
    if (shazamDownloadProgressSnapshot && shazamDownloadProgressSnapshot.running) return true;
    if (!p.running) return false;
    var m = p.mode || '';
    return (
        m === 'search_global' || m === 'search_single' || m === 'sync_single' ||
        m === 'star_batch' || m === 'sync_favorites'
    );
}

function shazamCancelPendingTrackListRender() {
    if (_shazamRenderListTimer) {
        clearTimeout(_shazamRenderListTimer);
        _shazamRenderListTimer = null;
    }
    if (_shazamRenderListFallbackTimer) {
        clearTimeout(_shazamRenderListFallbackTimer);
        _shazamRenderListFallbackTimer = null;
    }
    if (_shazamRenderListRaf) {
        cancelAnimationFrame(_shazamRenderListRaf);
        _shazamRenderListRaf = 0;
    }
}

/**
 * Schedule a track table rebuild. Use force=true for filters, clicks, and job completion.
 * When throttled, at most ~1 full render per SHAZAM_TRACK_LIST_RENDER_MIN_MS during compare/search/sync.
 */
function shazamScheduleRenderTrackList(data, force) {
    var payload = data != null ? data : shazamLastData;
    if (!payload) return;
    var throttle = !force && shazamShouldThrottleTrackListRender(payload);
    // Idempotent flush — cancels its sibling timer (rAF vs setTimeout fallback)
    // so the race winner renders exactly once.
    var flush = function () {
        shazamCancelPendingTrackListRender();
        if (!payload) return;
        shazamRenderTrackList(payload);
        _shazamRenderListLastAt = Date.now();
    };
    // Arm rAF (smooth paint when visible) AND a setTimeout fallback (fires even
    // when the tab is hidden, where rAF is paused). Without the fallback the
    // first render can hang on "Loading…" until the tab is focused.
    var armRafWithFallback = function () {
        _shazamRenderListRaf = requestAnimationFrame(flush);
        _shazamRenderListFallbackTimer = setTimeout(flush, SHAZAM_RENDER_RAF_FALLBACK_MS);
    };
    shazamCancelPendingTrackListRender();
    if (!throttle) {
        armRafWithFallback();
        return;
    }
    var now = Date.now();
    var wait = Math.max(0, SHAZAM_TRACK_LIST_RENDER_MIN_MS - (now - _shazamRenderListLastAt));
    if (wait <= 0) {
        armRafWithFallback();
    } else {
        _shazamRenderListTimer = setTimeout(function () {
            _shazamRenderListTimer = null;
            armRafWithFallback();
        }, wait);
    }
}

let shazamFilterTime = 'all';
const SHAZAM_FILTER_STATUS_KEY = 'mp3cleaner_shazam_filter_status';
// 'todl' = Available (found on Soundeo, not downloaded). 'shortlist' / 'maybe'
// are SUBSETS of Available — added 2026-04-30 to mirror Keith's funnel:
// Available → (Maybe | Shortlist) → Download.
const SHAZAM_FILTER_STATUS_VALUES = ['all', 'have', 'todl', 'shortlist', 'maybe', 'skipped'];
let shazamFilterStatus = 'all';
let shazamFilterSearch = '';
/** Scan Soundeo favorites range: 'all' | '1_month' | '2_months' | '3_months'. Use All time to fix starred state. */
let shazamScanRange = 'all';
let shazamCurrentlyPlaying = null;
let shazamAudioEl = null;
/** Row play button for the currently playing track (for bar sync). */
let shazamPlayingBtn = null;
let shazamBarTimeUpdate = null;
let shazamBarEnded = null;
/** Proxy ID for temp MP3 (AIFF/WAV); released on end/close/switch. */
let shazamCurrentProxyId = null;
/** True after we've alerted the user about a Soundeo preview failure this session.
 * The connection banner stays visible; this just suppresses repeated alert() spam
 * so a user clicking play on five expired-session rows in a row only sees one popup. */
let shazamSoundeoPlayErrorAlerted = false;
/** Pre-buffered next track so playback can continue when the current track ends. */
let shazamNextBuffer = null;
/** Cover art hashes for Sync list / play bar (from server status.cover_hashes). */
let shazamCoverHashes = {};
/** Metadata of the currently playing track (player bar star / download / skip). */
let shazamBarKey = null;
let shazamBarSoundeoUrl = null;
let shazamBarArtist = null;
let shazamBarTitle = null;

/**
 * Fill playbar artist/title from the row play button. Uses getAttribute because
 * data-title → dataset.title is unreliable in some browsers; falls back to
 * parsing data-track-key ("Artist - Title").
 */
function shazamBarSyncMetaFromPlayBtn(playBtn) {
    if (!playBtn) return;
    var a = (playBtn.getAttribute('data-artist') || '').trim();
    var t = (playBtn.getAttribute('data-title') || '').trim();
    var tk = (playBtn.getAttribute('data-track-key') || String(playBtn.dataset.trackKey || '')).trim();
    var sep = ' - ';
    var ix = tk.indexOf(sep);
    if (ix !== -1) {
        if (!a) a = tk.slice(0, ix).trim();
        if (!t) t = tk.slice(ix + sep.length).trim();
    }
    shazamBarArtist = a;
    shazamBarTitle = t;
}

function releaseShazamProxy() {
    if (!shazamCurrentProxyId) return;
    const pid = shazamCurrentProxyId;
    shazamCurrentProxyId = null;
    fetch('/api/shazam-sync/release-proxy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ proxy_id: pid })
    }).catch(function () {});
}

// Best-effort release on page unload (e.g. tab close)
window.addEventListener('beforeunload', function () {
    if (shazamCurrentProxyId) {
        navigator.sendBeacon('/api/shazam-sync/release-proxy', new Blob([JSON.stringify({ proxy_id: shazamCurrentProxyId })], { type: 'application/json' }));
    }
});

/** Find a play button by exact data-track-key (avoids brittle CSS attribute selectors). */
function shazamFindPlayBtnByTrackKey(trackKey) {
    if (!trackKey) return null;
    var btns = document.querySelectorAll('#shazamTrackList .shazam-play-btn[data-track-key]');
    for (var i = 0; i < btns.length; i++) {
        if (btns[i].dataset.trackKey === trackKey) return btns[i];
    }
    return null;
}

/** Same identity as shazamCurrentlyPlaying (local stream URL or raw Soundeo track page URL). */
function shazamPlayBtnPlaybackKey(btn) {
    if (!btn) return null;
    var dB64 = (btn.dataset.dirB64 || '').trim();
    var f = btn.dataset.file;
    var pB64 = (btn.dataset.pathB64 || '').trim();
    if (dB64 || pB64) {
        return (dB64 && f != null)
            ? '/api/shazam-sync/stream-file?dir=' + encodeURIComponent(dB64) + '&file=' + encodeURIComponent(f)
            : (pB64 ? '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pB64) : null);
    }
    var su = (btn.getAttribute('data-soundeo-url') || (btn.dataset && btn.dataset.soundeoUrl) || '').trim();
    return su || null;
}

/**
 * After innerHTML rebuild, shazamPlayingBtn may be detached but still truthy; dataset on detached
 * nodes made the bar show "have file" for the wrong track. Re-find the row button by current stream.
 */
function shazamReattachPlayingButton() {
    if (!shazamCurrentlyPlaying || !shazamAudioEl) return false;
    var list = document.getElementById('shazamTrackList');
    if (!list) return false;
    if (shazamPlayingBtn && shazamPlayingBtn.isConnected && shazamPlayBtnPlaybackKey(shazamPlayingBtn) === shazamCurrentlyPlaying) {
        return true;
    }
    var all = list.querySelectorAll('.shazam-play-btn');
    for (var i = 0; i < all.length; i++) {
        var pk = shazamPlayBtnPlaybackKey(all[i]);
        if (pk && pk === shazamCurrentlyPlaying) {
            shazamPlayingBtn = all[i];
            return true;
        }
    }
    return false;
}

/** Align playbar track key / Soundeo URL / artist-title with the DOM row for what's playing. */
function shazamSyncBarMetaFromPlayingButton() {
    if (!shazamReattachPlayingButton()) return;
    var pb = shazamPlayingBtn;
    if (!pb || !pb.isConnected) return;
    shazamBarKey = (pb.getAttribute('data-track-key') || pb.dataset.trackKey || '').trim();
    shazamBarSoundeoUrl = (pb.getAttribute('data-soundeo-url') || pb.dataset.soundeoUrl || '').trim();
    shazamBarSyncMetaFromPlayBtn(pb);
}

/** Cancel any pending next-track prefetch and release its resources. */
function shazamCancelNextBuffer() {
    var buf = shazamNextBuffer;
    shazamNextBuffer = null;
    if (!buf) return;
    buf.cancelled = true;
    if (buf.audioEl) {
        buf.audioEl.onended = null;
        buf.audioEl.onerror = null;
        buf.audioEl.pause();
        buf.audioEl.src = '';
    }
    if (buf.proxyId) {
        fetch('/api/shazam-sync/release-proxy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy_id: buf.proxyId })
        }).catch(function () {});
    }
}

/** Find the next play button after fromBtn's row (or shazamPlayingBtn if detached). */
function shazamFindNextBtn(fromBtn) {
    var effectiveBtn = (fromBtn && fromBtn.isConnected) ? fromBtn : shazamPlayingBtn;
    if (!effectiveBtn) return null;
    var fromRow = effectiveBtn.closest('tr');
    if (!fromRow) return null;
    var nextRow = fromRow.nextElementSibling;
    while (nextRow) {
        var b = nextRow.querySelector('.shazam-play-btn');
        if (b) return b;
        nextRow = nextRow.nextElementSibling;
    }
    return null;
}

/** Pre-fetch and pre-load the next track so it can start quickly when the current one ends. */
async function shazamPrefetchNext(fromBtn) {
    shazamCancelNextBuffer();
    // Don't pre-fetch the next track's preview while a queue download is
    // running. The prefetch endpoint full-downloads the preview MP3 from
    // Soundeo, which adds a third concurrent connection on the same cookie
    // session — Soundeo throttles this, and it's the dominant cause of "play
    // takes long during downloads". Buffering will resume after the download
    // queue completes.
    if (typeof shazamDownloadProgressSnapshot !== 'undefined'
        && shazamDownloadProgressSnapshot
        && shazamDownloadProgressSnapshot.running) {
        return;
    }
    var nextBtn = shazamFindNextBtn(fromBtn);
    if (!nextBtn) return;

    var buf = {
        type: null, trackKey: nextBtn.dataset.trackKey, btn: nextBtn,
        audioEl: null, mp3Url: null, proxyId: null, streamUrl: null,
        soundeoUrl: null, preparing: false, cancelled: false
    };

    var isSoundeo = nextBtn.classList.contains('shazam-soundeo-play') && !nextBtn.dataset.dirB64 && !nextBtn.dataset.pathB64;

    if (isSoundeo) {
        var trackUrl = nextBtn.dataset.soundeoUrl;
        if (!trackUrl) return;
        buf.type = 'soundeo';
        buf.soundeoUrl = trackUrl;
        buf.preparing = true;
        shazamNextBuffer = buf;
        try {
            var sRes = await fetch('/api/soundeo/prefetch-preview?track_url=' + encodeURIComponent(trackUrl));
            if (buf.cancelled) return;
            var sData = await sRes.json().catch(function () { return {}; });
            if (buf.cancelled) return;
            if (sRes.ok && sData.mp3_url) {
                buf.streamUrl = sData.mp3_url;
            } else {
                buf.streamUrl = '/api/soundeo/stream-preview?track_url=' + encodeURIComponent(trackUrl);
            }
        } catch (e) {
            if (!buf.cancelled) buf.streamUrl = '/api/soundeo/stream-preview?track_url=' + encodeURIComponent(trackUrl);
        }
        buf.preparing = false;
        if (!buf.cancelled && buf.streamUrl) {
            var sAudio = new Audio();
            sAudio.preload = 'auto';
            sAudio.src = buf.streamUrl;
            sAudio.load();
            buf.audioEl = sAudio;
        }
    } else {
        var dirB64 = (nextBtn.dataset.dirB64 || '').trim();
        var file = nextBtn.dataset.file || '';
        var pathB64 = (nextBtn.dataset.pathB64 || '').trim();
        var isAiff = /\.(aiff?|wav)$/.test(file.toLowerCase());

        if (isAiff) {
            var body = (dirB64 && file) ? { dir_b64: dirB64, file: file } : (pathB64 ? { path_b64: pathB64 } : null);
            if (!body) return;
            buf.type = 'aiff';
            buf.streamUrl = (dirB64 && file)
                ? '/api/shazam-sync/stream-file?dir=' + encodeURIComponent(dirB64) + '&file=' + encodeURIComponent(file)
                : '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pathB64);
            buf.preparing = true;
            shazamNextBuffer = buf;
            try {
                var prepCtrl = new AbortController();
                var prepTimer = setTimeout(function () { prepCtrl.abort(); }, 30000);
                var res = await fetch('/api/shazam-sync/prepare-proxy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                    signal: prepCtrl.signal
                });
                clearTimeout(prepTimer);
                if (buf.cancelled) return;
                var data = await res.json().catch(function () { return {}; });
                if (buf.cancelled) return;
                if (res.ok && data.mp3_url) {
                    buf.mp3Url = data.mp3_url;
                    buf.proxyId = data.proxy_id;
                    var proxyAudio = new Audio();
                    proxyAudio.preload = 'auto';
                    proxyAudio.src = data.mp3_url;
                    proxyAudio.load();
                    buf.audioEl = proxyAudio;
                }
            } catch (e) { /* fall back to shazamPlayNextTrack */ }
            buf.preparing = false;
        } else {
            var streamUrl2 = (dirB64 && file)
                ? '/api/shazam-sync/stream-file?dir=' + encodeURIComponent(dirB64) + '&file=' + encodeURIComponent(file)
                : (pathB64 ? '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pathB64) : null);
            if (!streamUrl2) return;
            buf.type = 'local';
            buf.streamUrl = streamUrl2;
            shazamNextBuffer = buf;
            var localAudio = new Audio();
            localAudio.preload = 'auto';
            localAudio.src = streamUrl2;
            localAudio.load();
            buf.audioEl = localAudio;
        }
    }
}

/**
 * When a track ends: play the pre-buffered next track if available, else start the next row normally.
 */
async function shazamPlayFromBuffer(prevRow, prevKey) {
    var buf = shazamNextBuffer;
    if (!buf || buf.cancelled) {
        shazamNextBuffer = null;
        shazamPlayNextTrack(prevRow, prevKey);
        return;
    }

    if (buf.preparing) {
        var waited = 0;
        while (buf.preparing && !buf.cancelled && waited < 6000) {
            await new Promise(function (r) { setTimeout(r, 100); });
            waited += 100;
        }
    }

    if (buf.cancelled || !buf.audioEl) {
        shazamNextBuffer = null;
        shazamPlayNextTrack(prevRow, prevKey);
        return;
    }

    shazamNextBuffer = null;

    var liveBtn = buf.trackKey ? shazamFindPlayBtnByTrackKey(buf.trackKey) : null;
    if (!liveBtn || !liveBtn.isConnected) {
        if (buf.proxyId) {
            fetch('/api/shazam-sync/release-proxy', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ proxy_id: buf.proxyId }) }).catch(function () {});
        }
        shazamPlayNextTrack(prevRow, prevKey);
        return;
    }

    releaseShazamProxy();
    var _oldAudioEl = shazamAudioEl;
    if (_oldAudioEl) {
        _oldAudioEl.onended = null;
        _oldAudioEl.onerror = null;
        if (shazamBarEnded) { _oldAudioEl.removeEventListener('ended', shazamBarEnded); shazamBarEnded = null; }
        if (shazamBarTimeUpdate) { _oldAudioEl.removeEventListener('timeupdate', shazamBarTimeUpdate); shazamBarTimeUpdate = null; }
        _oldAudioEl.pause();
        _oldAudioEl.src = '';
    }
    shazamAudioEl = buf.audioEl;

    var playKey = (buf.type === 'soundeo') ? buf.soundeoUrl : buf.streamUrl;
    var resetNewBtn = function () {
        if (liveBtn) { liveBtn.innerHTML = PLAY_ICON_ROW; liveBtn.classList.remove('playing'); }
        shazamCurrentlyPlaying = null;
        shazamPlayingBtn = null;
        shazamPlayerBarHide();
    };

    shazamAudioEl.onerror = function () { resetNewBtn(); };
    shazamAudioEl.onended = function () {
        var np = shazamPlayingBtn && shazamPlayingBtn.isConnected ? shazamPlayingBtn.closest('tr') : null;
        var nk = shazamPlayingBtn ? shazamPlayingBtn.dataset.trackKey : null;
        resetNewBtn();
        shazamPlayFromBuffer(np, nk);
    };

    if (buf.type === 'aiff') {
        shazamCurrentProxyId = buf.proxyId;
    }

    var prevPlaying = document.querySelector('.shazam-play-btn.playing');
    if (prevPlaying && prevPlaying !== liveBtn) {
        prevPlaying.innerHTML = PLAY_ICON_ROW;
        prevPlaying.classList.remove('playing');
    }

    try {
        await shazamAudioEl.play();
        liveBtn.innerHTML = PAUSE_ICON_ROW;
        liveBtn.classList.add('playing');
        shazamCurrentlyPlaying = playKey;
        shazamPlayingBtn = liveBtn;
        shazamPlayerBarShow(liveBtn.dataset.trackLabel || '—');
        shazamPrefetchNext(liveBtn);
    } catch (e) {
        resetNewBtn();
        shazamPlayNextTrack(prevRow, prevKey);
    }
}

function shazamPlayerBarShow(label) {
    const bar = document.getElementById('shazamPlayerBar');
    const labelEl = document.getElementById('shazamBarTrackLabel');
    const playPauseBtn = document.getElementById('shazamBarPlayPause');
    if (!bar || !labelEl || !playPauseBtn) return;
    labelEl.textContent = label || '—';
    bar.style.display = 'flex';
    playPauseBtn.innerHTML = PAUSE_ICON_BAR;
    playPauseBtn.classList.remove('paused');
    if (shazamAudioEl) {
        if (shazamBarTimeUpdate) shazamAudioEl.removeEventListener('timeupdate', shazamBarTimeUpdate);
        if (shazamBarEnded) shazamAudioEl.removeEventListener('ended', shazamBarEnded);
        shazamBarTimeUpdate = function () { shazamPlayerBarUpdateProgress(); };
        shazamBarEnded = function () {
            if (bar.style.display !== 'none') shazamPlayerBarHide();
            shazamCurrentlyPlaying = null;
        };
        shazamAudioEl.addEventListener('timeupdate', shazamBarTimeUpdate);
        shazamAudioEl.addEventListener('ended', shazamBarEnded);
    }
    const playBtn = shazamPlayingBtn;
    if (playBtn) {
        shazamBarKey = (playBtn.getAttribute('data-track-key') || playBtn.dataset.trackKey || '').trim();
        shazamBarSoundeoUrl = (playBtn.getAttribute('data-soundeo-url') || playBtn.dataset.soundeoUrl || '').trim();
        shazamBarSyncMetaFromPlayBtn(playBtn);
    }
    const barCover = document.getElementById('shazamBarCover');
    if (barCover) {
        // Existence from the disk-complete map (any variant); fetch by key so the
        // server resolves the file. Same model as the overview cells — no more
        // hand-rolled variant→hash lookup that could miss the cached form.
        var hasBarCover = false;
        if (shazamBarKey) {
            hasBarCover = !!(shazamCoverHashes[shazamBarKey] || shazamCoverHashes[(shazamBarKey || '').toLowerCase()]);
            if (!hasBarCover) {
                try {
                    var vks = shazamKeyVariants(shazamBarKey);
                    for (var vi = 0; vi < vks.length; vi++) {
                        if (shazamCoverHashes[vks[vi]]) { hasBarCover = true; break; }
                    }
                } catch (e) { /* ignore */ }
            }
        }
        if (hasBarCover) {
            barCover.style.backgroundImage = "url('" + shazamCoverByKeyUrl(shazamBarKey) + "')";
            barCover.classList.remove('shazam-bar-cover-placeholder');
        } else {
            barCover.style.backgroundImage = '';
            barCover.classList.add('shazam-bar-cover-placeholder');
        }
    }
    shazamBarUpdateActions();
    shazamPlayerBarUpdateProgress();
}

function shazamPlayerBarHide() {
    const bar = document.getElementById('shazamPlayerBar');
    if (!bar) return;
    bar.style.display = 'none';
    releaseShazamProxy();
    if (shazamAudioEl && shazamBarTimeUpdate) shazamAudioEl.removeEventListener('timeupdate', shazamBarTimeUpdate);
    if (shazamAudioEl && shazamBarEnded) shazamAudioEl.removeEventListener('ended', shazamBarEnded);
    shazamBarTimeUpdate = null;
    shazamBarEnded = null;
    if (shazamPlayingBtn) { shazamPlayingBtn.innerHTML = PLAY_ICON_ROW; shazamPlayingBtn.classList.remove('playing'); shazamPlayingBtn = null; }
    shazamBarKey = null;
    shazamBarSoundeoUrl = null;
    shazamBarArtist = null;
    shazamBarTitle = null;
    const barCover = document.getElementById('shazamBarCover');
    if (barCover) {
        barCover.style.backgroundImage = '';
        barCover.classList.add('shazam-bar-cover-placeholder');
    }
    shazamBarUpdateActions();
}

function shazamPlayerBarUpdateProgress() {
    if (!shazamAudioEl) return;
    const cur = shazamAudioEl.currentTime;
    const dur = shazamAudioEl.duration;
    const progressEl = document.getElementById('shazamBarProgress');
    const timeEl = document.getElementById('shazamBarTime');
    const durationEl = document.getElementById('shazamBarDuration');
    if (progressEl) progressEl.style.width = (dur && isFinite(dur) ? (cur / dur) * 100 : 0) + '%';
    const fmt = (s) => { const m = Math.floor(s / 60); const sec = Math.floor(s % 60); return m + ':' + (sec < 10 ? '0' : '') + sec; };
    if (timeEl) timeEl.textContent = fmt(isFinite(cur) ? cur : 0);
    if (durationEl) durationEl.textContent = fmt(dur && isFinite(dur) ? dur : 0);
}

function shazamPlayerBarScrub(e) {
    if (!shazamAudioEl || !shazamAudioEl.duration) return;
    const scrub = document.getElementById('shazamBarScrub');
    if (!scrub) return;
    const rect = scrub.getBoundingClientRect();
    const x = (e && e.clientX != null) ? e.clientX - rect.left : 0;
    const pct = Math.max(0, Math.min(1, x / rect.width));
    shazamAudioEl.currentTime = shazamAudioEl.duration * pct;
    shazamPlayerBarUpdateProgress();
}

function shazamPlayerBarPlayPause() {
    if (!shazamAudioEl) return;
    const playPauseBtn = document.getElementById('shazamBarPlayPause');
    if (shazamAudioEl.paused) {
        shazamAudioEl.play();
        if (playPauseBtn) { playPauseBtn.innerHTML = PAUSE_ICON_BAR; playPauseBtn.classList.remove('paused'); }
        if (shazamPlayingBtn) { shazamPlayingBtn.innerHTML = PAUSE_ICON_ROW; shazamPlayingBtn.classList.add('playing'); }
    } else {
        shazamAudioEl.pause();
        if (playPauseBtn) { playPauseBtn.innerHTML = PLAY_ICON_BAR; playPauseBtn.classList.add('paused'); }
        if (shazamPlayingBtn) { shazamPlayingBtn.innerHTML = PLAY_ICON_ROW; shazamPlayingBtn.classList.remove('playing'); }
    }
    shazamNudgeHoverAfterPlaybarUpdate();
}

function shazamPlayerBarClose() {
    shazamCancelNextBuffer();
    if (shazamAudioEl) shazamAudioEl.pause();
    shazamCurrentlyPlaying = null;
    shazamPlayerBarHide();
}

/** Play the next visible track in the list after the given row (or the currently playing row). */
function shazamPlayNextTrack(fromRow, fallbackTrackKey) {
    var currentRow = (fromRow && fromRow.isConnected) ? fromRow : null;
    if (!currentRow && shazamPlayingBtn && shazamPlayingBtn.isConnected) {
        currentRow = shazamPlayingBtn.closest('tr');
    }
    if (!currentRow) {
        var playingEl = document.querySelector('#shazamTrackList .shazam-play-btn.playing');
        if (playingEl) currentRow = playingEl.closest('tr');
    }
    if (!currentRow && fallbackTrackKey) {
        var allRows = document.querySelectorAll('#shazamTrackList tbody tr');
        var foundIdx = -1;
        for (var ri = 0; ri < allRows.length; ri++) {
            if (allRows[ri].dataset.trackKey === fallbackTrackKey) { foundIdx = ri; break; }
        }
        if (foundIdx >= 0 && foundIdx + 1 < allRows.length) {
            currentRow = allRows[foundIdx];
        } else if (allRows.length > 0) {
            var targetIdx = foundIdx >= 0 ? foundIdx : 0;
            var candidate = allRows[Math.min(targetIdx, allRows.length - 1)];
            var btn = candidate.querySelector('.shazam-play-btn');
            if (btn) {
                if (btn.classList.contains('shazam-soundeo-play') && !btn.dataset.dirB64 && !btn.dataset.pathB64) shazamToggleSoundeoPlay(btn);
                else shazamTogglePlay(btn);
            }
            return;
        }
    }
    if (!currentRow) return;
    var nextRow = currentRow.nextElementSibling;
    while (nextRow) {
        var nextBtn = nextRow.querySelector('.shazam-play-btn');
        if (nextBtn) {
            if (nextBtn.classList.contains('shazam-soundeo-play') && !nextBtn.dataset.dirB64 && !nextBtn.dataset.pathB64) {
                shazamToggleSoundeoPlay(nextBtn);
            } else {
                shazamTogglePlay(nextBtn);
            }
            return;
        }
        nextRow = nextRow.nextElementSibling;
    }
}

/** Play the previous visible track in the list before the currently playing row. */
function shazamPlayPrevTrack() {
    var currentRow = null;
    if (shazamPlayingBtn && shazamPlayingBtn.isConnected) {
        currentRow = shazamPlayingBtn.closest('tr');
    }
    if (!currentRow) {
        var playingEl = document.querySelector('#shazamTrackList .shazam-play-btn.playing');
        if (playingEl) currentRow = playingEl.closest('tr');
    }
    if (!currentRow) return;
    var prevRow = currentRow.previousElementSibling;
    while (prevRow) {
        var prevBtn = prevRow.querySelector('.shazam-play-btn');
        if (prevBtn) {
            if (prevBtn.classList.contains('shazam-soundeo-play') && !prevBtn.dataset.dirB64 && !prevBtn.dataset.pathB64) {
                shazamToggleSoundeoPlay(prevBtn);
            } else {
                shazamTogglePlay(prevBtn);
            }
            return;
        }
        prevRow = prevRow.previousElementSibling;
    }
}

function shazamPlayerBarNext() {
    var prevRow = shazamPlayingBtn && shazamPlayingBtn.isConnected ? shazamPlayingBtn.closest('tr') : null;
    var prevKey = shazamPlayingBtn ? shazamPlayingBtn.dataset.trackKey : null;
    shazamCancelNextBuffer();
    if (shazamAudioEl) shazamAudioEl.pause();
    shazamCurrentlyPlaying = null;
    shazamPlayerBarHide();
    shazamPlayNextTrack(prevRow, prevKey);
}

function shazamPlayerBarPrev() {
    shazamCancelNextBuffer();
    if (shazamAudioEl) shazamAudioEl.pause();
    shazamCurrentlyPlaying = null;
    shazamPlayerBarHide();
    shazamPlayPrevTrack();
}

/** White download-arrow icon for “have locally” on solid black button (sync table + playbar). */
function shazamSvgDownloadHaveWhite(size) {
    var n = size != null ? size : 16;
    var sw = n <= 16 ? '2.65' : '2.35';
    return (
        '<svg class="shazam-download-have-icon" width="' + n + '" height="' + n + '" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="' + sw + '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>' +
        '</svg>'
    );
}

/** Update the player bar action buttons (star / download / skip) to match current track state. */
function shazamBarUpdateActions() {
    var actionsEl = document.getElementById('shazamBarActions');
    if (!actionsEl) return;
    if (shazamCurrentlyPlaying && shazamAudioEl) {
        shazamSyncBarMetaFromPlayingButton();
    }
    actionsEl.style.display = shazamBarKey ? '' : 'none';
    var starBtn = document.getElementById('shazamBarStarBtn');
    var dlBtn = document.getElementById('shazamBarDownloadBtn');
    var skipBtn = document.getElementById('shazamBarSkipBtn');
    if (!starBtn || !dlBtn || !skipBtn) return;

    var key = shazamBarKey || '';
    var url = shazamBarSoundeoUrl || '';
    var keyVariants = key ? shazamKeyVariants(key) : [];
    var starred = keyVariants.some(function (k) { return shazamStarred[k]; });
    var dismissed = keyVariants.some(function (k) { return shazamDismissed[k]; });
    var starBusy = false;
    if (key) {
        starBusy = keyVariants.some(function (k) { return shazamActionPending[k]; });
        if (!starBusy) {
            Object.keys(shazamActionPending || {}).forEach(function (pk) {
                if (shazamActionPending[pk] && shazamTrackKeyMatches(pk, key)) starBusy = true;
            });
        }
    }

    var starFilledSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';
    var starOutlineSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';
    starBtn.classList.remove('shazam-bar-star-pending');
    starBtn.removeAttribute('aria-busy');
    if (starBusy) {
        var starBusyLabel = starred && !dismissed ? 'Unstarring' : 'Starring';
        starBtn.innerHTML = '<span class="shazam-btn-spinner shazam-bar-star-spinner" title="' + starBusyLabel + '…" role="status" aria-label="' + starBusyLabel + '"></span>';
        starBtn.disabled = true;
        starBtn.title = (starred && !dismissed) ? 'Removing from favorites…' : 'Adding to favorites…';
        starBtn.setAttribute('aria-busy', 'true');
        starBtn.classList.add('shazam-bar-star-pending');
        starBtn.classList.remove('shazam-bar-action-active');
    } else if (starred && !dismissed) {
        starBtn.innerHTML = starFilledSvg;
        starBtn.title = 'Remove from Soundeo favorites (unstar)';
        starBtn.classList.add('shazam-bar-action-active');
    } else {
        starBtn.innerHTML = starOutlineSvg;
        starBtn.title = url ? 'Add to Soundeo favorites' : 'Find link first (Search)';
        starBtn.classList.remove('shazam-bar-action-active');
    }
    if (!starBusy) {
        starBtn.disabled = !url || dismissed;
    }

    var isLocalFile = !!(shazamPlayingBtn && shazamPlayingBtn.isConnected && (shazamPlayingBtn.dataset.dirB64 || shazamPlayingBtn.dataset.pathB64));
    var inHaveList = key ? shazamTrackKeyInHaveLocally(key) : false;
    var haveFileUi = isLocalFile || inHaveList;
    var downloadOutlineSvg = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#111111" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    var downloadWorkerBusy = !!(key && shazamDownloadProgressSnapshot.running && shazamTrackKeyMatches(shazamDownloadProgressSnapshot.current_key, key));
    var downloadBusy = !!(key && shazamPendingDownload[key]) || downloadWorkerBusy;
    dlBtn.classList.remove('shazam-bar-action-active', 'shazam-bar-dl-have', 'shazam-bar-dl-pending');
    if (downloadBusy) {
        dlBtn.innerHTML = '<span class="shazam-btn-spinner shazam-bar-dl-spinner" title="Downloading…" role="status" aria-label="Downloading"></span>';
        dlBtn.disabled = true;
        dlBtn.title = 'Downloading…';
        dlBtn.setAttribute('aria-busy', 'true');
        dlBtn.classList.add('shazam-bar-dl-pending');
    } else if (haveFileUi) {
        dlBtn.innerHTML = shazamSvgDownloadHaveWhite(16);
        dlBtn.disabled = false;
        dlBtn.title = 'Have locally — click: Download again or Show locally';
        dlBtn.removeAttribute('aria-busy');
        dlBtn.classList.add('shazam-bar-dl-have');
    } else {
        dlBtn.innerHTML = downloadOutlineSvg;
        dlBtn.disabled = !url;
        dlBtn.title = url ? 'Download AIFF' : 'No Soundeo link';
        dlBtn.removeAttribute('aria-busy');
    }

    // Maybe button — local-only flag, mirrors the per-row Maybe icon.
    var maybeBtn = document.getElementById('shazamBarMaybeBtn');
    if (maybeBtn) {
        var maybeOn = keyVariants.some(function (k) { return shazamMaybe[k]; });
        var maybeFilledSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="12" cy="12" r="9"/><text x="12" y="17" text-anchor="middle" font-size="14" font-weight="700" fill="#fff" font-family="-apple-system,sans-serif">?</text></svg>';
        var maybeOutlineSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><text x="12" y="17" text-anchor="middle" font-size="14" font-weight="700" stroke="none" fill="currentColor" font-family="-apple-system,sans-serif">?</text></svg>';
        maybeBtn.innerHTML = maybeOn ? maybeFilledSvg : maybeOutlineSvg;
        maybeBtn.title = maybeOn ? 'Maybe — clear (currently set: heard, undecided)' : 'Maybe — listened, undecided (local only, not on Soundeo)';
        maybeBtn.classList.toggle('shazam-bar-action-active', maybeOn);
        maybeBtn.disabled = !key;
    }

    var skipSvg = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="6" y2="18"/><line x1="10" y1="6" x2="10" y2="18"/><polygon points="14 8 14 16 20 12"/></svg>';
    skipBtn.innerHTML = skipSvg;
    skipBtn.title = 'Skip track';
    var sa = (shazamBarArtist || '').trim();
    var st = (shazamBarTitle || '').trim();
    var skipEnabled = !!(sa || st);
    if (!skipEnabled && key) {
        skipEnabled = key.indexOf(' - ') !== -1;
    }
    skipBtn.disabled = !skipEnabled;
    shazamNudgeHoverAfterPlaybarUpdate();
}

function shazamBarToggleStar() {
    if (shazamCurrentlyPlaying && shazamAudioEl) shazamSyncBarMetaFromPlayingButton();
    if (!shazamBarKey) return;
    var keyVariants = shazamKeyVariants(shazamBarKey);
    var starred = keyVariants.some(function (k) { return shazamStarred[k]; });
    var dismissed = keyVariants.some(function (k) { return shazamDismissed[k]; });
    if (starred && !dismissed) {
        shazamUnstarTrack(shazamBarKey, shazamBarSoundeoUrl, shazamBarArtist, shazamBarTitle);
    } else {
        shazamStarTrack(shazamBarKey, shazamBarSoundeoUrl, shazamBarArtist, shazamBarTitle);
    }
}

/** Playbar Maybe toggle — local-only flag, mirrors the per-row Maybe icon.
 *  Synthesizes a fake button with data-key/data-value and routes through the
 *  existing shazamToggleMaybe() so the optimistic-update + revert logic is shared. */
function shazamBarToggleMaybe() {
    if (shazamCurrentlyPlaying && shazamAudioEl) shazamSyncBarMetaFromPlayingButton();
    if (!shazamBarKey) return;
    var keyVariants = shazamKeyVariants(shazamBarKey);
    var currentlyMaybe = keyVariants.some(function (k) { return shazamMaybe[k]; });
    var fakeBtn = {
        dataset: {
            key: shazamBarKey,
            value: currentlyMaybe ? '0' : '1',
            artist: shazamBarArtist || '',
            title: shazamBarTitle || '',
        },
    };
    shazamToggleMaybe(fakeBtn);
    // Optimistic visual update so the playbar button flips immediately.
    shazamBarUpdateActions();
}

function shazamBarDownload(ev) {
    if (shazamCurrentlyPlaying && shazamAudioEl) shazamSyncBarMetaFromPlayingButton();
    if (!shazamBarKey) return;
    var dlBtn = document.getElementById('shazamBarDownloadBtn');
    var isHave = dlBtn && dlBtn.classList.contains('shazam-bar-dl-have');
    if (isHave) {
        var pathB64 = '';
        if (shazamPlayingBtn && shazamPlayingBtn.dataset && (shazamPlayingBtn.dataset.pathB64 || '').trim()) {
            pathB64 = (shazamPlayingBtn.dataset.pathB64 || '').trim();
        }
        if (!pathB64) pathB64 = shazamPathB64ForHaveKey(shazamBarKey);
        var posEv = ev;
        if (!posEv || typeof posEv.clientX !== 'number') {
            var r = dlBtn.getBoundingClientRect();
            posEv = { clientX: r.left + r.width / 2, clientY: r.bottom + 6 };
        }
        shazamDownloadHaveMenuAt(posEv, shazamBarKey, pathB64);
        return;
    }
    if (!shazamBarSoundeoUrl) return;
    shazamDownloadTrack(shazamBarKey);
}

function shazamBarSkip() {
    if (shazamPlayingBtn && shazamPlayingBtn.isConnected) {
        shazamBarSyncMetaFromPlayBtn(shazamPlayingBtn);
    }
    var a = (shazamBarArtist || '').trim();
    var t = (shazamBarTitle || '').trim();
    if (!a && !t && shazamBarKey) {
        var sep = ' - ';
        var ix = String(shazamBarKey).indexOf(sep);
        if (ix !== -1) {
            a = String(shazamBarKey).slice(0, ix).trim();
            t = String(shazamBarKey).slice(ix + sep.length).trim();
        }
    }
    if (!a && !t) return;
    var prevRow = null;
    if (shazamPlayingBtn && shazamPlayingBtn.isConnected) {
        prevRow = shazamPlayingBtn.closest('tr');
    }
    if (!prevRow) {
        var playingEl = document.querySelector('#shazamTrackList .shazam-play-btn.playing');
        if (playingEl) prevRow = playingEl.closest('tr');
    }
    var playNextKey = null;
    if (prevRow) {
        var nr = prevRow.nextElementSibling;
        while (nr) {
            var nb = nr.querySelector('.shazam-play-btn');
            if (nb) {
                playNextKey = (nb.getAttribute('data-track-key') || nb.dataset.trackKey || '').trim() || null;
                break;
            }
            nr = nr.nextElementSibling;
        }
    }
    shazamSkipSingleTrack(a, t, { fromPlaybar: true, playNextKey: playNextKey });
}

function shazamApplyFilters(merged) {
    const now = Math.floor(Date.now() / 1000);
    const oneMonth = 30 * 86400, twoMonths = 60 * 86400, threeMonths = 91 * 86400;
    let out = merged;
    if (shazamFilterTime !== 'all') {
        const sec = shazamFilterTime === '1_month' ? oneMonth : shazamFilterTime === '2_months' ? twoMonths : threeMonths;
        const cutoff = now - sec;
        out = out.filter(t => (t.shazamed_at ?? 0) >= cutoff);
    }
    if (shazamFilterStatus !== 'all') {
        // Helper: produce all key variants the server-side variant-write produces,
        // so the membership lookup in shazamStarred / shazamMaybe / shazamNotFound
        // hits regardless of which form a track was stored under.
        const _kvars = (artist, title) => {
            const key = `${artist || ''} - ${title || ''}`;
            const keyLower = key.toLowerCase();
            const keyNorm = key.indexOf(' (') !== -1 ? key.substring(0, key.indexOf(' (')).trim() : key;
            const keyNormLower = keyNorm.toLowerCase();
            let keyDeep = keyNormLower.replace(/ & /g, ', ');
            const d = keyDeep.indexOf(' - ');
            if (d !== -1) {
                const arts = keyDeep.substring(0, d).split(', ').map(a => a.trim()).filter(Boolean).sort().join(', ');
                keyDeep = arts + ' - ' + keyDeep.substring(d + 3);
            }
            return [key, keyLower, keyNorm, keyNormLower, keyDeep];
        };
        const _luTruthy = (map, vars) => { for (const k of vars) { if (map[k]) return true; } return false; };

        if (shazamFilterStatus === 'shortlist') {
            // Shortlist = Available (todl) AND starred. Subset of the funnel
            // between "found" and "downloaded".
            out = out.filter(t => {
                if (t.status !== 'todl') return false;
                return _luTruthy(shazamStarred, _kvars(t.artist, t.title));
            });
        } else if (shazamFilterStatus === 'maybe') {
            // Maybe = Available (todl) AND maybe flag set (local-only, never on Soundeo).
            out = out.filter(t => {
                if (t.status !== 'todl') return false;
                return _luTruthy(shazamMaybe, _kvars(t.artist, t.title));
            });
        } else {
            out = out.filter(t => t.status === shazamFilterStatus);
            // Available tab (todl): hide tracks searched and "not found on Soundeo" (not actionable for download).
            if (shazamFilterStatus === 'todl') {
                out = out.filter(t => !_luTruthy(shazamNotFound, _kvars(t.artist, t.title)));
            }
        }
    }
    const search = (shazamFilterSearch || '').trim().toLowerCase();
    if (search) {
        out = out.filter(t => {
            const artist = (t.artist || '').toLowerCase();
            const title = (t.title || '').toLowerCase();
            return artist.includes(search) || title.includes(search);
        });
    }
    return out;
}

/** Clears inline pointer-events from hover “nudge” helpers — safe to call anytime (idempotent). */
function shazamClearPointerEventNudgeResiduals() {
    var wrap = document.getElementById('shazamTrackList');
    if (wrap) wrap.style.pointerEvents = '';
    var bar = document.getElementById('shazamPlayerBar');
    if (bar) {
        var ch = bar.children;
        for (var i = 0; i < ch.length; i++) {
            ch[i].style.pointerEvents = '';
        }
    }
}

var shazamPointerNudgeSafetyTimer = null;
/** If rAF is delayed (background tab) or a callback throws, none can stick — clear shortly after last nudge. */
function shazamSchedulePointerNudgeSafetyClear() {
    if (shazamPointerNudgeSafetyTimer) clearTimeout(shazamPointerNudgeSafetyTimer);
    shazamPointerNudgeSafetyTimer = setTimeout(function () {
        shazamPointerNudgeSafetyTimer = null;
        shazamClearPointerEventNudgeResiduals();
    }, 400);
}

/**
 * Full table innerHTML replaces nodes under the cursor; Chromium often keeps a stale :hover chain
 * until the pointer moves. Briefly toggling pointer-events on the list wrapper forces a hit-test
 * so action buttons show rollover again while another row is busy (download/search/etc.).
 */
function shazamNudgeHoverAfterTrackTableReplace() {
    var wrap = document.getElementById('shazamTrackList');
    if (!wrap) return;
    wrap.style.pointerEvents = 'none';
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            shazamClearPointerEventNudgeResiduals();
            shazamSchedulePointerNudgeSafetyClear();
        });
    });
}

/** Same hit-test refresh as the track table, for the fixed player bar after action/play DOM updates. */
function shazamNudgeHoverAfterPlaybarUpdate() {
    var bar = document.getElementById('shazamPlayerBar');
    if (!bar || bar.style.display === 'none') return;
    var actions = document.getElementById('shazamBarActions');
    var kids = bar.children;
    var toRestore = [];
    for (var i = 0; i < kids.length; i++) {
        var el = kids[i];
        if (el === actions) continue;
        toRestore.push(el);
        el.style.pointerEvents = 'none';
    }
    if (!toRestore.length) return;
    requestAnimationFrame(function () {
        requestAnimationFrame(function () {
            shazamClearPointerEventNudgeResiduals();
            shazamSchedulePointerNudgeSafetyClear();
        });
    });
}

function shazamRenderTrackList(data) {
    const progressCaptured = shazamCaptureSyncProgress();
    if (!data) data = {};
    shazamLastData = data;
    if (data.urls) Object.assign(shazamTrackUrls, data.urls);
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.dismissed) Object.assign(shazamDismissed, data.dismissed);
    if (data.dismissed_manual_check && Array.isArray(data.dismissed_manual_check)) {
        shazamDismissedManualCheck = {};
        data.dismissed_manual_check.forEach(k => { shazamDismissedManualCheck[k] = true; });
    }
    if (data.soundeo_titles && typeof data.soundeo_titles === 'object') {
        Object.assign(shazamSoundeoTitles, data.soundeo_titles);
    }
    // Merge — see longer comment above; same race-safety reason.
    if (data.maybe && typeof data.maybe === 'object') {
        Object.assign(shazamMaybe, data.maybe);
    }
    if (data.listened && typeof data.listened === 'object') {
        Object.assign(shazamListened, data.listened);
    }
    if (data.cover_hashes && typeof data.cover_hashes === 'object') {
        shazamMergeCoverHashes(data.cover_hashes);
    }
    const have = (data.have_locally || []).map(t => ({ ...t, status: 'have' }));
    const toDl = (data.to_download || []).map((t, i) => ({ ...t, status: 'todl', _idx: i }));
    const skipped = (data.skipped_tracks || []).map(t => ({ ...t, status: 'skipped' }));
    shazamToDownloadTracks = data.to_download || [];
    const el = document.getElementById('shazamTrackList');
    const selectionBar = document.getElementById('shazamSelectionBar');
    if (!el) {
        shazamRestoreSyncProgress(progressCaptured);
        return;
    }
    let html = '';
    if (data.compare_running) {
        const sp = data.scan_progress;
        const msg = (sp && sp.total > 0)
            ? `Scanning: ${(sp.current || 0).toLocaleString()} / ${sp.total.toLocaleString()}`
            : (data.message || 'Comparing local folders...');
        html += `<p class="shazam-info-msg">${escapeHtml(msg)}</p>`;
    }
    if (data.error) {
        html += `<p class="shazam-info-msg shazam-warning">${escapeHtml(data.error)}</p>`;
    }
    if (data.message && !data.compare_running && !data.error) {
        html += `<p class="shazam-info-msg">${escapeHtml(data.message)}</p>`;
    }
    if (have.length === 0 && toDl.length === 0 && skipped.length === 0) {
        if (!data.error) {
            html += '<p class="shazam-info-msg">Click <strong>Fetch Shazam</strong> to load tracks, add destination folders in Settings, then <strong>Compare</strong>.</p>';
        }
        el.innerHTML = html || '<p class="shazam-info-msg">Run Compare to see tracks.</p>';
        if (selectionBar) selectionBar.style.display = 'none';
        shazamBarUpdateActions();
        shazamRestoreSyncProgress(progressCaptured);
        return;
    }
    const merged = [...have, ...toDl, ...skipped];
    merged.sort((a, b) => { const sa = a.shazamed_at ?? 0; const sb = b.shazamed_at ?? 0; return sb - sa; });
    const filtered = shazamApplyFilters(merged);
    const hasTodl = filtered.some(r => r.status === 'todl');
    const hasSkipped = filtered.some(r => r.status === 'skipped');
    html += '<table class="shazam-track-table"><thead><tr><th></th><th class="shazam-cover-col"></th><th>When</th><th>Artist</th><th>Title</th><th class="shazam-match-col">Match</th>';
    html += '<th></th><th>Actions</th>';
    html += '<th class="shazam-select-col">' + (hasTodl ? '<input type="checkbox" id="shazamSelectAll" onchange="shazamToggleSelectAll(this)" title="Select all" />' : '<span aria-hidden="true" style="display:inline-block;width:18px;height:18px;"></span>') + '</th>';
    html += '</tr></thead><tbody>';
    filtered.forEach((row, i) => {
        const when = shazamFormatRelativeTime(row.shazamed_at);
        const isTodl = row.status === 'todl';
        const isSkipped = row.status === 'skipped';
        const idx = row._idx;
        const key = `${row.artist} - ${row.title}`;
        const keyLower = key.toLowerCase();
        const keyNorm = key.indexOf(' (') !== -1 ? key.substring(0, key.indexOf(' (')).trim() : key;
        const keyNormLower = keyNorm.toLowerCase();
        const keyDeep = (() => { let s = keyNormLower.replace(/ & /g, ', '); const d = s.indexOf(' - '); if (d !== -1) { const arts = s.substring(0, d).split(', ').map(a => a.trim()).filter(Boolean).sort().join(', '); s = arts + ' - ' + s.substring(d + 3); } return s; })();
        const _lu = (map, ...keys) => { for (const k of keys) { const v = map[k]; if (v) return v; } return undefined; };
        const prog = (shazamCurrentProgress && (shazamCurrentProgress.mode === 'search_global' || shazamCurrentProgress.mode === 'search_single'))
            ? shazamCurrentProgress
            : null;
        const url = _lu(shazamTrackUrls, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.urls || {}, key, keyLower, keyNorm, keyNormLower, keyDeep) || (prog && _lu(prog.urls || {}, key, keyLower)) || null;
        const soundeoTitle = _lu(shazamSoundeoTitles, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.soundeo_titles || {}, key, keyLower, keyNorm, keyNormLower, keyDeep) || (prog && _lu(prog.soundeo_titles || {}, key, keyLower)) || null;
        // Starred only from explicit Soundeo state. Prefer live shazamStarred when key exists (even if false) so unstar updates UI.
        const hasLiveExact = (key in shazamStarred) || (keyLower in shazamStarred);
        const starredForExact = hasLiveExact
            ? !!(shazamStarred[key] || shazamStarred[keyLower])
            : (_lu(shazamStarred, key, keyLower) || _lu(data.starred || {}, key, keyLower));
        const hasLiveAlias = (keyNorm in shazamStarred) || (keyNormLower in shazamStarred) || (keyDeep in shazamStarred);
        const starredFromAlias = hasLiveAlias
            ? !!(shazamStarred[keyNorm] || shazamStarred[keyNormLower] || shazamStarred[keyDeep])
            : (_lu(shazamStarred, keyNorm, keyNormLower, keyDeep) || _lu(data.starred || {}, keyNorm, keyNormLower, keyDeep));
        const starred = !!(row.status === 'have' ? starredForExact : (starredForExact || starredFromAlias));
        // Local-only personal-curation flags (NEVER affect Soundeo). Variant-aware lookup.
        const isMaybe = !!_lu(shazamMaybe, key, keyLower, keyNorm, keyNormLower, keyDeep);
        const isListened = !!_lu(shazamListened, key, keyLower, keyNorm, keyNormLower, keyDeep);
        // Multi-shazam flag — track was Shazammed N times across all syncs (cumulative). Show ≥ 2.
        const shazamedCount = (typeof row.shazamed_count === 'number' && row.shazamed_count >= 2) ? row.shazamed_count : 0;
        // Same key variants as shazamSetNotFoundLive so dot colour (orange vs grey) stays in sync
        const isSearchedNotFound = !!(_lu(shazamNotFound, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.not_found || {}, key, keyLower, keyNorm, keyNormLower, keyDeep) || (prog && _lu(prog.not_found || {}, key, keyLower)));
        const soundeoScoreMap = data.soundeo_match_scores || {};
        const soundeoMatchScore = _lu(soundeoScoreMap, key, keyLower, keyNorm, keyNormLower, keyDeep) || (prog && _lu(prog.soundeo_match_scores || {}, key, keyLower, keyNorm, keyNormLower, keyDeep)) || null;
        const score = row.match_score != null ? row.match_score : null;
        const isSynced = !!url;
        // Use same key variants as shazamSetDismissedLive so dot state (colour) and row stay in sync
        const isDismissed = !!(_lu(shazamDismissed, key, keyLower, keyNorm, keyNormLower, keyDeep));
        const manualCheckDismissed = !!shazamDismissedManualCheck[key];
        const isNonExtendedVersion = soundeoTitle && /\((original\s+mix|radio\s+edit|radio\s+version|short\s+version)\)/i.test(soundeoTitle.trim()) && !/extended/i.test(soundeoTitle.trim());
        const showManualCheck = isTodl && !isDismissed && isSynced && !manualCheckDismissed && isNonExtendedVersion;
        const isPending = shazamRowActionPending(key);
        const starOnlyPending = !!shazamActionPending[key];
        // Queue position for row: star queue has key; search queue has artist+title
        const starQueueIdx = (shazamCurrentStarQueue || []).findIndex(function (q) {
            var qk = (q.key || (q.artist + ' - ' + q.title)).trim();
            return qk === key || qk.toLowerCase() === keyLower;
        });
        const searchQueueIdx = (shazamCurrentSearchQueue || []).findIndex(function (q) {
            return (q.artist || '').trim() === row.artist && (q.title || '').trim() === row.title;
        });
        const unstarQueueIdx = (shazamCurrentUnstarQueue || []).findIndex(function (q) {
            var qk = (q.key || (q.artist + ' - ' + q.title)).trim();
            return qk === key || qk.toLowerCase() === keyLower;
        });
        const downloadQueueList = shazamCurrentDownloadQueue || [];
        const downloadQueueIdx = downloadQueueList.findIndex(function (k) {
            return k === key || (k || '').toLowerCase() === keyLower;
        });
        const inStarQueue = starQueueIdx >= 0;
        const inSearchQueue = searchQueueIdx >= 0;
        const inUnstarQueue = unstarQueueIdx >= 0;
        const inDownloadQueue = downloadQueueIdx >= 0;
        const starQueuePos = inStarQueue ? starQueueIdx + 1 : 0;
        const starQueueTotal = (shazamCurrentStarQueue || []).length;
        const searchQueuePos = inSearchQueue ? searchQueueIdx + 1 : 0;
        const searchQueueTotal = (shazamCurrentSearchQueue || []).length;
        const unstarQueuePos = inUnstarQueue ? unstarQueueIdx + 1 : 0;
        const unstarQueueTotal = (shazamCurrentUnstarQueue || []).length;
        const downloadQueuePos = inDownloadQueue ? downloadQueueIdx + 1 : 0;
        const downloadQueueTotal = downloadQueueList.length;
        const inAnyQueue = inStarQueue || inSearchQueue || inUnstarQueue || inDownloadQueue;
        const escapedKey = escapeHtml(key);
        const escapedArtist = escapeHtml(row.artist);
        const escapedTitle = escapeHtml(row.title);
        const currentKey = shazamCurrentProgress.current_key;
        const isCurrentTrack = !!(shazamCurrentProgress.running && currentKey && (currentKey === key || currentKey.toLowerCase() === keyLower));

        // Dot colours: have+starred = green, have+not starred = teal; both live-update when star status changes (shazamSetStarredLive + re-render)
        // Spinner at start of row when this track is being processed (sync current_key, row pending, or active download worker)
        const downloadWorkerActive = !!(shazamDownloadProgressSnapshot.running && shazamTrackKeyMatches(shazamDownloadProgressSnapshot.current_key, key));
        const showRowSpinner = isCurrentTrack || isPending || downloadWorkerActive;
        let statusCell = '';
        if (showRowSpinner) {
            statusCell = '<td class="status-cell"><span class="status-spinner" title="Processing…"></span></td>';
        } else if (isDismissed) {
            statusCell = '<td class="status-cell"><span class="status-dot status-dismissed" title="Dismissed">\u00d7</span></td>';
        } else if (row.status === 'skipped') {
            statusCell = '<td class="status-cell"><span class="status-dot status-skipped" title="Skipped">\u2014</span></td>';
        } else if (row.status === 'have' && starred) {
            statusCell = '<td class="status-cell"><span class="status-dot status-have-starred" title="Have locally, starred"></span></td>';
        } else if (row.status === 'have' && !starred) {
            statusCell = '<td class="status-cell"><span class="status-dot status-have" title="Have locally (not starred)"></span></td>';
        } else if (row.status === 'todl' && !url) {
            if (isSearchedNotFound) {
                statusCell = '<td class="status-cell"><span class="status-dot status-not-found" title="Searched, not found on Soundeo"></span></td>';
            } else {
                statusCell = '<td class="status-cell"><span class="status-dot status-no-link" title="No Soundeo link (search not run or no record)"></span></td>';
            }
        } else if (row.status === 'todl' && url && starred) {
            statusCell = '<td class="status-cell"><span class="status-dot status-starred" title="Starred on Soundeo"></span></td>';
        } else {
            statusCell = '<td class="status-cell"><span class="status-dot status-found" title="Found on Soundeo"></span></td>';
        }

        const starInactive = isDismissed || isSkipped || (isTodl && !url);
        const starTitle = starInactive ? (isDismissed ? 'Dismissed' : isSkipped ? 'Skipped' : 'Not on Soundeo') : (starred ? 'In Soundeo favorites' : 'Not in favorites');
        const starFilled = starred && !isDismissed;

        let matchCell = '';
        if (isSkipped || isDismissed) {
            matchCell = '<td class="shazam-match-col">\u2014</td>';
        } else if (isTodl) {
            const manualIcon = showManualCheck
                ? '<span class="manual-check-icon" title="Soundeo link is Original Mix / Radio Edit \u2013 check for Extended" data-track-key="' + escapedKey + '" onclick="shazamDismissManualCheck(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg></span>'
                : '';
            var _rawSc = soundeoMatchScore != null ? Number(soundeoMatchScore) : NaN;
            const sPct = soundeoMatchScore != null && !Number.isNaN(_rawSc)
                ? Math.min(100, Math.round(_rawSc > 1 ? _rawSc : _rawSc * 100))
                : null;
            const scoreHtml = sPct != null ? '<span class="shazam-match-pct">' + sPct + '%</span>' : '';
            matchCell = '<td class="shazam-match-col">' + (scoreHtml || manualIcon || '\u2014') + '</td>';
        } else {
            var _rawHave = score != null ? Number(score) : NaN;
            const pct = score != null && !Number.isNaN(_rawHave)
                ? Math.min(100, Math.round(_rawHave > 1 ? _rawHave : _rawHave * 100))
                : null;
            matchCell = '<td class="shazam-match-col">' + (pct != null ? '<span class="shazam-match-pct">' + pct + '%</span>' : '\u2014') + '</td>';
        }

        // Cover thumbnail (overview list).
        //   Existence gate: the cover_hashes map from /status is recomputed from
        //   the cover_cache/ directory on every poll, so it is never sparse — a
        //   hit here reliably means a file exists on disk.
        //   Fetch: by track KEY (not a pre-resolved hash). The server resolves
        //   the key to a file via its canonical variant set, so the thumbnail
        //   shows whenever the file exists, regardless of which key-variant it
        //   was cached under. This removes the frontend's dependency on the map
        //   holding the exact hash under the exact key form this row uses — the
        //   root cause of the recurring "blank cover, file present" bug.
        const hasCover = !!_lu(shazamCoverHashes, key, keyLower, keyNorm, keyNormLower, keyDeep);
        const coverCell = hasCover
            ? '<td class="shazam-cover-col"><span class="track-cover" style="background-image:url(' + shazamCoverByKeyUrl(key) + ');" aria-hidden="true"></span></td>'
            : '<td class="shazam-cover-col"><span class="track-cover track-cover-placeholder" aria-hidden="true"></span></td>';

        const safeAttr = s => escapeHtml(s).replace(/'/g, '&#39;');
        const trackLabel = (soundeoTitle || key).replace(/"/g, '&quot;');
        const playMetaAttrs = ` data-track-key="${safeAttr(key)}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}"`;
        let playCell = '';
        if (row.filepath) {
            const pathNorm = String(row.filepath).replace(/\\/g, '/');
            const lastSlash = pathNorm.lastIndexOf('/');
            const dir = lastSlash >= 0 ? pathNorm.substring(0, lastSlash) : '';
            const file = lastSlash >= 0 ? pathNorm.substring(lastSlash + 1) : pathNorm;
            const dirB64 = dir ? btoa(unescape(encodeURIComponent(dir))) : '';
            const pathB64 = pathNorm ? btoa(unescape(encodeURIComponent(pathNorm))) : '';
            const localFile = file || pathNorm;
            const soundeoUrlAttr = url ? ` data-soundeo-url="${escapeHtml(url)}"` : '';
            playCell = `<td class="shazam-play-col"><button type="button" class="shazam-play-btn"${playMetaAttrs} data-dir-b64="${escapeHtml(dirB64)}" data-file="${escapeHtml(file)}" data-path-b64="${escapeHtml(pathB64)}" data-track-label="${escapeHtml(trackLabel)}"${soundeoUrlAttr} onclick="shazamTogglePlay(this)" oncontextmenu="event.preventDefault(); shazamPlayContextMenu(event, this);" title="Play local file: ${escapeHtml(localFile)}">${PLAY_ICON_ROW}</button></td>`;
        } else if (url) {
            const previewTip = soundeoTitle ? `Stream Soundeo preview: ${escapeHtml(soundeoTitle)}` : 'Stream Soundeo preview';
            playCell = `<td class="shazam-play-col"><button type="button" class="shazam-play-btn shazam-soundeo-play"${playMetaAttrs} data-soundeo-url="${escapeHtml(url)}" data-track-label="${escapeHtml(trackLabel)}" onclick="shazamToggleSoundeoPlay(this)" oncontextmenu="event.preventDefault(); shazamPlayContextMenu(event, this);" title="${previewTip}">${PLAY_ICON_ROW}</button></td>`;
        } else {
            playCell = '<td class="shazam-play-col"></td>';
        }

        const inactive = ' shazam-row-action-inactive';
        const searchInactive = isDismissed || isSkipped ? inactive : '';
        const skipInactive = isDismissed || !isTodl ? inactive : '';
        // Single star/unstar: unstarred (or dismissed) → star outline (star or undismiss); starred → filled star → unstar only (no dismiss)
        const starToggleAction = isDismissed ? 'undismiss' : (starred ? 'unstar' : 'star');
        const starToggleInactive = (starToggleAction === 'star' && !isSynced) ? inactive : '';
        const starToggleTitle = isDismissed ? 'Undo dismiss (re-star on Soundeo)' : (starred ? 'Remove from Soundeo favorites (unstar)' : (!isSynced ? 'Find link first (Search)' : 'Add to Soundeo favorites'));
        const starToggleSvg = (starred && !isDismissed) ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>' : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';
        const starToggleDataAttrs = (starToggleAction === 'star') ? ` data-track-url="${safeAttr(url || '')}"` : ` data-url="${safeAttr(url || '')}"`;
        const starBtnContent = starOnlyPending ? '<span class="shazam-btn-spinner" title="Processing…"></span>' : starToggleSvg;
        const starBtnDisabled = starOnlyPending ? ' disabled' : '';

        let actionsCell = '<td class="shazam-actions-col">';
        if (inAnyQueue) {
            var parts = [];
            if (inStarQueue) parts.push(starQueuePos + '/' + starQueueTotal);
            if (inSearchQueue) parts.push(searchQueuePos + '/' + searchQueueTotal);
            if (inUnstarQueue) parts.push(unstarQueuePos + '/' + unstarQueueTotal);
            if (inDownloadQueue) parts.push(downloadQueuePos + '/' + downloadQueueTotal);
            var queueShort = parts.length > 1 ? parts.join(' ') : (inStarQueue ? ('★ ' + starQueuePos + '/' + starQueueTotal) : (inSearchQueue ? ('⌕ ' + searchQueuePos + '/' + searchQueueTotal) : (inUnstarQueue ? ('☆ ' + unstarQueuePos + '/' + unstarQueueTotal) : ('↓ ' + downloadQueuePos + '/' + downloadQueueTotal))));
            var titleParts = [];
            if (inStarQueue) titleParts.push('Star ' + starQueuePos + '/' + starQueueTotal);
            if (inSearchQueue) titleParts.push('Search ' + searchQueuePos + '/' + searchQueueTotal);
            if (inUnstarQueue) titleParts.push('Unstar ' + unstarQueuePos + '/' + unstarQueueTotal);
            if (inDownloadQueue) titleParts.push('Download ' + downloadQueuePos + '/' + downloadQueueTotal);
            var queueTitle = titleParts.join(', ');
            var downloadActiveOnRow = inDownloadQueue && shazamDownloadProgressSnapshot.running && shazamTrackKeyMatches(shazamDownloadProgressSnapshot.current_key, key);
            actionsCell += '<span class="shazam-queue-replacement" title="' + escapeHtml(queueTitle) + '">' + (downloadActiveOnRow ? '<span class="shazam-btn-spinner shazam-queue-inline-spinner" title="Downloading…"></span>' : '') + '<span class="shazam-queue-label">' + escapeHtml(queueShort) + '</span>';
            if (inStarQueue) {
                actionsCell += '<button type="button" class="shazam-row-action-btn shazam-remove-queue" data-queue="star" data-key="' + safeAttr(key) + '" data-artist="' + safeAttr(row.artist) + '" data-title="' + safeAttr(row.title) + '" title="Remove from star queue">\u00d7</button>';
            }
            if (inSearchQueue) {
                actionsCell += '<button type="button" class="shazam-row-action-btn shazam-remove-queue" data-queue="search" data-key="' + safeAttr(key) + '" data-artist="' + safeAttr(row.artist) + '" data-title="' + safeAttr(row.title) + '" title="Remove from search queue">\u00d7</button>';
            }
            if (inUnstarQueue) {
                actionsCell += '<button type="button" class="shazam-row-action-btn shazam-remove-queue" data-queue="unstar" data-key="' + safeAttr(key) + '" data-artist="' + safeAttr(row.artist) + '" data-title="' + safeAttr(row.title) + '" title="Remove from unstar queue">\u00d7</button>';
            }
            if (inDownloadQueue) {
                actionsCell += '<button type="button" class="shazam-row-action-btn shazam-remove-queue" data-queue="download" data-key="' + safeAttr(key) + '" data-artist="' + safeAttr(row.artist) + '" data-title="' + safeAttr(row.title) + '" title="Remove from download queue">\u00d7</button>';
            }
            actionsCell += '</span>';
        } else {
            /* Order: star toggle, MAYBE toggle (local-only, between skip and star), download, search, then conditional (clear dismissed / undo / skip) */
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-star-action${starToggleInactive}${starOnlyPending ? ' shazam-star-action-pending' : ''}" data-action="${starToggleAction}" data-key="${safeAttr(key)}"${starToggleDataAttrs} data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="${escapeHtml(starOnlyPending ? 'Processing…' : starToggleTitle)}"${starBtnDisabled}>${starBtnContent}</button>`;
            // Maybe button: filled circle when set, outline when unset. Local-only — does NOT touch Soundeo.
            const maybeTitle = isMaybe ? 'Maybe — clear (currently set: heard, undecided)' : 'Maybe — listened, undecided (local only, not on Soundeo)';
            const maybeSvg = isMaybe
                ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="12" cy="12" r="9"/><text x="12" y="17" text-anchor="middle" font-size="14" font-weight="700" fill="#fff" font-family="-apple-system,sans-serif">?</text></svg>'
                : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><text x="12" y="17" text-anchor="middle" font-size="14" font-weight="700" stroke="none" fill="currentColor" font-family="-apple-system,sans-serif">?</text></svg>';
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-maybe-action${isMaybe ? ' shazam-maybe-action-set' : ''}" data-action="maybe" data-key="${safeAttr(key)}" data-value="${isMaybe ? '0' : '1'}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="${escapeHtml(maybeTitle)}">${maybeSvg}</button>`;
            const downloadHave = row.status === 'have' || (isTodl && shazamTrackKeyInHaveLocally(key));
            const downloadInactive = (row.status === 'skipped' || !url) ? inactive : '';
            const downloadHaveClass = downloadHave ? ' shazam-download-have' : '';
            const downloadBusyRow = !!(shazamPendingDownload[key] || (shazamDownloadProgressSnapshot.running && shazamTrackKeyMatches(shazamDownloadProgressSnapshot.current_key, key)));
            const downloadPendingClass = downloadBusyRow ? ' shazam-download-action-pending' : '';
            const downloadPathB64 = downloadHave
                ? (row.filepath ? shazamPathB64FromFilepath(row.filepath) : shazamPathB64ForHaveKey(key))
                : '';
            const downloadTitle = downloadBusyRow
                ? 'Downloading…'
                : downloadHave
                    ? 'Have locally — click: Download again or Show locally'
                    : row.status === 'skipped'
                        ? 'Skipped'
                        : !url
                            ? 'No Soundeo link'
                            : 'Download AIFF';
            const downloadSvg = downloadBusyRow
                ? '<span class="shazam-btn-spinner" title="Downloading…"></span>'
                : downloadHave
                    ? shazamSvgDownloadHaveWhite(17)
                    : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
            const downloadBtnDisabled = downloadBusyRow ? ' disabled' : '';
            const dlAriaBusy = downloadBusyRow ? ' aria-busy="true"' : '';
            const dlPathAttr = downloadPathB64 ? ` data-path-b64="${safeAttr(downloadPathB64)}"` : '';
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-download-action${downloadHaveClass}${downloadPendingClass}${downloadInactive}" data-action="download" data-key="${safeAttr(key)}" title="${escapeHtml(downloadTitle)}"${downloadBtnDisabled}${dlAriaBusy}${dlPathAttr}>${downloadSvg}</button>`;
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-search-action${searchInactive}" data-action="search" data-key="${safeAttr(key)}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Search on Soundeo (find link, no favorite)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></button>`;
            if (isDismissed) {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-clear-dismissed" data-action="clear_dismissed" data-key="${safeAttr(key)}" title="Reset to: have locally, not starred on Soundeo (removes strikethrough, link visible again)">Remove strikethrough</button>`;
            }
            if (isSkipped) {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-undo-action" onclick="shazamUnskipRow(this)" title="Undo skip"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M6 12L18 8v8L6 12z"/></svg></button>`;
            } else if (!skipInactive) {
                // Only render Skip on rows where it's actually actionable (to-download,
                // not dismissed). On have-local rows the previous greyed-out version was
                // routinely mistaken for an "Undo skip" button — same icon family, no
                // pointer-events, no tooltip on touch devices. Hiding it removes the
                // false affordance entirely; the Undo-skip button (rendered only when
                // isSkipped) remains the single back-arrow that ever appears.
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-skip-action" data-action="skip" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Skip (hide locally)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="6" y1="6" x2="6" y2="18"/><line x1="10" y1="6" x2="10" y2="18"/><polygon points="14 8 14 16 20 12"/></svg></button>`;
            }
        }
        actionsCell += '</td>';

        let selectCell = '';
        if (isTodl && !isDismissed) {
            selectCell = `<td class="shazam-select-col"><input type="checkbox" class="shazam-track-cb" data-idx="${idx}" onchange="shazamUpdateSelectionCount()" /></td>`;
        } else {
            selectCell = '<td class="shazam-select-col"></td>';
        }

        let rowClass = isSkipped ? 'shazam-row-skipped' : (isDismissed ? 'shazam-row-dismissed' : (isTodl ? 'to-download' : 'have-local'));
        if (isMaybe) rowClass += ' shazam-row-maybe';
        if (isListened) rowClass += ' shazam-row-listened';
        const rowAttrs = (isSkipped
            ? ` data-artist="${escapedArtist}" data-title="${escapedTitle}"`
            : (isTodl ? ` data-idx="${idx}"` : '')) + ` data-track-key="${escapedKey}"`;

        let titleCellContent = escapeHtml(row.title);
        if (!isDismissed && !isSkipped) {
            if (url) {
                const linkLabel = soundeoTitle ? escapeHtml(soundeoTitle) : 'Open on Soundeo';
                const linkTitle = soundeoTitle ? `Open on Soundeo: ${escapeHtml(soundeoTitle)}` : 'Open on Soundeo';
                titleCellContent += `<div class="soundeo-source-title"><a href="${escapeHtml(url)}" target="_blank" rel="noopener" title="${linkTitle}">${linkLabel}</a></div>`;
            } else if (soundeoTitle) {
                titleCellContent += `<div class="soundeo-source-title" title="${escapeHtml(soundeoTitle)}">${escapeHtml(soundeoTitle)}</div>`;
            }
        }

        // Multi-shazam badge appears after artist name when shazamedCount >= 2.
        const shazamedBadge = shazamedCount > 0
            ? ` <span class="shazam-multi-badge" title="Shazammed ${shazamedCount} times — you probably really liked this">${shazamedCount}×</span>`
            : '';
        // Listened mark — subtle ✓ before the artist name when set.
        const listenedMark = isListened
            ? '<span class="shazam-listened-mark" title="Listened" aria-hidden="true">·</span>'
            : '';
        html += `<tr class="${rowClass}"${rowAttrs}>${statusCell}${coverCell}<td class="shazam-when">${escapeHtml(when)}</td><td>${listenedMark}${escapeHtml(row.artist)}${shazamedBadge}</td><td>${titleCellContent}</td>${matchCell}${playCell}${actionsCell}${selectCell}</tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
    if (selectionBar) selectionBar.style.display = 'none';
    shazamUpdateSelectionCount();
    var foundPlaying = null;
    if (shazamCurrentlyPlaying && shazamAudioEl) {
        const allPlayBtns = el.querySelectorAll('.shazam-play-btn');
        for (const b of allPlayBtns) {
            const pk = shazamPlayBtnPlaybackKey(b);
            if (pk && pk === shazamCurrentlyPlaying) {
                foundPlaying = b;
                break;
            }
        }
    }
    shazamPlayingBtn = foundPlaying;
    shazamBarUpdateActions();
    shazamNudgeHoverAfterTrackTableReplace();
    shazamRestoreSyncProgress(progressCaptured);
}

async function shazamDismissManualCheck(btn) {
    const key = (btn && btn.dataset && btn.dataset.trackKey) ? btn.dataset.trackKey : null;
    if (!key) return;
    try {
        const res = await fetch('/api/shazam-sync/dismiss-manual-check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_key: key })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG || 'Request failed');
            return;
        }
        shazamDismissedManualCheck[key] = true;
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
}

function shazamTogglePlay(btn) {
    if (typeof setsPlayerPause === 'function') setsPlayerPause();
    try {
        const dirB64 = (btn.dataset.dirB64 || '').trim();
        const file = btn.dataset.file;
        const pathB64 = (btn.dataset.pathB64 || '').trim();
        const streamUrl = (dirB64 && file != null)
            ? '/api/shazam-sync/stream-file?dir=' + encodeURIComponent(dirB64) + '&file=' + encodeURIComponent(file)
            : (pathB64 ? '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pathB64) : null);
        if (!streamUrl) return;
        const playKey = streamUrl;

        if (shazamAudioEl) { shazamAudioEl.onended = null; shazamAudioEl.onerror = null; shazamAudioEl.pause(); shazamAudioEl.src = ''; }
        shazamAudioEl = new Audio();

        const playingBtn = document.querySelector('.shazam-play-btn.playing');
        if (playingBtn) {
            playingBtn.innerHTML = PLAY_ICON_ROW;
            playingBtn.classList.remove('playing');
        }
        if (shazamCurrentlyPlaying === playKey) {
            shazamCancelNextBuffer();
            shazamCurrentlyPlaying = null;
            shazamPlayerBarHide();
            return;
        }
        shazamCancelNextBuffer();
        releaseShazamProxy();

        let playErrorAlertShown = false;
        const showPlayError = (msg) => { if (!playErrorAlertShown) { playErrorAlertShown = true; alert(msg); } };
        const resetBtn = () => {
            var rowBtn = shazamPlayingBtn || btn;
            if (rowBtn) { rowBtn.innerHTML = PLAY_ICON_ROW; rowBtn.classList.remove('playing'); }
            shazamCurrentlyPlaying = null;
            shazamPlayingBtn = null;
            shazamPlayerBarHide();
        };
        const fileLower = (btn.dataset.file || '').toLowerCase();
        const isAiffOrWav = /\.(aiff?|wav)$/.test(fileLower);

        if (isAiffOrWav) {
            (async function () {
                var activeBtn = btn;
                activeBtn.textContent = '…';
                activeBtn.disabled = true;
                const body = (dirB64 && file != null) ? { dir_b64: dirB64, file: file } : (pathB64 ? { path_b64: pathB64 } : null);
                if (!body) { activeBtn.disabled = false; activeBtn.innerHTML = PLAY_ICON_ROW; return; }
                try {
                    const _playCtrl = new AbortController();
                    const _playTimer = setTimeout(function () { _playCtrl.abort(); }, 90000);
                    const res = await fetch('/api/shazam-sync/prepare-proxy', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                        signal: _playCtrl.signal
                    });
                    clearTimeout(_playTimer);
                    const data = await res.json().catch(function () { return {}; });
                    if (!res.ok) {
                        showPlayError(data.error || res.status === 403 ? 'Add your music folder to Sync \u2192 Settings \u2192 Destination folders.' : res.status === 404 ? 'File not found.' : 'Prepare failed.');
                        activeBtn.disabled = false;
                        activeBtn.innerHTML = PLAY_ICON_ROW;
                        return;
                    }
                    const mp3Url = data.mp3_url;
                    const proxyId = data.proxy_id;
                    if (!mp3Url || !proxyId) {
                        showPlayError('Invalid prepare response.');
                        activeBtn.disabled = false;
                        activeBtn.innerHTML = PLAY_ICON_ROW;
                        return;
                    }
                    if (!activeBtn.isConnected) {
                        var _tk = activeBtn.dataset.trackKey || '';
                        var _fb = _tk ? shazamFindPlayBtnByTrackKey(_tk) : null;
                        if (_fb && _fb.isConnected) activeBtn = _fb;
                    }
                    shazamCurrentProxyId = proxyId;
                    shazamAudioEl.onerror = function () {
                        resetBtn();
                        fetch(streamUrl).then(function (r) {
                            if (r.status === 403) showPlayError('Playback blocked. Add your music folder to Sync \u2192 Settings \u2192 Destination folders, then run Compare.');
                            else if (r.status === 404) showPlayError('File not found. It may have been moved or deleted.');
                            else showPlayError('Playback failed.');
                        }).catch(function () { showPlayError('Playback failed.'); });
                    };
                    shazamAudioEl.onended = function () {
                        var prevRow = shazamPlayingBtn && shazamPlayingBtn.isConnected ? shazamPlayingBtn.closest('tr') : null;
                        var prevKey = shazamPlayingBtn ? shazamPlayingBtn.dataset.trackKey : null;
                        resetBtn();
                        shazamPlayFromBuffer(prevRow, prevKey);
                    };
                    shazamAudioEl.src = mp3Url;
                    shazamAudioEl.load();
                    await shazamAudioEl.play();
                    activeBtn.innerHTML = PAUSE_ICON_ROW;
                    activeBtn.classList.add('playing');
                    activeBtn.disabled = false;
                    shazamCurrentlyPlaying = playKey;
                    shazamPlayingBtn = activeBtn;
                    shazamPlayerBarShow(activeBtn.dataset.trackLabel || '—');
                    shazamPrefetchNext(activeBtn);
                } catch (e) {
                    showPlayError('Playback failed: ' + (e.message || String(e)));
                    activeBtn.disabled = false;
                    activeBtn.innerHTML = PLAY_ICON_ROW;
                }
            })();
            return;
        }

        btn.textContent = '…';
        shazamAudioEl.onerror = () => {
            resetBtn();
            fetch(streamUrl).then(function (res) {
                if (res.status === 403) {
                    showPlayError('Playback blocked. Add your music folder to Sync \u2192 Settings \u2192 Destination folders, then run Compare.');
                } else if (res.status === 404) {
                    showPlayError('File not found. It may have been moved or deleted.');
                } else if (res.status >= 400 && isAiffOrWav) {
                    showPlayError('Playback failed. For AIFF/WAV files, install ffmpeg (e.g. brew install ffmpeg) and restart the app.');
                } else if (res.status === 200) {
                    showPlayError('Playback failed. The file could not be played. If it\'s AIFF or WAV, ensure ffmpeg is installed (e.g. brew install ffmpeg) and restart the app.');
                }
            }).catch(function () {
                showPlayError('Playback failed. Could not load the file.');
            });
        };
        shazamAudioEl.onended = () => {
            var prevRow = shazamPlayingBtn && shazamPlayingBtn.isConnected ? shazamPlayingBtn.closest('tr') : null;
            var prevKey = shazamPlayingBtn ? shazamPlayingBtn.dataset.trackKey : null;
            resetBtn();
            shazamPlayFromBuffer(prevRow, prevKey);
        };
        shazamAudioEl.src = streamUrl;
        shazamAudioEl.play().then(() => {
            btn.innerHTML = PAUSE_ICON_ROW;
            btn.classList.add('playing');
            shazamCurrentlyPlaying = playKey;
            shazamPlayingBtn = btn;
            shazamPlayerBarShow(btn.dataset.trackLabel || '—');
            shazamPrefetchNext(btn);
        }).catch(() => {
            resetBtn();
            setTimeout(function () {
                showPlayError('Playback could not start. If the file is AIFF or WAV, install ffmpeg (e.g. brew install ffmpeg) and restart the app.');
            }, 100);
        });
    } catch (e) {
        console.error('Play error:', e);
        alert('Play failed: ' + (e.message || String(e)));
    }
}

async function shazamToggleSoundeoPlay(btn) {
    if (typeof setsPlayerPause === 'function') setsPlayerPause();
    const trackUrl = btn.dataset.soundeoUrl;
    if (!trackUrl) return;

    if (shazamAudioEl) { shazamAudioEl.onended = null; shazamAudioEl.onerror = null; shazamAudioEl.pause(); shazamAudioEl.src = ''; }
    shazamAudioEl = new Audio();

    const playingBtn = document.querySelector('.shazam-play-btn.playing');
    if (playingBtn && playingBtn !== btn) {
        playingBtn.innerHTML = PLAY_ICON_ROW;
        playingBtn.classList.remove('playing');
    }
    if (shazamCurrentlyPlaying === trackUrl) {
        shazamCancelNextBuffer();
        btn.innerHTML = PLAY_ICON_ROW;
        btn.classList.remove('playing');
        shazamCurrentlyPlaying = null;
        shazamPlayerBarHide();
        return;
    }
    shazamCancelNextBuffer();

    btn.textContent = '…';
    btn.disabled = true;
    var activeBtn = btn;
    const resetBtn = (errMsg) => {
        activeBtn.innerHTML = PLAY_ICON_ROW;
        activeBtn.classList.remove('playing');
        activeBtn.disabled = false;
        if (errMsg) {
            // Surface the reason on hover so the user has *something* to read instead
            // of a button that silently snaps back to ▶.
            activeBtn.title = '⚠ ' + errMsg;
            activeBtn.classList.add('shazam-play-failed');
            setTimeout(function () { activeBtn.classList.remove('shazam-play-failed'); }, 2400);
        }
    };

    // Pre-flight the preview-URL probe BEFORE wiring up audio.src. The audio
    // element only exposes opaque onerror events (no HTTP status), and the
    // server returns 404 JSON when the Soundeo session has expired — which is
    // by far the most common cause of "press play, nothing happens". Probing
    // the JSON endpoint first lets us tell the user *why* and trigger the
    // connection banner so they can re-auth instead of silently retrying.
    //
    // Exception: while a queue download is running, Soundeo throttles per
    // cookie-session — the track-page fetch in `_extract_soundeo_preview_url`
    // becomes very slow (Soundeo CDN is busy streaming the active download).
    // Pre-flighting then would block the click for 8+ seconds while delivering
    // little signal. Skip it; let the audio element try directly. The
    // stream-preview proxy uses the same cache, so it'll resolve as soon as
    // Soundeo unblocks.
    const downloadActive = !!(typeof shazamDownloadProgressSnapshot !== 'undefined'
        && shazamDownloadProgressSnapshot
        && shazamDownloadProgressSnapshot.running);
    let previewOk = false;
    let previewErr = null;
    let previewStatus = 0;
    if (downloadActive) {
        previewOk = true; // optimistic: skip preflight, let audio element handle
    } else {
        try {
            const probeCtrl = new AbortController();
            // 8s rather than 12s — if Soundeo can't answer within 8s it's not
            // going to suddenly recover, and the user notices the wait.
            const probeTimer = setTimeout(function () { probeCtrl.abort(); }, 8000);
            const probeRes = await fetch('/api/soundeo/preview-url?track_url=' + encodeURIComponent(trackUrl), { signal: probeCtrl.signal });
            clearTimeout(probeTimer);
            previewStatus = probeRes.status;
            if (probeRes.ok) {
                previewOk = true;
            } else {
                const data = await probeRes.json().catch(function () { return {}; });
                previewErr = (data && data.error) || ('Soundeo preview unavailable (HTTP ' + probeRes.status + ')');
            }
        } catch (e) {
            // Timeout / network error: don't block the click. Try playback anyway —
            // the audio element will fail loudly via onerror if Soundeo is truly down.
            previewOk = true;
            previewErr = (e && e.name === 'AbortError')
                ? 'Soundeo preflight timed out — attempting playback anyway'
                : ('Soundeo preflight failed: ' + ((e && e.message) || e));
            console.warn('shazamToggleSoundeoPlay: preflight error, proceeding optimistically:', previewErr);
        }
    }

    if (!previewOk) {
        console.warn('shazamToggleSoundeoPlay: preview pre-flight failed:', previewStatus, previewErr);
        // 404 / "could not extract" almost always means the saved Soundeo session
        // is no longer valid even though our `connected` badge says it is (the
        // badge only checks that a cookies file exists, not that it's still alive).
        // Show the connection banner and alert exactly once per session so the
        // user can re-authenticate via Settings → Reconnect Soundeo.
        const looksLikeSessionExpired = previewStatus === 404 || /could not extract preview/i.test(previewErr || '');
        if (looksLikeSessionExpired && typeof showConnectionBanner === 'function') {
            try { showConnectionBanner(); } catch (e) { /* banner not present */ }
        }
        if (looksLikeSessionExpired && !shazamSoundeoPlayErrorAlerted) {
            shazamSoundeoPlayErrorAlerted = true;
            alert("Couldn't fetch Soundeo preview.\n\n" + previewErr +
                "\n\nMost common cause: your Soundeo session has expired.\n" +
                "Open Settings → Reconnect Soundeo, then try again.");
        }
        resetBtn(previewErr || 'Preview unavailable');
        shazamCurrentlyPlaying = null;
        return;
    }

    // Pre-flight succeeded → play through the proxy stream endpoint (CORS-safe;
    // the Audio element can't load Soundeo's CDN URL directly).
    try {
        const streamUrl = '/api/soundeo/stream-preview?track_url=' + encodeURIComponent(trackUrl);
        shazamAudioEl.onerror = () => {
            console.warn('Soundeo preview audio error after successful preflight');
            resetBtn('Audio playback failed');
            shazamCurrentlyPlaying = null;
        };
        shazamAudioEl.onended = () => {
            var prevRow = shazamPlayingBtn && shazamPlayingBtn.isConnected ? shazamPlayingBtn.closest('tr') : null;
            var prevKey = shazamPlayingBtn ? shazamPlayingBtn.dataset.trackKey : null;
            resetBtn();
            shazamCurrentlyPlaying = null;
            shazamPlayFromBuffer(prevRow, prevKey);
        };
        shazamAudioEl.src = streamUrl;
        shazamAudioEl.load();
        await shazamAudioEl.play();
        if (activeBtn && !activeBtn.isConnected) {
            var _stk = activeBtn.dataset.trackKey || '';
            var _sfb = _stk ? shazamFindPlayBtnByTrackKey(_stk) : null;
            if (_sfb && _sfb.isConnected) activeBtn = _sfb;
        }
        activeBtn.innerHTML = PAUSE_ICON_ROW;
        activeBtn.classList.add('playing');
        activeBtn.disabled = false;
        // Clear any leftover error tooltip from a previous failed attempt on this row.
        const baseLabel = activeBtn.dataset.trackLabel || '';
        if (baseLabel) activeBtn.title = baseLabel;
        shazamCurrentlyPlaying = trackUrl;
        shazamPlayingBtn = activeBtn;
        shazamPlayerBarShow(activeBtn.dataset.trackLabel || '—');
        shazamPrefetchNext(activeBtn);
    } catch (e) {
        console.warn('Soundeo preview playback failed:', e);
        resetBtn('Playback error: ' + ((e && e.message) || e));
        shazamCurrentlyPlaying = null;
    }
}

/** Key variants used for row lookup (match render logic). */
function shazamKeyVariants(key) {
    if (!key) return [];
    const keyLower = key.toLowerCase();
    const keyNorm = key.indexOf(' (') !== -1 ? key.substring(0, key.indexOf(' (')).trim() : key;
    const keyNormLower = keyNorm.toLowerCase();
    // Fold diacritics so 'Âme' matches 'Ame' (backend deep normalization also folds).
    const keyNormFold = (keyNormLower.normalize ? keyNormLower.normalize('NFKD') : keyNormLower).replace(/[\u0300-\u036f]/g, '');
    const keyDeep = (() => { let s = keyNormFold.replace(/ & /g, ', '); const d = s.indexOf(' - '); if (d !== -1) { const arts = s.substring(0, d).split(', ').map(a => a.trim()).filter(Boolean).sort().join(', '); s = arts + ' - ' + s.substring(d + 3); } return s; })();
    return [key, keyLower, keyNorm, keyNormLower, keyDeep];
}

/** First truthy value from map for any key (same pattern as inline _lu in render paths). */
function shazamLookupMap(map) {
    if (!map) return undefined;
    for (var i = 1; i < arguments.length; i++) {
        var k = arguments[i];
        if (k == null || k === '') continue;
        var v = map[k];
        if (v) return v;
    }
    return undefined;
}

/** Set starred state for a key and all display variants so row live-updates. */
function shazamSetStarredLive(key, value) {
    var keys = shazamKeyVariants(key);
    keys.forEach(function (k) { shazamStarred[k] = value; });
    if (shazamLastData && shazamLastData.starred) {
        keys.forEach(function (k) { shazamLastData.starred[k] = value; });
    }
    shazamBarUpdateActions();
}

/** Set dismissed state for a key and all display variants so row live-updates. */
function shazamSetDismissedLive(key, value) {
    var keys = shazamKeyVariants(key);
    if (value) {
        keys.forEach(function (k) { shazamDismissed[k] = true; });
    } else {
        keys.forEach(function (k) { delete shazamDismissed[k]; });
    }
    shazamBarUpdateActions();
}

/** Set track URL for a key and all display variants so dot state (found/starred) and row stay in sync. */
function shazamSetUrlLive(key, url) {
    if (!key) return;
    var keys = shazamKeyVariants(key);
    keys.forEach(function (k) {
        if (url) shazamTrackUrls[k] = url; else delete shazamTrackUrls[k];
    });
    if (shazamLastData && shazamLastData.urls) {
        keys.forEach(function (k) {
            if (url) shazamLastData.urls[k] = url; else delete shazamLastData.urls[k];
        });
    }
    shazamBarUpdateActions();
}

/** Set not_found state for a key and all display variants so dot state (orange vs grey) stays in sync. */
function shazamSetNotFoundLive(key, value) {
    if (!key) return;
    var keys = shazamKeyVariants(key);
    keys.forEach(function (k) {
        if (value) shazamNotFound[k] = true; else delete shazamNotFound[k];
    });
    if (shazamLastData && shazamLastData.not_found) {
        keys.forEach(function (k) {
            if (value) shazamLastData.not_found[k] = true; else delete shazamLastData.not_found[k];
        });
    }
}

/** Merge Soundeo match score into list data so the Match column updates as soon as a single-track search finishes. */
function shazamSetSoundeoMatchScoreLive(key, score) {
    if (key == null || score == null || typeof score !== 'number' || Number.isNaN(score)) return;
    var keys = shazamKeyVariants(key);
    if (!shazamLastData) return;
    if (!shazamLastData.soundeo_match_scores) shazamLastData.soundeo_match_scores = {};
    keys.forEach(function (k) {
        shazamLastData.soundeo_match_scores[k] = score;
    });
}

/** Unstar on Soundeo only; link stays visible, no strikethrough. Queue-based like star/search. */
async function shazamUnstarTrack(key, trackUrl, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    shazamBarUpdateActions();
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    shazamBarUpdateActions();
    try {
        const res = await fetch('/api/shazam-sync/unstar-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, track_url: trackUrl, artist: artist || '', title: title || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            shazamClearActionPendingForKey(key);
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            shazamBarUpdateActions();
            return;
        }
        var unstarQueue = data.unstar_queue || [];
        if (data.status === 'started') {
            shazamBarLog('UNSTAR_HANDLER', 'status=started, showing progress only (no APPLY_QUEUE)');
            shazamCurrentStarQueue = shazamCurrentStarQueue || [];
            shazamCurrentUnstarQueue = unstarQueue;
            shazamSingleBarActive = true;
            shazamShowSyncProgress(data.message || 'Unstarring…');
            shazamStartProgressPoll();
        } else {
            shazamApplyQueueState(shazamCurrentStarQueue, shazamCurrentSearchQueue, unstarQueue);
        }
        shazamBarUpdateActions();
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
        shazamClearActionPendingForKey(key);
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        shazamBarUpdateActions();
    }
}

async function shazamDismissTrack(key, trackUrl, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/dismiss-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, track_url: trackUrl }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            shazamSetDismissedLive(key, true);
            shazamSetStarredLive(key, false);
        } else if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
}

/** Clear dismissed state so link shows again (no strikethrough); does not re-star on Soundeo. */
async function shazamClearDismissed(key) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/clear-dismissed', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            shazamSetDismissedLive(key, false);
        } else if (!res.ok || data.error) {
            alert(data.error || 'Failed to clear dismissed state');
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
}

/** Remove this track from the star, search, unstar, or download queue. Updates local queue state and re-renders. */
async function shazamRemoveFromQueue(btn) {
    if (!btn || !btn.dataset) return;
    const queue = (btn.dataset.queue || '').toLowerCase();
    const key = (btn.dataset.key || '').trim();
    const artist = (btn.dataset.artist || '').trim();
    const title = (btn.dataset.title || '').trim();
    if (queue !== 'star' && queue !== 'search' && queue !== 'unstar' && queue !== 'download') return;
    const url = queue === 'star' ? '/api/shazam-sync/remove-from-star-queue' : (queue === 'search' ? '/api/shazam-sync/remove-from-search-queue' : (queue === 'unstar' ? '/api/shazam-sync/remove-from-unstar-queue' : '/api/shazam-sync/remove-from-download-queue'));
    const body = queue === 'star' ? { key: key || (artist + ' - ' + title) } : (queue === 'search' ? { artist: artist, title: title } : (queue === 'download' ? { key: key || (artist + ' - ' + title) } : (key ? { key: key } : { artist: artist, title: title })));
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || 'Failed to remove from queue');
            return;
        }
        if (queue === 'download') {
            shazamCurrentDownloadQueue = data.download_queue || [];
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            return;
        }
        if (queue === 'star') {
            shazamCurrentStarQueue = data.star_queue || [];
        } else if (queue === 'search') {
            shazamCurrentSearchQueue = data.single_search_queue || [];
        } else {
            shazamCurrentUnstarQueue = data.unstar_queue || [];
        }
        shazamApplyQueueState(shazamCurrentStarQueue, shazamCurrentSearchQueue, shazamCurrentUnstarQueue);
        if (queue === 'star' || queue === 'unstar') {
            var rk = (key || ((artist || '') + ' - ' + (title || ''))).trim();
            if (rk) shazamClearActionPendingForKey(rk);
        }
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        shazamBarUpdateActions();
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
}

async function shazamUndismissTrack(key, trackUrl, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/undismiss-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, track_url: trackUrl, artist, title }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            shazamSetDismissedLive(key, false);
            shazamSetStarredLive(key, true);
            if (data.url) shazamSetUrlLive(key, data.url);
        } else if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    await shazamLoadStatus();
}

async function shazamStarTrack(key, trackUrl, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    shazamBarUpdateActions();
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    shazamBarUpdateActions();
    try {
        const res = await fetch('/api/shazam-sync/star-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, track_url: trackUrl || undefined, artist: artist || '', title: title || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG || 'Could not star track');
            shazamClearActionPendingForKey(key);
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            shazamBarUpdateActions();
            return;
        }
        var starQueue = data.star_queue || [];
        if (data.status === 'started') {
            shazamBarLog('STAR_HANDLER', 'status=started, showing progress only (no APPLY_QUEUE)');
            shazamCurrentStarQueue = starQueue;
            shazamCurrentSearchQueue = shazamCurrentSearchQueue || [];
            shazamCurrentUnstarQueue = data.unstar_queue !== undefined ? data.unstar_queue : shazamCurrentUnstarQueue;
            shazamSingleBarActive = true;
            shazamShowSyncProgress(data.message || 'Starring…');
            shazamStartProgressPoll();
        } else {
            shazamApplyQueueState(starQueue, shazamCurrentSearchQueue, data.unstar_queue !== undefined ? data.unstar_queue : shazamCurrentUnstarQueue);
        }
        shazamBarUpdateActions();
    } catch (e) {
        shazamClearActionPendingForKey(key);
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        shazamBarUpdateActions();
        alert('Error: ' + (e.message || 'Request failed'));
    }
}

async function shazamSkipSingleTrack(artist, title, opts) {
    opts = opts || {};
    const fromPlaybar = !!opts.fromPlaybar;
    const playNextKey = opts.playNextKey != null ? opts.playNextKey : null;
    const key = `${artist} - ${title}`;
    const keyLower = key.toLowerCase();
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;

    /* Playbar skip: stop audio immediately. Optimistic re-render removes the row but leaves shazamAudioEl playing until the fetch returns — user hears the skipped track the whole time. */
    if (fromPlaybar) {
        shazamCancelNextBuffer();
        if (shazamAudioEl) {
            shazamAudioEl.onended = null;
            shazamAudioEl.onerror = null;
            if (shazamBarTimeUpdate) {
                shazamAudioEl.removeEventListener('timeupdate', shazamBarTimeUpdate);
                shazamBarTimeUpdate = null;
            }
            if (shazamBarEnded) {
                shazamAudioEl.removeEventListener('ended', shazamBarEnded);
                shazamBarEnded = null;
            }
            shazamAudioEl.pause();
            shazamAudioEl.src = '';
        }
        releaseShazamProxy();
        shazamCurrentlyPlaying = null;
        var _playingInList = document.querySelector('#shazamTrackList .shazam-play-btn.playing');
        if (_playingInList) {
            _playingInList.innerHTML = PLAY_ICON_ROW;
            _playingInList.classList.remove('playing');
        }
        shazamPlayingBtn = null;
        shazamPlayerBarHide();
    }

    // Optimistic update: move track to skipped so UI updates immediately
    let reverted = false;
    if (shazamLastData) {
        const toDl = shazamLastData.to_download || [];
        const idx = toDl.findIndex(t => (t.artist || '').trim() + ' - ' + (t.title || '').trim() === key || (t.artist || '').trim().toLowerCase() + ' - ' + (t.title || '').trim().toLowerCase() === keyLower);
        if (idx !== -1) {
            const entry = toDl[idx];
            const skippedTracks = (shazamLastData.skipped_tracks || []).slice();
            skippedTracks.push({ artist: entry.artist, title: entry.title, shazamed_at: entry.shazamed_at });
            shazamLastData.to_download = toDl.filter((_, i) => i !== idx);
            shazamLastData.to_download_count = (shazamLastData.to_download_count || toDl.length) - 1;
            shazamLastData.skipped_tracks = skippedTracks;
            shazamRenderTrackList(shazamLastData);
        }
    }

    function shazamStopPlaybarAfterSkip() {
        shazamCancelNextBuffer();
        if (shazamAudioEl) shazamAudioEl.pause();
        shazamCurrentlyPlaying = null;
        shazamPlayerBarHide();
    }

    async function shazamTryPlayNextAfterSkip() {
        if (!fromPlaybar) return;
        if (playNextKey) {
            var nextBtn = shazamFindPlayBtnByTrackKey(playNextKey);
            if (nextBtn) {
                if (nextBtn.classList.contains('shazam-soundeo-play') && !nextBtn.dataset.dirB64 && !nextBtn.dataset.pathB64) {
                    await shazamToggleSoundeoPlay(nextBtn);
                } else {
                    shazamTogglePlay(nextBtn);
                }
            } else {
                shazamStopPlaybarAfterSkip();
            }
        } else {
            shazamStopPlaybarAfterSkip();
        }
    }

    try {
        const res = await fetch('/api/shazam-sync/skip-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            delete shazamActionPending[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            await shazamTryPlayNextAfterSkip();
            return;
        }
        reverted = true;
        alert(data.error || SHAZAM_ACTION_REJECTED_MSG || 'Skip failed');
    } catch (e) {
        reverted = true;
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (reverted && shazamLastData) shazamLoadStatus();
}

async function shazamSyncSingleTrack(key, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/sync-single-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, artist, title }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                var syncSingleLabel = 'Find & star: ' + (artist + ' – ' + title);
                if (syncSingleLabel.length > 45) syncSingleLabel = syncSingleLabel.slice(0, 42) + '…';
                shazamJobQueue.push({ id: ++shazamJobId, type: 'sync_single_track', label: syncSingleLabel, payload: { key: key, artist: artist, title: title } });
                shazamRenderJobQueue();
                shazamEnsureProgressVisibleWhenQueued();
            } else {
                alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            }
            delete shazamActionPending[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            return;
        }
        if (data.status === 'started') {
            if (shazamProgressInterval) { clearInterval(shazamProgressInterval); shazamProgressInterval = null; }
            if (shazamProgressRestoreInterval) { clearInterval(shazamProgressRestoreInterval); shazamProgressRestoreInterval = null; }
            shazamShowSyncProgress();
            const pollStart = Date.now();
            const poll = setInterval(async () => {
                if (Date.now() - pollStart > SHAZAM_INLINE_POLL_MAX_MS) {
                    clearInterval(poll);
                    shazamHideSyncProgress();
                    shazamCurrentProgress = {};
                    return;
                }
                const pRes = await fetch('/api/shazam-sync/progress');
                const p = await pRes.json();
                shazamCurrentProgress = p;
                const el = document.getElementById('shazamProgress');
                if (el) el.textContent = p.running ? (p.message || 'Finding & starring…') : (p.error || p.message || 'Done.');
                shazamSetProgressClickable(p.running && !!p.current_key);
                if (p.running && shazamLastData) shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
                if (!p.running) {
                    shazamCurrentProgress = {};
                    clearInterval(poll);
                    shazamHideSyncProgress();
                    if (p.mode === 'sync_single') {
                        if (p.done === 1 && p.url) {
                            shazamSetUrlLive(key, p.url);
                            if (p.soundeo_title) {
                                shazamKeyVariants(key).forEach(function (k) {
                                    shazamSoundeoTitles[k] = p.soundeo_title;
                                });
                            }
                            shazamLoadStatus();
                            shazamMaybeStartQueuedJob();
                        } else if (p.error) {
                            alert(p.error);
                        }
                    }
                    delete shazamActionPending[key];
                    if (shazamLastData) shazamRenderTrackList(shazamLastData);
                }
            }, 500);
            return;
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
}


function shazamToggleSelectAll(checkbox) {
    document.querySelectorAll('.shazam-track-cb').forEach(cb => { cb.checked = checkbox.checked; });
    shazamUpdateSelectionCount();
}

function shazamUpdateSelectionCount() {
    const checked = document.querySelectorAll('.shazam-track-cb:checked');
    const el = document.getElementById('shazamSelectedCount');
    const selectionBar = document.getElementById('shazamSelectionBar');
    if (el) el.textContent = checked.length + ' selected';
    if (selectionBar) selectionBar.style.display = checked.length > 0 ? 'flex' : 'none';
}

function shazamGetSelectedTracks() {
    const checked = document.querySelectorAll('.shazam-track-cb:checked');
    return Array.from(checked).map(cb => shazamToDownloadTracks[parseInt(cb.dataset.idx, 10)]).filter(Boolean);
}

async function shazamDownloadSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    const urls = (shazamLastData && shazamLastData.urls) ? shazamLastData.urls : {};
    const keys = tracks
        .map(t => (t.artist || '') + ' - ' + (t.title || ''))
        .filter(k => urls[k] || urls[k.toLowerCase()]);
    if (!keys.length) {
        alert('Selected tracks have no Soundeo link. Search first to get a link.');
        return;
    }
    try {
        const res = await fetch('/api/shazam-sync/download-queue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keys })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || 'Download failed');
            return;
        }
        if (data.download_queue && Array.isArray(data.download_queue)) {
            shazamCurrentDownloadQueue = data.download_queue;
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
        }
        if (data.status === 'started') {
            shazamShowSyncProgress(data.message || `Downloading ${keys.length} track(s)…`);
            shazamStartDownloadPoll();
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function shazamSkipSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    try {
        const res = await fetch('/api/shazam-sync/skip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.error || 'Skip failed'); return; }
        shazamLoadStatus();
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamUnskipRow(btn) {
    const tr = btn.closest('tr');
    if (!tr) return;
    shazamUnskip(tr.dataset.artist || '', tr.dataset.title || '');
}

async function shazamUnskip(artist, title) {
    try {
        const res = await fetch('/api/shazam-sync/unskip', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: [{ artist, title }] }),
        });
        const data = await res.json();
        if (!res.ok) { alert(data.error || 'Undo failed'); return; }
        shazamLoadStatus();
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamIgnoreTrackRow(btn) {
    const key = (btn && btn.dataset && btn.dataset.trackKey) ? btn.dataset.trackKey : '';
    const url = (btn && btn.dataset && btn.dataset.trackUrl) ? btn.dataset.trackUrl : '';
    if (key && url) shazamIgnoreTrack(key, url);
}

async function shazamIgnoreTrack(key, url) {
    if (!confirm('Remove this track from your Soundeo favorites? This cannot be undone from the app.')) return;
    try {
        const res = await fetch('/api/shazam-sync/remove-from-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_key: key, track_url: url })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(data.error || 'Failed to remove from Soundeo'); return; }
        shazamLoadStatus();
    } catch (e) { alert('Error: ' + e.message); }
}

async function shazamIgnoreSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    const keyToUrl = shazamTrackUrls || {};
    const withUrl = tracks.filter(t => {
        const k = `${t.artist} - ${t.title}`;
        return keyToUrl[k];
    });
    if (!withUrl.length) { alert('Selected tracks have no Soundeo link. Sync first or select tracks with a link.'); return; }
    const n = withUrl.length;
    if (!confirm(`Remove ${n} track(s) from your Soundeo favorites? This cannot be undone from the app.`)) return;
    try {
        for (const t of withUrl) {
            const key = `${t.artist} - ${t.title}`;
            const url = keyToUrl[key];
            await fetch('/api/shazam-sync/remove-from-soundeo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ track_key: key, track_url: url })
            });
        }
        shazamLoadStatus();
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamRenderJobQueue() {
    const bar = document.getElementById('shazamJobQueueBar');
    const list = document.getElementById('shazamJobQueueList');
    const clearBtn = document.getElementById('shazamJobQueueClearBtn');
    if (!bar || !list) return;
    if (shazamJobQueue.length === 0) {
        if (clearBtn) clearBtn.style.display = 'none';
        shazamHideBarWithAnimation(bar, function () {
            list.innerHTML = '';
            shazamUpdateBatchJobsSectionVisibility();
        });
    } else {
        list.innerHTML = shazamJobQueue.map(job => {
            const remove = escapeHtml('×');
            return `<span class="shazam-job-queue-item" data-job-id="${job.id}">${escapeHtml(job.label)} <button type="button" class="shazam-job-queue-remove" onclick="shazamRemoveQueuedJob(${job.id})" title="Remove from queue">${remove}</button></span>`;
        }).join('');
        if (clearBtn) clearBtn.style.display = 'inline-block';
        shazamShowBarWithAnimation(bar);
        shazamUpdateBatchJobsSectionVisibility();
    }
}

/** When we queue a job because something is already running, show progress section and start polling so "Running:" is visible. */
function shazamEnsureProgressVisibleWhenQueued() {
    if (shazamProgressInterval) return;
    shazamShowSyncProgress('Loading…');
    shazamStartProgressPoll();
}

function shazamClearJobQueue() {
    shazamJobQueue = [];
    shazamRenderJobQueue();
}

function shazamRemoveQueuedJob(id) {
    shazamJobQueue = shazamJobQueue.filter(j => j.id !== id);
    shazamRenderJobQueue();
}

async function shazamMaybeStartQueuedJob() {
    if (shazamJobQueue.length === 0) return;
    try {
        const pRes = await fetch('/api/shazam-sync/progress');
        const p = await pRes.json();
        if (p.running) return;
        const sRes = await fetch('/api/shazam-sync/status');
        const s = await sRes.json();
        if (s && s.compare_running) return;
    } catch (_) { return; }
    const job = shazamJobQueue.shift();
    shazamRenderJobQueue();
    if (job.type === 'search') {
        const res = await fetch('/api/shazam-sync/search-soundeo-global', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ search_mode: job.payload.mode })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowSyncProgress(data.message || 'Searching…');
        shazamStartProgressPoll();
    } else if (job.type === 'star_batch') {
        const res = await fetch('/api/shazam-sync/star-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: job.payload.tracks })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowSyncProgress(data.message || 'Starring…');
        shazamStartProgressPoll();
    } else if (job.type === 'sync_favorites') {
        const res = await fetch('/api/shazam-sync/sync-favorites-from-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ time_range: job.payload.time_range || 'all' })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowSyncProgress(data.message || 'Syncing favorites from Soundeo…');
        shazamStartProgressPoll();
    } else if (job.type === 'run_soundeo') {
        const res = await fetch('/api/shazam-sync/run-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ time_range: job.payload.time_range || 'all' })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowSyncProgress(data.message || 'Syncing to Soundeo…');
        shazamStartProgressPoll();
    } else if (job.type === 'sync_single_track') {
        const res = await fetch('/api/shazam-sync/sync-single-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: job.payload.key, artist: job.payload.artist, title: job.payload.title })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowSyncProgress(data.message || 'Finding & starring…');
        shazamStartProgressPoll();
    } else if (job.type === 'compare') {
        const res = await fetch('/api/shazam-sync/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0, 'Starting compare…');
            shazamStartComparePoll(Date.now());
        } else if (data.error) {
            shazamLoadStatus();
        }
    } else if (job.type === 'rescan') {
        const res = await fetch('/api/shazam-sync/rescan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ compare_after: job.payload.compare_after !== false })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0, job.payload.compare_after !== false ? 'Rescan & compare…' : 'Rescanning…');
            shazamStartComparePoll(Date.now());
        } else if (data.error) {
            shazamLoadStatus();
        }
    } else if (job.type === 'rescan_folder') {
        const res = await fetch('/api/shazam-sync/rescan-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ folder_path: job.payload.folder_path })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { shazamLoadStatus(); return; }
        shazamShowCompareProgress(true, 0, 0, 'Rescanning folder…');
        shazamStartComparePoll(Date.now());
    } else if (job.type === 'fetch_shazam') {
        const res = await fetch('/api/shazam-sync/fetch-shazam', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json().catch(() => ({}));
        if (!res.ok && data.error) { shazamLoadStatus(); return; }
        shazamLoadStatus();
        shazamSearchAllOnSoundeo('new');
        shazamMaybeStartQueuedJob();
    }
}

async function shazamStarSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    const keyToUrl = shazamTrackUrls || {};
    const urlsFromData = (shazamLastData && shazamLastData.urls) ? shazamLastData.urls : {};
    const withUrl = tracks.map(t => {
        const key = `${t.artist} - ${t.title}`;
        const url = keyToUrl[key] || keyToUrl[key.toLowerCase()] || urlsFromData[key] || urlsFromData[key.toLowerCase()];
        return url ? { key, track_url: url, artist: t.artist || '', title: t.title || '' } : null;
    }).filter(Boolean);
    if (!withUrl.length) {
        alert('Selected tracks have no Soundeo link. Run Search first to get links.');
        return;
    }
    try {
        const res = await fetch('/api/shazam-sync/star-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks: withUrl }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                shazamJobQueue.push({ id: ++shazamJobId, type: 'star_batch', label: `Star (${withUrl.length} tracks)`, payload: { tracks: withUrl } });
                shazamRenderJobQueue();
                shazamEnsureProgressVisibleWhenQueued();
            } else {
                alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            }
            return;
        }
        shazamShowSyncProgress(data.message || 'Starring…');
        shazamStartProgressPoll();
    } catch (e) { alert('Error: ' + e.message); }
}

async function shazamStopSync() {
    try {
        await fetch('/api/shazam-sync/stop', { method: 'POST' });
        const stopBtn = document.getElementById('shazamSyncStopBtn');
        if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopping…'; }
    } catch (e) { alert('Error: ' + e.message); }
}

/** Duration (ms) for queue bar in/out animations; must match CSS --shazam-bar-anim-duration. */
var SHAZAM_BAR_ANIM_MS = 450;

/** Log progress/queue bar visibility for debugging double-show. Filter console by "[ShazamBar]". */
function shazamBarLog(tag, message, detail) {
    var ts = new Date().toISOString().split('T')[1].slice(0, 12);
    var caller = '';
    try {
        var stack = new Error().stack;
        if (stack) {
            var lines = stack.split('\n').slice(2, 5);
            caller = lines.map(function (l) { return l.replace(/^\s*at\s+/, '').split(' ')[0]; }).join(' <- ');
        }
    } catch (e) {}
    console.log('[ShazamBar] ' + ts + ' | ' + tag + ' | ' + message, detail !== undefined ? detail : '', caller ? '| ' + caller : '');
}

/** Show a queue/progress bar with a smooth slide-in-from-top animation. Only animates when the bar is not already visible — repeated calls while visible are no-ops (no animation restart). */
function shazamShowBarWithAnimation(barEl) {
    if (!barEl) return;
    var isProgressBar = barEl.id === 'shazamSyncProgress';
    if (!isProgressBar && shazamIsSingleTrackProgress()) {
        return;
    }
    if (barEl.style.display === 'flex' && !barEl.classList.contains('shazam-bar-leave')) {
        return;
    }
    barEl.classList.remove('shazam-bar-leave');
    barEl.style.display = 'flex';
    barEl.classList.remove('shazam-bar-enter');
    void barEl.offsetHeight;
    barEl.classList.add('shazam-bar-enter');
    const onEnd = function () {
        barEl.classList.remove('shazam-bar-enter');
        barEl.removeEventListener('animationend', onEnd);
    };
    barEl.addEventListener('animationend', onEnd);
}

/** Hide a queue/progress bar with a smooth slide-out animation, then run callback (e.g. update section visibility). */
function shazamHideBarWithAnimation(barEl, callback) {
    if (!barEl) {
        if (callback) callback();
        return;
    }
    if (barEl.style.display === 'none') {
        if (callback) callback();
        return;
    }
    var w = document.getElementById('shazamQueueBarsFixed');
    if (w && w.style.display !== 'none') w.dataset.leaveHeight = w.offsetHeight;
    shazamBarLog('HIDE_BAR', 'bar hiding + animation', { id: barEl.id || '(no id)' });
    barEl.classList.remove('shazam-bar-enter');
    barEl.classList.add('shazam-bar-leave');
    const onEnd = function () {
        barEl.classList.remove('shazam-bar-leave');
        barEl.removeEventListener('animationend', onEnd);
        barEl.style.display = 'none';
        if (callback) callback();
    };
    barEl.addEventListener('animationend', onEnd);
    setTimeout(function () {
        if (barEl.classList.contains('shazam-bar-leave')) {
            barEl.removeEventListener('animationend', onEnd);
            barEl.classList.remove('shazam-bar-leave');
            barEl.style.display = 'none';
            if (callback) callback();
        }
    }, SHAZAM_BAR_ANIM_MS + 50);
}

/** Show/hide the queue bars fixed wrapper (notification bubble). Wrapper-level in/out animations; hide when no bars visible. */
function shazamUpdateBatchJobsSectionVisibility() {
    var progressEl = document.getElementById('shazamSyncProgress');
    var progressVisible = progressEl && progressEl.style.display === 'flex';
    var searchQueueBar = document.getElementById('shazamSingleSearchQueueBar');
    var starQueueBar = document.getElementById('shazamStarQueueBar');
    var unstarQueueBar = document.getElementById('shazamUnstarQueueBar');
    var downloadQueueBar = document.getElementById('shazamDownloadQueueBar');
    var searchQueueVisible = searchQueueBar && searchQueueBar.style.display === 'flex';
    var starQueueVisible = starQueueBar && starQueueBar.style.display === 'flex';
    var unstarQueueVisible = unstarQueueBar && unstarQueueBar.style.display === 'flex';
    var downloadQueueVisible = downloadQueueBar && downloadQueueBar.style.display === 'flex';
    var jobQueueVisible = shazamJobQueue.length > 0;
    var willShow = progressVisible || searchQueueVisible || starQueueVisible || unstarQueueVisible || downloadQueueVisible || jobQueueVisible;
    var wrapper = document.getElementById('shazamQueueBarsFixed');
    if (!wrapper) return;
    if (willShow) {
        wrapper.classList.remove('shazam-queue-bars-leaving');
        wrapper.style.height = '';
        wrapper.style.overflow = '';
        if (wrapper.style.display === 'none') {
            wrapper.style.display = 'flex';
            wrapper.classList.add('shazam-queue-bars-entering');
            setTimeout(function () { wrapper.classList.remove('shazam-queue-bars-entering'); }, SHAZAM_BAR_ANIM_MS);
        }
        return;
    }
    if (wrapper.style.display === 'none') return;
    var h = wrapper.dataset.leaveHeight || wrapper.offsetHeight;
    if (h) wrapper.style.height = h + 'px';
    wrapper.style.overflow = 'hidden';
    delete wrapper.dataset.leaveHeight;
    function clearWrapper() {
        wrapper.classList.remove('shazam-queue-bars-leaving');
        wrapper.style.display = 'none';
        wrapper.style.height = '';
        wrapper.style.overflow = '';
        delete wrapper.dataset.leaveHeight;
    }
    function onOutDone(ev) {
        if (ev.target !== wrapper) return;
        wrapper.removeEventListener('animationend', onOutDone);
        clearWrapper();
    }
    requestAnimationFrame(function () { wrapper.classList.add('shazam-queue-bars-leaving'); });
    wrapper.addEventListener('animationend', onOutDone, false);
    setTimeout(function () {
        if (wrapper.classList.contains('shazam-queue-bars-leaving')) {
            wrapper.removeEventListener('animationend', onOutDone);
            clearWrapper();
        }
    }, SHAZAM_BAR_ANIM_MS + 50);
}

/** Set current queue state (globals + banners) so row "Queued 2/5" and queue bars stay in sync. */
function shazamApplyQueueState(starQueue, searchQueue, unstarQueue) {
    shazamBarLog('APPLY_QUEUE', 'updating queue bars (may show/hide)', { star: (starQueue || []).length, search: (searchQueue || []).length, unstar: (unstarQueue !== undefined ? (unstarQueue || []) : shazamCurrentUnstarQueue).length });
    shazamCurrentStarQueue = starQueue || [];
    shazamCurrentSearchQueue = searchQueue || [];
    shazamCurrentUnstarQueue = unstarQueue !== undefined ? (unstarQueue || []) : shazamCurrentUnstarQueue;
    shazamRenderStarQueue(shazamCurrentStarQueue);
    shazamRenderSingleSearchQueue(shazamCurrentSearchQueue);
    shazamRenderUnstarQueue(shazamCurrentUnstarQueue);
}

/** Render the per-track search queue (from progress or POST response). queue = [ { artist, title }, ... ] */
function shazamRenderSingleSearchQueue(queue) {
    const bar = document.getElementById('shazamSingleSearchQueueBar');
    const list = document.getElementById('shazamSingleSearchQueueList');
    if (!bar || !list) return;
    if (!queue || queue.length === 0) {
        shazamHideBarWithAnimation(bar, function () {
            list.innerHTML = '';
            shazamUpdateBatchJobsSectionVisibility();
        });
    } else {
        list.innerHTML = queue.map(function (q) {
            const label = (q.artist && q.title) ? (q.artist + ' – ' + q.title) : (q.artist || q.title || '…');
            return '<span class="shazam-job-queue-item">' + escapeHtml(label) + '</span>';
        }).join('');
        shazamShowBarWithAnimation(bar);
        shazamUpdateBatchJobsSectionVisibility();
    }
}

/** Render the per-track star queue. queue = [ { artist, title, key? }, ... ]. Uses dedicated Star queue bar so Search and Star can both be visible. */
function shazamRenderStarQueue(queue) {
    const bar = document.getElementById('shazamStarQueueBar');
    const list = document.getElementById('shazamStarQueueList');
    if (!bar || !list) return;
    if (!queue || queue.length === 0) {
        shazamHideBarWithAnimation(bar, function () {
            list.innerHTML = '';
            shazamUpdateBatchJobsSectionVisibility();
        });
    } else {
        list.innerHTML = queue.map(function (q) {
            const label = (q.artist && q.title) ? (q.artist + ' – ' + q.title) : (q.artist || q.title || q.key || '…');
            return '<span class="shazam-job-queue-item">' + escapeHtml(label) + '</span>';
        }).join('');
        shazamShowBarWithAnimation(bar);
        shazamUpdateBatchJobsSectionVisibility();
    }
}

/** Render the per-track unstar queue. queue = [ { artist, title, key? }, ... ]. */
function shazamRenderUnstarQueue(queue) {
    const bar = document.getElementById('shazamUnstarQueueBar');
    const list = document.getElementById('shazamUnstarQueueList');
    if (!bar || !list) return;
    if (!queue || queue.length === 0) {
        shazamHideBarWithAnimation(bar, function () {
            list.innerHTML = '';
            shazamUpdateBatchJobsSectionVisibility();
        });
    } else {
        list.innerHTML = queue.map(function (q) {
            const label = (q.artist && q.title) ? (q.artist + ' – ' + q.title) : (q.artist || q.title || q.key || '…');
            return '<span class="shazam-job-queue-item">' + escapeHtml(label) + '</span>';
        }).join('');
        shazamShowBarWithAnimation(bar);
        shazamUpdateBatchJobsSectionVisibility();
    }
}

/** Render the download queue. queue = [ 'Artist - Title', ... ] (keys). */
function shazamRenderDownloadQueue(queue) {
    const bar = document.getElementById('shazamDownloadQueueBar');
    const list = document.getElementById('shazamDownloadQueueList');
    if (!bar || !list) return;
    if (!queue || queue.length === 0) {
        shazamHideBarWithAnimation(bar, function () {
            list.innerHTML = '';
            shazamUpdateBatchJobsSectionVisibility();
        });
    } else {
        list.innerHTML = queue.map(function (key) {
            return '<span class="shazam-job-queue-item">' + escapeHtml(key || '…') + '</span>';
        }).join('');
        shazamShowBarWithAnimation(bar);
        shazamUpdateBatchJobsSectionVisibility();
    }
}

function shazamShowSyncProgress(initialMessage) {
    const el = document.getElementById('shazamSyncProgress');
    const textEl = document.getElementById('shazamProgress');
    const stopBtn = document.getElementById('shazamSyncStopBtn');
    const alreadyVisible = el && el.style.display === 'flex';
    shazamBarLog('SHOW_PROGRESS', alreadyVisible ? 'progress bar already visible (text only)' : 'progress bar showing with animation', { message: (initialMessage || 'Starting…').slice(0, 40), alreadyVisible: !!alreadyVisible });
    if (el) {
        if (!alreadyVisible) shazamShowBarWithAnimation(el);
        else el.style.display = 'flex';
    }
    if (textEl) textEl.textContent = initialMessage || 'Starting…';
    if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = 'Stop'; }
    shazamUpdateBatchJobsSectionVisibility();
}

function shazamHideSyncProgress() {
    shazamBarLog('HIDE_PROGRESS', 'hiding progress bar and clearing queue bars');
    shazamFollowCurrentRow = false;
    const el = document.getElementById('shazamSyncProgress');
    const viewLogBtn = document.getElementById('shazamDownloadViewLogBtn');
    const gotoBtn = document.getElementById('shazamProgressGotoBtn');
    if (viewLogBtn) viewLogBtn.style.display = 'none';
    if (gotoBtn) gotoBtn.textContent = 'Follow row';
    shazamSetProgressClickable(false);
    if (el) {
        shazamHideBarWithAnimation(el, function () {
            shazamRefreshQueueBarsFromProgress(function () {
                if (shazamLastData) shazamRenderTrackList(shazamLastData);
            });
        });
    } else {
        shazamRefreshQueueBarsFromProgress(function () {
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    }
}

/** When progress has current_key, show progress bar as clickable (cursor + title + Follow row button). */
function shazamSetProgressClickable(clickable) {
    const el = document.getElementById('shazamSyncProgress');
    const btn = document.getElementById('shazamProgressGotoBtn');
    if (!el) return;
    el.classList.toggle('shazam-progress-goto-row', !!clickable);
    el.title = clickable ? (shazamFollowCurrentRow ? 'Click to unfollow row' : 'Click to follow row') : '';
    if (btn) {
        btn.style.display = clickable ? 'inline-block' : 'none';
        btn.textContent = shazamFollowCurrentRow ? 'Unfollow row' : 'Follow row';
    }
}

/** Scroll the current track row to the center of the viewport and optionally highlight. Used for follow mode. */
function shazamScrollCurrentRowToCenter(highlight) {
    const key = shazamCurrentProgress && shazamCurrentProgress.current_key;
    if (!key) return;
    const rows = document.querySelectorAll('.shazam-track-table tbody tr[data-track-key]');
    for (const row of rows) {
        const rowKey = row.getAttribute('data-track-key');
        if (rowKey === key || (rowKey && rowKey.toLowerCase() === key.toLowerCase())) {
            row.scrollIntoView({ behavior: 'smooth', block: 'center' });
            if (highlight) {
                row.classList.add('shazam-row-highlight');
                clearTimeout(row._highlightTimeout);
                row._highlightTimeout = setTimeout(function () { row.classList.remove('shazam-row-highlight'); }, 2500);
            }
            break;
        }
    }
}

/** Click black “have” download button: menu — Download again / Show locally. */
function shazamDownloadHaveMenuClose() {
    var menu = document.getElementById('shazamDownloadHaveMenu');
    if (menu) {
        menu.style.display = 'none';
        menu.innerHTML = '';
    }
    document.removeEventListener('click', shazamDownloadHaveMenuClose);
}

function shazamOpenPathB64InFinder(pathB64) {
    if (!pathB64) return;
    fetch('/api/shazam-sync/open-file-location', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path_b64: pathB64 }),
    })
        .then(r => r.json())
        .then(d => {
            if (d.error) alert(d.error);
            else if (d.warning) alert('Show locally: ' + d.warning);
        })
        .catch(() => {});
}

/**
 * @param {MouseEvent} ev
 * @param {string} key Track key
 * @param {string} pathB64 Full file path as from btoa(utf-8 path)
 */
function shazamDownloadHaveMenuAt(ev, key, pathB64) {
    var menu = document.getElementById('shazamDownloadHaveMenu');
    if (!menu || !ev) return;
    shazamDownloadHaveMenuClose();
    menu.innerHTML = '';

    var keyLower = (key || '').toLowerCase();
    var keyNorm = key && key.indexOf(' (') !== -1 ? key.substring(0, key.indexOf(' (')).trim() : key;
    var keyNormLower = (keyNorm || '').toLowerCase();
    var keyDeep = (shazamKeyVariants(key || '') || []).slice(-1)[0] || null;
    var trackUrl = shazamLookupMap(shazamTrackUrls, key, keyLower, keyNorm, keyNormLower, keyDeep)
        || (shazamLastData && shazamLastData.urls ? shazamLookupMap(shazamLastData.urls, key, keyLower, keyNorm, keyNormLower, keyDeep) : undefined)
        || null;

    var items = [];
    items.push({
        label: 'Download again',
        disabled: !trackUrl,
        action: function () {
            if (trackUrl) shazamDownloadTrack(key);
        },
    });
    if (pathB64) {
        items.push({
            label: 'Show locally',
            disabled: false,
            action: function () { shazamOpenPathB64InFinder(pathB64); },
        });
    }

    items.forEach(function (item) {
        var span = document.createElement('button');
        span.type = 'button';
        span.className = 'shazam-play-context-menu-item';
        span.textContent = item.label;
        if (item.disabled) {
            span.disabled = true;
            span.classList.add('shazam-context-menu-item-disabled');
        } else {
            span.addEventListener('click', function (e) {
                e.preventDefault();
                item.action();
                shazamDownloadHaveMenuClose();
            });
        }
        menu.appendChild(span);
    });

    menu.style.display = 'block';
    var pad = 8;
    var x = ev.clientX;
    var y = ev.clientY;
    menu.style.left = '-9999px';
    menu.style.top = '0px';
    var rect = menu.getBoundingClientRect();
    if (x + rect.width + pad > window.innerWidth) x = window.innerWidth - rect.width - pad;
    if (y + rect.height + pad > window.innerHeight) y = window.innerHeight - rect.height - pad;
    if (x < pad) x = pad;
    if (y < pad) y = pad;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    setTimeout(function () { document.addEventListener('click', shazamDownloadHaveMenuClose); }, 0);
}

function shazamDownloadHaveMenuFromButton(ev, btn) {
    if (!btn || !ev) return;
    ev.preventDefault();
    ev.stopPropagation();
    var key = (btn.dataset.key || '').trim();
    var pathB64 = (btn.dataset.pathB64 || '').trim();
    shazamDownloadHaveMenuAt(ev, key, pathB64);
}

/** Decode base64 path (same encoding as backend: UTF-8 then b64). Returns path string or empty. */
function shazamDecodePathB64(pathB64) {
    if (!pathB64) return '';
    try {
        const bin = atob(pathB64.replace(/ /g, '+'));
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new TextDecoder().decode(bytes);
    } catch (e) {
        return '';
    }
}

/** Right-click play icon: show context menu with "Open file location" and/or "Open on Soundeo" based on context. */
function shazamPlayContextMenu(ev, btn) {
    shazamDownloadHaveMenuClose();
    const menu = document.getElementById('shazamPlayContextMenu');
    if (!menu) return;
    menu.innerHTML = '';
    const dirB64 = (btn.dataset.dirB64 || '').trim();
    const pathB64 = (btn.dataset.pathB64 || '').trim();
    const soundeoUrl = (btn.dataset.soundeoUrl || '').trim();
    const items = [];
    let localPath = '';
    if (pathB64) localPath = shazamDecodePathB64(pathB64);
    else if (dirB64) localPath = shazamDecodePathB64(dirB64);

    if (dirB64 || pathB64) {
        items.push({ label: 'Open file location', action: function () {
            const body = pathB64 ? { path_b64: pathB64 } : { dir_b64: dirB64 };
            fetch('/api/shazam-sync/open-file-location', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
                .then(r => r.json()).then(d => {
                    if (d.error) alert(d.error);
                    else if (d.warning) alert('Open file location: ' + d.warning);
                }).catch(() => {});
        } });
        if (localPath) {
            items.push({ label: 'Copy path', action: function () {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(localPath).then(function () { /* copied */ }, function () { alert(localPath); });
                } else {
                    alert(localPath);
                }
            } });
        }
    }
    if (soundeoUrl) {
        items.push({ label: 'Open on Soundeo', action: function () { window.open(soundeoUrl, '_blank', 'noopener'); } });
    }
    if (items.length === 0) return;

    if (localPath) {
        const pathRow = document.createElement('div');
        pathRow.className = 'shazam-play-context-menu-path';
        pathRow.title = localPath;
        pathRow.textContent = localPath.length > 56 ? localPath.slice(0, 50) + '\u2026' + localPath.slice(-6) : localPath;
        menu.appendChild(pathRow);
    }
    items.forEach(function (item) {
        const span = document.createElement('button');
        span.type = 'button';
        span.className = 'shazam-play-context-menu-item';
        span.textContent = item.label;
        span.addEventListener('click', function (e) { e.preventDefault(); item.action(); shazamPlayContextMenuClose(); });
        menu.appendChild(span);
    });
    menu.style.display = 'block';
    const pad = 8;
    let x = ev.clientX;
    let y = ev.clientY;
    const rect = menu.getBoundingClientRect();
    if (x + rect.width + pad > window.innerWidth) x = window.innerWidth - rect.width - pad;
    if (y + rect.height + pad > window.innerHeight) y = window.innerHeight - rect.height - pad;
    if (x < pad) x = pad;
    if (y < pad) y = pad;
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    document.removeEventListener('click', shazamPlayContextMenuClose);
    setTimeout(function () { document.addEventListener('click', shazamPlayContextMenuClose); }, 0);
}
function shazamPlayContextMenuClose() {
    const menu = document.getElementById('shazamPlayContextMenu');
    if (menu) menu.style.display = 'none';
    document.removeEventListener('click', shazamPlayContextMenuClose);
}

/** Go to current row (center in viewport). Click once = follow mode on (row stays centered). Click again = unfollow. */
function shazamScrollToCurrentTrack(ev) {
    if (ev && ev.target && ev.target.closest && ev.target.closest('#shazamSyncStopBtn')) return;
    const key = shazamCurrentProgress && shazamCurrentProgress.current_key;
    if (!key) return;
    shazamFollowCurrentRow = !shazamFollowCurrentRow;
    const btn = document.getElementById('shazamProgressGotoBtn');
    const el = document.getElementById('shazamSyncProgress');
    if (btn) btn.textContent = shazamFollowCurrentRow ? 'Unfollow row' : 'Follow row';
    if (el) el.title = shazamFollowCurrentRow ? 'Click to unfollow row' : 'Click to follow row';
    if (shazamFollowCurrentRow) shazamScrollCurrentRowToCenter(true);
}

/** Capture current progress bar visibility and text so we can restore after re-render (e.g. row action). */
function shazamCaptureSyncProgress() {
    const el = document.getElementById('shazamSyncProgress');
    const textEl = document.getElementById('shazamProgress');
    const visible = el && el.style.display === 'flex';
    return { visible: !!visible, text: (textEl && textEl.textContent) || '' };
}

/** Restore progress bar if it was visible before a re-render, so the "Searching X of Y" cue is not lost. Skip during single-track star/unstar (the handler and poll own the bar lifecycle). */
function shazamRestoreSyncProgress(captured) {
    if (!captured || !captured.visible) return;
    if (shazamSingleBarActive) return;
    const el = document.getElementById('shazamSyncProgress');
    if (!el) return;
    if (el.classList.contains('shazam-bar-leave')) return;
    const textEl = document.getElementById('shazamProgress');
    if (el.style.display !== 'flex') shazamShowBarWithAnimation(el);
    if (textEl && captured.text) textEl.textContent = captured.text;
}


function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

async function shazamRunSync() {
    try {
        const timeRange = shazamFilterTime || 'all';
        const res = await fetch('/api/shazam-sync/run-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ time_range: timeRange })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                shazamJobQueue.push({ id: ++shazamJobId, type: 'run_soundeo', label: 'Run Soundeo', payload: { time_range: timeRange } });
                shazamRenderJobQueue();
                shazamEnsureProgressVisibleWhenQueued();
            } else {
                alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            }
            return;
        }
        shazamShowSyncProgress(data.message || 'Syncing to Soundeo…');
        shazamStartProgressPoll();
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
}

async function shazamSyncFavoritesFromSoundeo() {
    try {
        const timeRange = shazamScanRange || 'all';
        const res = await fetch('/api/shazam-sync/sync-favorites-from-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ time_range: timeRange })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                shazamJobQueue.push({ id: ++shazamJobId, type: 'sync_favorites', label: 'Sync favorites', payload: { time_range: timeRange } });
                shazamRenderJobQueue();
                shazamEnsureProgressVisibleWhenQueued();
            } else {
                alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            }
            return;
        }
        shazamShowSyncProgress('Syncing favorites from Soundeo…');
        shazamStartProgressPoll();
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamPollProgress() {
    fetch('/api/shazam-sync/progress').then(r => r.json()).then(p => {
        shazamCurrentProgress = p;
        // Apply incremental per-track updates ASAP so the list updates during the batch (not only at the end).
        // Search global reports urls/not_found/titles/scores/starred in progress payload; merge before any re-render.
        if (p && p.urls) Object.assign(shazamTrackUrls, p.urls);
        if (p && p.soundeo_titles) Object.assign(shazamSoundeoTitles, p.soundeo_titles);
        if (p && p.cover_hashes) {
            if (shazamLastData) {
                shazamLastData.cover_hashes = shazamLastData.cover_hashes || {};
                Object.assign(shazamLastData.cover_hashes, p.cover_hashes);
            }
            shazamMergeCoverHashes(p.cover_hashes);
        }
        if (p && p.soundeo_match_scores && shazamLastData) {
            shazamLastData.soundeo_match_scores = shazamLastData.soundeo_match_scores || {};
            Object.assign(shazamLastData.soundeo_match_scores, p.soundeo_match_scores);
        }
        if (p && p.starred) Object.assign(shazamStarred, p.starred);
        if (p && p.not_found) Object.assign(shazamNotFound, p.not_found);
        var hasPendingSingle = shazamAnyRowActionPending();
        /* Always sync queue bars from server (was skipped during single star/unstar → stale Star queue display). */
        shazamApplyQueueState(p.star_queue || [], p.single_search_queue || [], p.unstar_queue || []);
        if (p.download_queue && Array.isArray(p.download_queue)) {
            shazamCurrentDownloadQueue = p.download_queue;
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
        }
        // Re-render track list whenever queue state changes so row-level "Star/Search/Unstar/Download queued X/Y" and × stay in sync (skip whenever any single-track action is pending to avoid spinner/hover flicker)
        var queuesNonEmpty = (p.star_queue || []).length > 0 || (p.single_search_queue || []).length > 0 || (p.unstar_queue || []).length > 0 || (p.download_queue || []).length > 0;
        var skipRerenderForSingle = hasPendingSingle || (p.running && (p.mode === 'star_single' || p.mode === 'unstar_single'));
        if (shazamLastData && queuesNonEmpty && !skipRerenderForSingle) {
            shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
        }
        // If server is idle but client still shows many "pending" spinners, clear stuck flags.
        // This avoids a confusing state where rows spin forever even though no job/queue exists.
        const dp = p.download_progress;
        const serverIdle = !p.running && !queuesNonEmpty && !(dp && dp.running);
        // Grace window: a download just clicked hasn't had time to flip the worker to running yet.
        // Clearing it now would wipe the spinner the instant it appears. Keep download-pending during
        // the grace; only clear it once the server has been idle past the grace (genuinely stuck).
        const dlWithinGrace = (Date.now() - shazamLastDownloadClickAt) < SHAZAM_DOWNLOAD_PENDING_GRACE_MS;
        const hasActionPending = Object.keys(shazamActionPending || {}).length > 0;
        const clearDownloadPending = Object.keys(shazamPendingDownload || {}).length > 0 && !dlWithinGrace;
        if (serverIdle && (hasActionPending || clearDownloadPending)) {
            shazamBarLog('POLL', 'server idle -> clear stuck pending', { actionPending: Object.keys(shazamActionPending || {}).length, dlPending: Object.keys(shazamPendingDownload || {}).length, clearDownloadPending });
            shazamActionPending = {};
            if (clearDownloadPending) shazamPendingDownload = {};
            if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
            shazamBarUpdateActions();
        }
        const el = document.getElementById('shazamProgress');
        const stopBtn = document.getElementById('shazamSyncStopBtn');
        const doneMsg = p.stopped
            ? `Stopped. Favorited: ${p.done || 0}${shazamFailedSuffix(p.failed)}`
            : (p.error ? `Error: ${p.error}` : `Done. Favorited: ${p.done || 0}${shazamFailedSuffix(p.failed)}`);
        if (el) {
            if (p.running) {
                const label = (p.mode === 'star_batch' || p.mode === 'star_single') ? 'Starring' : (p.mode === 'unstar_single' ? 'Unstarring' : (p.mode === 'search_global' ? 'Search' : (p.mode === 'sync_favorites' ? 'Sync favorites' : (p.mode === 'sync_single' ? 'Find & star' : 'Syncing'))));
                let text = (p.mode === 'star_single') ? (p.message || 'Starring…') : (p.mode === 'unstar_single' ? (p.message || 'Unstarring…') : `${label} ${p.current || 0}/${p.total || 0}${p.message ? ' — ' + p.message : ''}`);
                if (p.mode !== 'star_single' && p.mode !== 'unstar_single' && p.last_url) {
                    const urlDisplay = p.last_url.replace(/^https?:\/\//, '');
                    text += ' — ' + urlDisplay.slice(0, 60) + (urlDisplay.length > 60 ? '…' : '');
                }
                el.textContent = text;
            } else {
                const endMsg = (p.mode === 'star_batch' || p.mode === 'star_single')
                    ? (p.stopped ? `Stopped. Starred: ${p.done || 0}${shazamFailedSuffix(p.failed)}` : (p.error ? `Error: ${p.error}` : `Done. Starred: ${p.done || 0}${shazamFailedSuffix(p.failed)}`))
                    : (p.mode === 'unstar_single' ? (p.error ? 'Error: ' + p.error : (p.message || `Done. Unstarred: ${p.done || 0}`)) : (p.mode === 'sync_favorites' ? (p.error ? 'Error: ' + p.error : (p.message || 'Done.')) : doneMsg));
                el.textContent = endMsg;
            }
        }
        var completedKey = p.key || p.current_key;
        if (!p.running && p.mode === 'star_single' && completedKey) {
            shazamClearActionPendingForKey(completedKey);
            if (p.starred === true || p.done === 1) {
                shazamSetStarredLive(completedKey, true);
                if (p.url) shazamSetUrlLive(completedKey, p.url);
            }
        }
        if (!p.running && p.mode === 'unstar_single' && completedKey) {
            shazamClearActionPendingForKey(completedKey);
            shazamSetStarredLive(completedKey, false);
        }
        shazamBarUpdateActions();
        shazamSetProgressClickable(p.running && !!p.current_key);
        if (p.running) {
            shazamProgressPollCount = (shazamProgressPollCount || 0) + 1;
            var statusPollEvery = shazamShouldThrottleTrackListRender() ? 4 : 2;
            if (shazamProgressPollCount % statusPollEvery === 1) {
                fetch('/api/shazam-sync/status').then(r => r.json()).then(data => {
                    if (data && !data.compare_running) {
                        shazamApplyStatus(data);
                    }
                }).catch(() => {});
            }
            if (shazamLastData) {
                var hasPending = shazamAnyRowActionPending();
                var skipFullRerender = hasPending || (p.mode === 'star_single' || p.mode === 'unstar_single');
                if (!skipFullRerender) {
                    shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
                }
                if (shazamFollowCurrentRow && p.current_key) shazamScrollCurrentRowToCenter(false);
            }
        }
        if (p.urls) {
            Object.assign(shazamTrackUrls, p.urls);
        }
        // When no sync/search running but download queue has items, start the download worker (e.g. after Search finishes)
        if (!p.running && !shazamSingleBarActive && (p.download_queue || []).length > 0 && !(dp && dp.running)) {
            fetch('/api/shazam-sync/download-start-next', { method: 'POST' }).then(r => r.json()).then(function (d) {
                if (d.started) {
                    shazamShowSyncProgress('Downloading…');
                    shazamStartDownloadPoll();
                }
            }).catch(function () {});
        }
        if (p.starred) {
            Object.assign(shazamStarred, p.starred);
        }
        var starQueueEmpty = (p.star_queue || []).length === 0;
        var unstarQueueEmpty = (p.unstar_queue || []).length === 0;
        if (!p.running && shazamProgressInterval) {
            if ((p.mode === 'star_single' && !starQueueEmpty) || (p.mode === 'unstar_single' && !unstarQueueEmpty)) {
                // Queue still has items (next will start); keep polling
            } else {
                shazamFollowCurrentRow = false;
                shazamCurrentProgress = {};
                shazamProgressPollCount = 0;
                clearInterval(shazamProgressInterval);
                shazamProgressInterval = null;
                if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopped'; }
                const gotoBtn = document.getElementById('shazamProgressGotoBtn');
                if (gotoBtn) gotoBtn.textContent = 'Follow row';
                var isSingleStarUnstar = p.mode === 'star_single' || p.mode === 'unstar_single';
                if (isSingleStarUnstar) {
                    setTimeout(function () {
                        shazamHideSyncProgress();
                        shazamLoadStatus().finally(function () {
                            shazamSingleBarActive = false;
                            shazamBarLog('SINGLE_BAR', 'lifecycle complete, flag cleared');
                        });
                        shazamMaybeStartQueuedJob();
                    }, 1800);
                } else {
                    shazamHideSyncProgress();
                    shazamLoadStatus();
                    shazamMaybeStartQueuedJob();
                }
            }
        }
    }).catch(() => {});
}

function switchTab(tabId) {
    const panels = document.querySelectorAll('.tab-panel');
    const buttons = document.querySelectorAll('.tab-btn');
    const targetPanel = document.getElementById('tab-panel-' + tabId);
    const targetBtn = document.getElementById('tab-btn-' + tabId);
    if (!targetPanel || !targetBtn) return;
    panels.forEach(p => {
        p.classList.toggle('active', p.id === 'tab-panel-' + tabId);
    });
    buttons.forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tabId);
        b.setAttribute('aria-selected', b.dataset.tab === tabId ? 'true' : 'false');
    });
    const queueBubble = document.getElementById('shazamQueueBarsFixed');
    if (queueBubble) {
        if (tabId === 'shazam') {
            shazamUpdateBatchJobsSectionVisibility();
        } else {
            queueBubble.style.display = 'none';
        }
    }
    if (tabId === 'sets') {
        setsLoad();
        setsStatePollStart();
    } else {
        setsStatePollStop();
    }
    saveAppStateToStorage({ active_tab: tabId });
}

// ========== Tracklist sets tab (paste URL → scraped tracks per set) ==========

let setsCache = [];
const SETS_COLLAPSE_KEY = 'mp3cleaner_sets_collapsed';

function _setsCollapsed() {
    try { return JSON.parse(localStorage.getItem(SETS_COLLAPSE_KEY) || '{}'); } catch (e) { return {}; }
}

function _setsSetCollapsed(id, val) {
    const c = _setsCollapsed();
    if (val) c[id] = true; else delete c[id];
    try { localStorage.setItem(SETS_COLLAPSE_KEY, JSON.stringify(c)); } catch (e) { /* ignore */ }
}

async function setsLoad() {
    const list = document.getElementById('setsList');
    if (!list) return;
    try {
        const res = await fetch('/api/sets');
        const data = await res.json();
        setsCache = data.sets || [];
        setsRender();
    } catch (e) {
        list.innerHTML = '<p class="shazam-info-msg shazam-warning">Could not load sets: ' + escapeHtml(e.message || String(e)) + '</p>';
    }
}

function _setsLookup(map, key) {
    if (!map || !key) return undefined;
    try {
        const vks = shazamKeyVariants(key);
        for (let i = 0; i < vks.length; i++) {
            if (vks[i] && map[vks[i]]) return map[vks[i]];
        }
    } catch (e) {
        return map[key] || map[key.toLowerCase()];
    }
    return undefined;
}

/** Per-track state for a Sets row, derived from the same live maps the Sync
 * tab uses (variant-aware): Soundeo url, starred, already-downloaded. */
function _setsTrackState(artist, title) {
    const key = `${artist} - ${title}`;
    const kl = key.toLowerCase();
    const url = _setsLookup(shazamTrackUrls, key) || null;
    const starred = !!_setsLookup(shazamStarred, key);
    let have = false, shazammed = false;
    const d = shazamLastData || {};
    for (const t of (d.have_locally || [])) {
        if ((`${t.artist} - ${t.title}`).toLowerCase() === kl) { have = true; shazammed = true; break; }
    }
    if (!shazammed) {
        for (const t of [...(d.to_download || []), ...(d.skipped_tracks || [])]) {
            if ((`${t.artist} - ${t.title}`).toLowerCase() === kl) { shazammed = true; break; }
        }
    }
    const liked = !!_setsLookup(shazamMaybe, key);
    const pending = !!(shazamActionPending[key] || shazamActionPending[kl]);
    const downloadPending = !!(shazamPendingDownload[key] || shazamPendingDownload[kl]);
    return { key, url, starred, have, shazammed, liked, pending, downloadPending };
}

// Light state refresh while the Sets tab is open: actions (search/star/download)
// resolve asynchronously via the existing Sync machinery; polling /status keeps
// the Sets rows' buttons (link found → ▶/★/⬇ unlock, star fills, ⬇ becomes ✓)
// in sync without the user leaving the tab. Stopped when the tab is left.
let _setsStatePollInterval = null;
function setsStatePollStart() {
    if (_setsStatePollInterval) return;
    _setsStatePollInterval = setInterval(async () => {
        const panel = document.getElementById('tab-panel-sets');
        if (!panel || !panel.classList.contains('active')) { setsStatePollStop(); return; }
        try {
            const res = await fetch('/api/shazam-sync/status');
            if (!res.ok) return;
            const data = await res.json();
            if (data.urls) Object.assign(shazamTrackUrls, data.urls);
            if (data.starred) Object.assign(shazamStarred, data.starred);
            if (data.maybe && typeof data.maybe === 'object') { shazamMaybe = Object.assign({}, data.maybe); }
            if (data.cover_hashes) shazamMergeCoverHashes(data.cover_hashes);
            shazamLastData = shazamLastData || {};
            if (data.have_locally) shazamLastData.have_locally = data.have_locally;
            if (data.to_download) shazamLastData.to_download = data.to_download;
            // Re-render only when the state relevant to the visible rows actually
            // changed — an unconditional rebuild every 5s would wipe the playing
            // ▶/⏸ state and hover, and cause needless churn.
            if (_setsStateFingerprint() !== _setsLastFingerprint) setsRender();
        } catch (e) { /* transient */ }
    }, 5000);
}
function setsStatePollStop() {
    if (_setsStatePollInterval) { clearInterval(_setsStatePollInterval); _setsStatePollInterval = null; }
}

function setsRender() {
    const list = document.getElementById('setsList');
    if (!list) return;
    if (!setsCache.length) {
        list.innerHTML = '<p class="shazam-info-msg">No sets yet. Paste a URL above to scrape your first tracklist.</p>';
        return;
    }
    const collapsed = _setsCollapsed();
    let html = '';
    for (const s of setsCache) {
        const isCollapsed = !!collapsed[s.id];
        const when = s.created_at ? new Date(s.created_at * 1000).toLocaleDateString() : '';
        html += '<div class="sets-card" data-set-id="' + escapeHtml(s.id) + '">';
        html += '<div class="sets-card-head" onclick="setsToggleCollapse(\'' + escapeHtml(s.id) + '\')">';
        html += '<span class="sets-card-caret">' + (isCollapsed ? '▸' : '▾') + '</span>';
        html += '<strong class="sets-card-title">' + escapeHtml(s.title || s.url) + '</strong>';
        html += '<span class="sets-card-meta">' + escapeHtml(String(s.track_count || (s.tracks || []).length)) + ' tracks · ' + escapeHtml(s.source || '') + (when ? ' · ' + escapeHtml(when) : '') + '</span>';
        html += '<span class="sets-card-actions">';
        if (s.stream_url) {
            const kindLabel = setsStreamKind(s.stream_url) === 'youtube' ? 'YouTube' : (setsStreamKind(s.stream_url) === 'soundcloud' ? 'SoundCloud' : 'stream');
            html += '<button type="button" class="btn btn-small btn-primary" onclick="event.stopPropagation(); setsPlaySet(\'' + escapeHtml(s.id) + '\')" title="Play the full set audio (' + kindLabel + ')">▶ Play set</button> ';
        }
        html += '<a href="' + escapeHtml(s.url) + '" target="_blank" rel="noopener" class="btn btn-small" onclick="event.stopPropagation()" title="Open source page">Source</a> ';
        html += '<button type="button" class="btn btn-small" onclick="event.stopPropagation(); setsRefresh(\'' + escapeHtml(s.id) + '\')" title="Re-scrape this URL">Refresh</button> ';
        html += '<button type="button" class="btn btn-small" onclick="event.stopPropagation(); setsDelete(\'' + escapeHtml(s.id) + '\')" title="Remove this set">✕</button>';
        html += '</span></div>';
        if (!isCollapsed) {
            html += '<table class="shazam-track-table sets-track-table"><thead><tr><th style="width:36px;">#</th><th style="width:72px;">Time</th><th>Artist</th><th>Title</th><th style="width:36px;"></th><th style="width:130px;">Actions</th></tr></thead><tbody>';
            const safeAttr = v => escapeHtml(String(v == null ? '' : v)).replace(/'/g, '&#39;');
            const inactive = ' shazam-row-action-inactive';
            (s.tracks || []).forEach((t, i) => {
                const st = _setsTrackState(t.artist, t.title);
                const isIdTrack = /^id$/i.test((t.artist || '').trim()) && /^id$/i.test((t.title || '').trim());
                // Fixed-width slot whether or not there's a dot — otherwise rows
                // with/without status dots get ragged left edges.
                const dot = st.have
                    ? '<span class="status-dot status-have" title="Already in your local library"></span>'
                    : (st.url
                        ? '<span class="status-dot status-found" title="Found on Soundeo"></span>'
                        : (st.shazammed ? '<span class="status-dot status-no-link" title="Also in your Shazam list (no Soundeo link yet)"></span>' : ''));
                const badge = '<span class="sets-dot-slot">' + dot + '</span>';
                const label = (t.artist ? t.artist + ' - ' : '') + (t.title || '');

                // ▶ preview — only when a Soundeo link exists; reuses the global
                // playbar + stream-preview machinery (and auto-advances down the set).
                const playCell = st.url
                    ? '<td class="shazam-play-col"><button type="button" class="shazam-play-btn shazam-soundeo-play" data-track-key="' + safeAttr(st.key) + '" data-artist="' + safeAttr(t.artist) + '" data-title="' + safeAttr(t.title) + '" data-soundeo-url="' + safeAttr(st.url) + '" data-track-label="' + safeAttr(label) + '" onclick="shazamToggleSoundeoPlay(this)" title="Stream Soundeo preview">' + PLAY_ICON_ROW + '</button></td>'
                    : '<td class="shazam-play-col"><button type="button" class="shazam-play-btn shazam-row-action-inactive" disabled title="Search first to find this track on Soundeo">' + PLAY_ICON_ROW + '</button></td>';

                // ★ star / unstar — same data-action contract as Sync rows; the
                // global click handler routes to the existing queue functions.
                const starSvg = st.starred
                    ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>'
                    : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>';
                // Unmatched rows: the star slot is a ❤ "like" — pre-star while
                // skimming. Liking adds the track to the Sync list, queues a
                // Soundeo search, and auto-converts to a real ★ when found.
                const heartSvg = st.liked
                    ? '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>'
                    : '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>';
                const starBtn = st.pending
                    ? '<button type="button" class="shazam-row-action-btn" disabled aria-busy="true" title="Working…"><span class="shazam-btn-spinner" role="status" aria-label="Working"></span></button>'
                    : st.url
                    ? '<button type="button" class="shazam-row-action-btn" data-action="' + (st.starred ? 'unstar' : 'star') + '" data-key="' + safeAttr(st.key) + '" ' + (st.starred ? 'data-url' : 'data-track-url') + '="' + safeAttr(st.url) + '" data-artist="' + safeAttr(t.artist) + '" data-title="' + safeAttr(t.title) + '" title="' + (st.starred ? 'Remove from Soundeo favorites' : 'Add to Soundeo favorites') + '">' + starSvg + '</button>'
                    : isIdTrack
                    ? '<button type="button" class="shazam-row-action-btn' + inactive + '" disabled title="Unknown track (ID) — listen via the timestamp; nothing to search">' + heartSvg + '</button>'
                    : '<button type="button" class="shazam-row-action-btn sets-like-btn' + (st.liked ? ' sets-liked' : '') + '" onclick="setsLikeTrack(this)" data-artist="' + safeAttr(t.artist) + '" data-title="' + safeAttr(t.title) + '" data-liked="' + (st.liked ? '1' : '0') + '" title="' + (st.liked ? 'Liked — searching Soundeo; auto-stars when found. Click to unlike.' : 'Like: add to Sync list, search Soundeo, auto-star when found') + '">' + heartSvg + '</button>';

                // ⬇ download — via the existing one-by-one download queue; ✓ when already local.
                const dlSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
                const dlBtn = st.downloadPending
                    ? '<button type="button" class="shazam-row-action-btn" disabled aria-busy="true" title="Downloading…"><span class="shazam-btn-spinner" role="status" aria-label="Downloading"></span></button>'
                    : st.have
                    ? '<button type="button" class="shazam-row-action-btn' + inactive + '" disabled title="Already in your local library">✓</button>'
                    : (st.url
                        ? '<button type="button" class="shazam-row-action-btn" data-action="download" data-key="' + safeAttr(st.key) + '" title="Download AIFF from Soundeo">' + dlSvg + '</button>'
                        : '<button type="button" class="shazam-row-action-btn' + inactive + '" disabled title="Search first">' + dlSvg + '</button>');

                // 🔍 search — always available (also re-search to refresh a link).
                const searchSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
                const searchBtn = isIdTrack
                    ? '<button type="button" class="shazam-row-action-btn' + inactive + '" disabled title="Unknown track (ID) — nothing to search">' + searchSvg + '</button>'
                    : '<button type="button" class="shazam-row-action-btn" data-action="search" data-key="' + safeAttr(st.key) + '" data-artist="' + safeAttr(t.artist) + '" data-title="' + safeAttr(t.title) + '" title="Search on Soundeo (find link, no favorite)">' + searchSvg + '</button>';

                // Timestamp: when the set has a playable stream, the time becomes a
                // "jump into the actual mix here" button — skim without Soundeo.
                const timeCell = (s.stream_url && t.start_time)
                    ? '<td class="shazam-when"><button type="button" class="sets-time-jump" onclick="setsPlayAt(\'' + escapeHtml(s.id) + '\', \'' + escapeHtml(t.start_time) + '\')" title="Play the set from ' + escapeHtml(t.start_time) + '">▶ ' + escapeHtml(t.start_time) + '</button></td>'
                    : '<td class="shazam-when">' + escapeHtml(t.start_time || '') + '</td>';
                const startSec = t.start_time ? _setsParseTimeToSec(t.start_time) : -1;
                html += '<tr data-track-key="' + safeAttr(st.key) + '" data-start-sec="' + startSec + '"><td>' + (i + 1) + '</td>' + timeCell;
                html += '<td>' + badge + escapeHtml(t.artist || '—') + '</td><td>' + escapeHtml(t.title || '—') + '</td>';
                html += playCell;
                html += '<td class="shazam-actions-col">' + starBtn + ' ' + dlBtn + ' ' + searchBtn + '</td></tr>';
            });
            html += '</tbody></table>';
        }
        html += '</div>';
    }
    list.innerHTML = html;
    _setsLastFingerprint = _setsStateFingerprint();
    // Reattach playing state after the innerHTML rebuild (same pattern as the
    // Sync renderer): the audio element keeps playing independently; the row's
    // ▶ must show ⏸ again and shazamPlayingBtn must point at the NEW button so
    // pause/auto-advance keep working.
    if (shazamCurrentlyPlaying) {
        const btns = list.querySelectorAll('.shazam-play-btn[data-soundeo-url]');
        for (const b of btns) {
            if (b.dataset.soundeoUrl === shazamCurrentlyPlaying) {
                b.innerHTML = PAUSE_ICON_ROW;
                b.classList.add('playing');
                shazamPlayingBtn = b;
                break;
            }
        }
    }
}

/** Fingerprint of the per-track state shown in the Sets rows (url/star/have)
 * so the poll can skip no-op re-renders. */
let _setsLastFingerprint = '';
function _setsStateFingerprint() {
    const parts = [];
    for (const s of setsCache) {
        for (const t of (s.tracks || [])) {
            const st = _setsTrackState(t.artist, t.title);
            parts.push((st.url ? '1' : '0') + (st.starred ? '1' : '0') + (st.have ? '1' : '0') + (st.liked ? '1' : '0') + (st.pending ? '1' : '0') + (st.downloadPending ? '1' : '0'));
        }
    }
    return parts.join('');
}

function setsToggleCollapse(id) {
    _setsSetCollapsed(id, !_setsCollapsed()[id]);
    setsRender();
}

async function setsAddUrl(forcedUrl) {
    const input = document.getElementById('setsUrlInput');
    const btn = document.getElementById('setsAddBtn');
    const errEl = document.getElementById('setsError');
    const url = (forcedUrl || (input ? input.value : '') || '').trim();
    if (!url) return;
    if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
    if (btn) { btn.disabled = true; btn.textContent = 'Scraping…'; }
    try {
        const res = await fetch('/api/sets/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || ('Scrape failed (HTTP ' + res.status + ')'));
        setsCache = data.sets || [];
        if (input && !forcedUrl) input.value = '';
        setsRender();
    } catch (e) {
        if (errEl) { errEl.textContent = e.message || String(e); errEl.style.display = 'block'; }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Add set'; }
    }
}

function setsRefresh(id) {
    const s = setsCache.find(x => x.id === id);
    if (s) setsAddUrl(s.url);
}

async function setsDelete(id) {
    const s = setsCache.find(x => x.id === id);
    if (!s) return;
    if (!confirm('Remove set "' + (s.title || s.url) + '"?')) return;
    try {
        const res = await fetch('/api/sets/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id })
        });
        const data = await res.json().catch(() => ({}));
        setsCache = data.sets || [];
        setsRender();
    } catch (e) { alert('Delete failed: ' + (e.message || e)); }
}

// (Per-track Search now goes through the shared [data-action="search"] handler,
// same queue/progress semantics as Sync rows. The 5s state poll refreshes the
// row when the link lands, unlocking ▶/★/⬇.)

// ========== Set audio player (play the ACTUAL mix, seek to timestamps) =======
// "Skim mode": before searching/scraping Soundeo at all, play the set's source
// stream (SoundCloud / YouTube — captured at scrape time as set.stream_url) and
// jump to any track via its timestamp, like trackid.net / 1001tracklists do.

let _setsActivePlayer = null;  // { setId, kind, iframe, scWidget }
let _setsSCApiPromise = null;

function setsStreamKind(url) {
    const u = (url || '').toLowerCase();
    if (u.includes('soundcloud.com')) return 'soundcloud';
    if (u.includes('youtube.com') || u.includes('youtu.be')) return 'youtube';
    return u ? 'other' : '';
}

function _setsParseTimeToSec(t) {
    const parts = String(t || '').trim().split(':').map(Number);
    if (parts.some(isNaN) || !parts.length) return 0;
    return parts.reduce((acc, v) => acc * 60 + v, 0);
}

function _setsLoadSCApi() {
    if (window.SC && window.SC.Widget) return Promise.resolve();
    if (_setsSCApiPromise) return _setsSCApiPromise;
    _setsSCApiPromise = new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = 'https://w.soundcloud.com/player/api.js';
        s.onload = () => resolve();
        s.onerror = () => { _setsSCApiPromise = null; reject(new Error('SoundCloud player API failed to load')); };
        document.head.appendChild(s);
    });
    return _setsSCApiPromise;
}

function _setsPauseSoundeoPreview() {
    // One audio source at a time: stop a running Soundeo preview / local play
    // before the set stream starts.
    try {
        if (shazamAudioEl) { shazamAudioEl.pause(); }
        const bar = document.getElementById('shazamPlayerBar');
        if (bar && bar.style.display !== 'none') shazamPlayerBarHide();
    } catch (e) { /* ignore */ }
}

async function setsPlaySet(setId, seekSec) {
    const s = setsCache.find(x => x.id === setId);
    if (!s || !s.stream_url) return;
    const kind = setsStreamKind(s.stream_url);
    if (kind === 'other') { window.open(s.stream_url, '_blank', 'noopener'); return; }
    _setsPauseSoundeoPreview();

    const bar = document.getElementById('setsPlayerBar');
    const host = document.getElementById('setsPlayerHost');
    const titleEl = document.getElementById('setsPlayerTitle');
    if (!bar || !host) return;

    // Same set already loaded → just seek (keeps playback running seamlessly).
    if (_setsActivePlayer && _setsActivePlayer.setId === setId) {
        if (seekSec != null) setsSeek(seekSec);
        return;
    }

    bar.style.display = 'block';
    if (titleEl) titleEl.textContent = s.title || s.url;
    if (_setsActivePlayer && _setsActivePlayer.posTimer) clearInterval(_setsActivePlayer.posTimer);
    setsClearHighlight();
    host.innerHTML = '';
    _setsActivePlayer = { setId, kind, iframe: null, scWidget: null, posTimer: null };

    const iframe = document.createElement('iframe');
    iframe.setAttribute('allow', 'autoplay; encrypted-media');
    iframe.style.cssText = 'width:100%;border:0;display:block;';
    if (kind === 'soundcloud') {
        iframe.style.height = '120px';
        iframe.src = 'https://w.soundcloud.com/player/?url=' + encodeURIComponent(s.stream_url) +
            '&auto_play=true&visual=false&show_teaser=false&hide_related=true';
        host.appendChild(iframe);
        _setsActivePlayer.iframe = iframe;
        try {
            await _setsLoadSCApi();
            const w = SC.Widget(iframe);
            _setsActivePlayer.scWidget = w;
            w.bind(SC.Widget.Events.READY, function () {
                // Pending seek: PLAY_PROGRESS is the reliable trigger when the
                // stream autoplays, but it never fires if autoplay is blocked —
                // so the position poller below ALSO retries the seek until the
                // reported position converges on the target.
                let pendingSeek = (seekSec != null && seekSec > 0) ? seekSec : null;
                let seekTries = 0;
                w.bind(SC.Widget.Events.PLAY_PROGRESS, function () {
                    if (pendingSeek != null) { w.seekTo(pendingSeek * 1000); }
                });
                if (_setsActivePlayer && _setsActivePlayer.scWidget === w) {
                    _setsActivePlayer.posTimer = setInterval(function () {
                        try {
                            w.getPosition(function (pos) {
                                const posSec = pos / 1000;
                                if (pendingSeek != null) {
                                    if (Math.abs(posSec - pendingSeek) < 3) {
                                        pendingSeek = null;  // converged
                                    } else if (seekTries < 10) {
                                        seekTries++;
                                        w.seekTo(pendingSeek * 1000);
                                    } else {
                                        pendingSeek = null;  // give up, follow reality
                                    }
                                }
                                setsHighlightPosition(posSec);
                            });
                        } catch (e) { /* widget gone */ }
                    }, 1000);
                }
            });
        } catch (e) {
            console.warn('SC widget API unavailable — playback works, seek disabled', e);
        }
    } else { // youtube
        iframe.style.height = '200px';
        const idm = s.stream_url.match(/(?:watch\?v=|embed\/|youtu\.be\/)([a-zA-Z0-9_-]{6,15})/);
        const vid = idm ? idm[1] : '';
        iframe.src = 'https://www.youtube.com/embed/' + vid + '?autoplay=1&enablejsapi=1' +
            (seekSec ? '&start=' + Math.floor(seekSec) : '');
        host.appendChild(iframe);
        _setsActivePlayer.iframe = iframe;
        iframe.addEventListener('load', function () {
            try {
                iframe.contentWindow.postMessage(JSON.stringify({ event: 'listening', id: 'setsPlayer' }), '*');
            } catch (e) { /* ignore */ }
        });
    }
}

function setsSeek(seekSec) {
    const p = _setsActivePlayer;
    if (!p) return;
    if (p.kind === 'soundcloud' && p.scWidget) {
        p.scWidget.seekTo(Math.max(0, seekSec) * 1000);
        p.scWidget.play();
    } else if (p.kind === 'youtube' && p.iframe && p.iframe.contentWindow) {
        p.iframe.contentWindow.postMessage(JSON.stringify({
            event: 'command', func: 'seekTo', args: [Math.max(0, seekSec), true]
        }), '*');
        p.iframe.contentWindow.postMessage(JSON.stringify({
            event: 'command', func: 'playVideo', args: []
        }), '*');
    }
}

function setsPlayAt(setId, timeStr) {
    setsPlaySet(setId, _setsParseTimeToSec(timeStr));
}

let _setsHighlightLastAt = 0;
/** Highlight the row whose time window contains the set player's position.
 * Called ~1/s from SC PLAY_PROGRESS / YT infoDelivery. Re-applied every tick,
 * so a poll re-render self-heals within a second. */
function setsHighlightPosition(posSec) {
    const p = _setsActivePlayer;
    if (!p) return;
    const now = Date.now();
    if (now - _setsHighlightLastAt < 900) return;
    _setsHighlightLastAt = now;
    const card = document.querySelector('.sets-card[data-set-id="' + p.setId + '"]');
    if (!card) return;
    const rows = card.querySelectorAll('tr[data-track-key]');
    let active = null;
    for (const r of rows) {
        const s = parseInt(r.dataset.startSec || '-1', 10);
        if (isNaN(s) || s < 0) continue;
        if (s <= posSec) active = r; else break;
    }
    rows.forEach(r => r.classList.toggle('sets-row-playing', r === active));
}

function setsClearHighlight() {
    document.querySelectorAll('.sets-row-playing').forEach(r => r.classList.remove('sets-row-playing'));
}

// YouTube embeds report currentTime via infoDelivery postMessages once we ask.
window.addEventListener('message', function (ev) {
    const p = _setsActivePlayer;
    if (!p || p.kind !== 'youtube' || !p.iframe || ev.source !== p.iframe.contentWindow) return;
    try {
        const data = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
        const t = data && data.info && data.info.currentTime;
        if (typeof t === 'number') setsHighlightPosition(t);
    } catch (e) { /* not ours */ }
});

function setsPlayerStop() {
    const bar = document.getElementById('setsPlayerBar');
    const host = document.getElementById('setsPlayerHost');
    if (_setsActivePlayer && _setsActivePlayer.posTimer) clearInterval(_setsActivePlayer.posTimer);
    if (host) host.innerHTML = '';   // removing the iframe stops the audio
    if (bar) bar.style.display = 'none';
    _setsActivePlayer = null;
    setsClearHighlight();
}

async function setsLikeTrack(btn) {
    if (btn.disabled) return;
    btn.disabled = true;
    btn.innerHTML = '<span class="shazam-btn-spinner" role="status" aria-label="Saving"></span>';
    const artist = btn.dataset.artist || '';
    const title = btn.dataset.title || '';
    const nowLiked = btn.dataset.liked !== '1';
    const key = `${artist} - ${title}`;
    // Optimistic UI; the 5s poll reconciles with the server (and later flips
    // the row to ★ once the search lands and the like converts to a star).
    if (nowLiked) shazamMaybe[key] = true; else delete shazamMaybe[key];
    setsRender();
    try {
        const res = await fetch('/api/sets/like', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title, liked: nowLiked })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Like failed');
    } catch (e) {
        if (nowLiked) delete shazamMaybe[key]; else shazamMaybe[key] = true;
        setsRender();
        alert('Like failed: ' + (e.message || e));
    }
}

/** Pause (not close) the set player — called when a per-track preview or local
 * file starts, so only one audio source plays at a time. */
function setsPlayerPause() {
    const p = _setsActivePlayer;
    if (!p) return;
    try {
        if (p.kind === 'soundcloud' && p.scWidget) p.scWidget.pause();
        else if (p.kind === 'youtube' && p.iframe && p.iframe.contentWindow) {
            p.iframe.contentWindow.postMessage(JSON.stringify({ event: 'command', func: 'pauseVideo', args: [] }), '*');
        }
    } catch (e) { /* ignore */ }
}

function showConnectionBanner() {
    const el = document.getElementById('connectionBanner');
    if (el) el.style.display = 'block';
}

function hideConnectionBanner() {
    const el = document.getElementById('connectionBanner');
    if (el) el.style.display = 'none';
}

document.addEventListener('DOMContentLoaded', function () {
    try {
        if (window.location.protocol === 'file:') {
            showConnectionBanner();
        } else {
            fetch('/api/app-state', { method: 'GET' }).then(function (res) {
                if (res.ok) hideConnectionBanner();
                else showConnectionBanner();
            }).catch(function () {
                showConnectionBanner();
            });
        }
        restoreAppState();
        var savedTab = loadAppStateFromStorage().active_tab;
        var tabToShow = (savedTab === 'shazam' || savedTab === 'mp3' || savedTab === 'sets') ? savedTab : 'shazam';
        switchTab(tabToShow);
        var tabBtns = document.querySelectorAll('.tab-btn');
        for (var i = 0; i < tabBtns.length; i++) {
            (function (btn) {
                btn.addEventListener('click', function () { switchTab(btn.dataset.tab); });
            })(tabBtns[i]);
        }
        var folderInput = document.getElementById('folderPath');
        if (folderInput) {
            folderInput.addEventListener('blur', function () {
                var path = (folderInput.value || '').trim();
                saveAppStateToStorage({ last_folder_path: path });
            });
        }
        shazamBootstrapLoad();
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') shazamClearPointerEventNudgeResiduals();
        });
        window.shazamClearPointerEventNudgeResiduals = shazamClearPointerEventNudgeResiduals;
        var favoritesDropdownWrap = document.querySelector('.favorites-dropdown-wrap');
        var favoritesDropdownBtn = document.getElementById('shazamFavoritesDropdownBtn');
        var favoritesDropdownMenu = document.getElementById('shazamFavoritesDropdownMenu');
        if (favoritesDropdownBtn && favoritesDropdownMenu) {
            favoritesDropdownBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (favoritesDropdownWrap) favoritesDropdownWrap.classList.toggle('open');
                favoritesDropdownBtn.setAttribute('aria-expanded', (favoritesDropdownWrap && favoritesDropdownWrap.classList.contains('open')) ? 'true' : 'false');
            });
            favoritesDropdownMenu.querySelectorAll('.search-dropdown-item[data-scan-range]').forEach(function (item) {
                item.addEventListener('click', function (e) {
                    e.stopPropagation();
                    if (favoritesDropdownWrap) favoritesDropdownWrap.classList.remove('open');
                    favoritesDropdownBtn.setAttribute('aria-expanded', 'false');
                    shazamScanRange = item.dataset.scanRange || 'all';
                    shazamSyncFavoritesFromSoundeo();
                });
            });
        }
        var rescanDropdownWrap = document.querySelector('.rescan-dropdown-wrap');
        var rescanDropdownBtn = document.getElementById('shazamRescanDropdownBtn');
        var rescanDropdownMenu = document.getElementById('shazamRescanDropdownMenu');
        if (rescanDropdownBtn && rescanDropdownMenu) {
            rescanDropdownBtn.addEventListener('click', function (e) {
                e.stopPropagation();
                if (rescanDropdownWrap) rescanDropdownWrap.classList.toggle('open');
                rescanDropdownBtn.setAttribute('aria-expanded', (rescanDropdownWrap && rescanDropdownWrap.classList.contains('open')) ? 'true' : 'false');
            });
            rescanDropdownMenu.querySelectorAll('.search-dropdown-item[data-rescan-mode]').forEach(function (item) {
                item.addEventListener('click', function (e) {
                    e.stopPropagation();
                    if (rescanDropdownWrap) rescanDropdownWrap.classList.remove('open');
                    rescanDropdownBtn.setAttribute('aria-expanded', 'false');
                    var mode = item.dataset.rescanMode;
                    if (mode === 'match_only') {
                        shazamCompare();
                    } else {
                        shazamRescan(mode === 'compare');
                    }
                });
            });
        }
    } catch (err) {
        console.error('SoundBridge init error:', err);
        showConnectionBanner();
    }
    (function () {
        try {
            const saved = localStorage.getItem(SHAZAM_FILTER_STATUS_KEY);
            if (saved && SHAZAM_FILTER_STATUS_VALUES.includes(saved)) {
                shazamFilterStatus = saved;
                document.querySelectorAll('.shazam-filter-btn[data-status]').forEach(b => {
                    b.classList.toggle('active', b.dataset.status === saved);
                });
            }
        } catch (e) { /* ignore */ }
    })();
    document.querySelectorAll('.shazam-filter-btn[data-status]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.shazam-filter-btn[data-status]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            shazamFilterStatus = btn.dataset.status;
            try {
                localStorage.setItem(SHAZAM_FILTER_STATUS_KEY, shazamFilterStatus);
            } catch (e) { /* ignore */ }
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    });
    document.querySelectorAll('.shazam-filter-time-btn[data-time-range]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.shazam-filter-time-btn[data-time-range]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            shazamFilterTime = btn.dataset.timeRange || 'all';
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    });
    const trackSearchInput = document.getElementById('shazamTrackSearch');
    const trackSearchClearBtn = document.getElementById('shazamTrackSearchClear');
    function shazamUpdateSearchClearVisibility() {
        if (trackSearchClearBtn) trackSearchClearBtn.style.display = (trackSearchInput && trackSearchInput.value.trim()) ? '' : 'none';
    }
    if (trackSearchInput) {
        trackSearchInput.addEventListener('input', () => {
            shazamFilterSearch = trackSearchInput.value;
            shazamUpdateSearchClearVisibility();
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    }
    if (trackSearchClearBtn && trackSearchInput) {
        trackSearchClearBtn.addEventListener('click', () => {
            trackSearchInput.value = '';
            shazamFilterSearch = '';
            shazamUpdateSearchClearVisibility();
            trackSearchInput.focus();
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    }
    var searchDropdownWrap = document.querySelector('.search-dropdown-wrap');
    var searchDropdownBtn = document.getElementById('shazamSearchDropdownBtn');
    var searchDropdownMenu = document.getElementById('shazamSearchDropdownMenu');
    if (searchDropdownBtn && searchDropdownMenu) {
        searchDropdownBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            if (searchDropdownWrap) searchDropdownWrap.classList.toggle('open');
            searchDropdownBtn.setAttribute('aria-expanded', (searchDropdownWrap && searchDropdownWrap.classList.contains('open')) ? 'true' : 'false');
        });
        searchDropdownMenu.querySelectorAll('.search-dropdown-item').forEach(function (item) {
            item.addEventListener('click', function (e) {
                e.stopPropagation();
                if (searchDropdownWrap) searchDropdownWrap.classList.remove('open');
                searchDropdownBtn.setAttribute('aria-expanded', 'false');
                const mode = item.dataset.mode;
                if (mode) shazamSearchAllOnSoundeo(mode);
            });
        });
    }
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-dropdown-wrap') && !e.target.closest('.favorites-dropdown-wrap') && !e.target.closest('.rescan-dropdown-wrap')) {
            if (searchDropdownWrap) {
                searchDropdownWrap.classList.remove('open');
                if (searchDropdownBtn) searchDropdownBtn.setAttribute('aria-expanded', 'false');
            }
            if (favoritesDropdownWrap) {
                favoritesDropdownWrap.classList.remove('open');
                if (favoritesDropdownBtn) favoritesDropdownBtn.setAttribute('aria-expanded', 'false');
            }
            if (rescanDropdownWrap) {
                rescanDropdownWrap.classList.remove('open');
                if (rescanDropdownBtn) rescanDropdownBtn.setAttribute('aria-expanded', 'false');
            }
        }
    });
    document.addEventListener('click', (e) => {
        const removeQueueBtn = e.target.closest('.shazam-remove-queue');
        if (removeQueueBtn) {
            shazamRemoveFromQueue(removeQueueBtn);
            return;
        }
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        if (btn.disabled) return;
        if (btn.classList.contains('shazam-row-action-inactive')) return;
        const action = btn.dataset.action;
        if (action === 'unstar') {
            shazamUnstarTrack(btn.dataset.key, btn.dataset.url, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'dismiss') {
            shazamDismissTrack(btn.dataset.key, btn.dataset.url, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'undismiss') {
            shazamUndismissTrack(btn.dataset.key, btn.dataset.url, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'clear_dismissed') {
            shazamClearDismissed(btn.dataset.key);
        } else if (action === 'skip') {
            shazamSkipSingleTrack(btn.dataset.artist, btn.dataset.title);
        } else if (action === 'sync') {
            shazamSyncSingleTrack(btn.dataset.key, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'search') {
            shazamSearchSingleOnSoundeo(btn.dataset.key, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'star') {
            shazamStarTrack(btn.dataset.key, btn.dataset.trackUrl, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'download') {
            if (btn.classList.contains('shazam-download-have') && !btn.classList.contains('shazam-download-action-pending')) {
                shazamDownloadHaveMenuFromButton(e, btn);
                return;
            }
            shazamDownloadTrack(btn.dataset.key);
        } else if (action === 'maybe') {
            shazamToggleMaybe(btn);
        }
        if (btn.closest('.sets-track-table') && typeof setsRender === 'function') {
            setsRender();
        }
    });
});

/** Toggle the local-only "Maybe" flag for a track. Optimistic update + POST.
 *  NEVER touches Soundeo. The button's data-value="1"/"0" tells us the desired state. */
async function shazamToggleMaybe(btn) {
    if (!btn || !btn.dataset) return;
    const key = (btn.dataset.key || '').trim();
    if (!key) return;
    const desired = btn.dataset.value === '1';
    const keyVariants = (typeof shazamKeyVariants === 'function') ? shazamKeyVariants(key) : [key, key.toLowerCase()];
    // Optimistic local update so the button flips instantly.
    keyVariants.forEach(k => {
        if (!k) return;
        if (desired) shazamMaybe[k] = true;
        else delete shazamMaybe[k];
    });
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/mark-maybe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value: desired }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
            // Revert on failure
            keyVariants.forEach(k => {
                if (!k) return;
                if (desired) delete shazamMaybe[k];
                else shazamMaybe[k] = true;
            });
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            alert((data && data.error) || 'Failed to update Maybe flag');
        } else {
            // Re-apply desired state — defeats any racing status poll that
            // landed between optimistic update and server save (server might
            // have served stale data, Object.assign in the merge would have
            // re-added the cleared key). At this point the server HAS saved,
            // so any further poll returns the correct state.
            keyVariants.forEach(k => {
                if (!k) return;
                if (desired) shazamMaybe[k] = true;
                else delete shazamMaybe[k];
            });
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        }
    } catch (e) {
        // Revert on network error
        keyVariants.forEach(k => {
            if (!k) return;
            if (desired) delete shazamMaybe[k];
            else shazamMaybe[k] = true;
        });
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        alert('Network error: ' + (e.message || 'request failed'));
    }
}

/** Toggle the local-only "Listened" flag for a track. NEVER touches Soundeo. */
async function shazamMarkListened(key, value) {
    if (!key) return;
    const desired = value !== false;
    const keyVariants = (typeof shazamKeyVariants === 'function') ? shazamKeyVariants(key) : [key, key.toLowerCase()];
    keyVariants.forEach(k => {
        if (!k) return;
        if (desired) shazamListened[k] = true;
        else delete shazamListened[k];
    });
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        await fetch('/api/shazam-sync/mark-listened', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, value: desired }),
        });
    } catch (e) {
        // Best-effort — listened isn't critical, no alert spam
    }
}

async function shazamDownloadTrack(key) {
    if (shazamPendingDownload[key]) return;
    shazamPendingDownload[key] = true;
    shazamLastDownloadClickAt = Date.now();
    shazamBarUpdateActions();
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    shazamBarUpdateActions();
    try {
        var keyLower = (key || '').toLowerCase();
        var keyNorm = key && key.indexOf(' (') !== -1 ? key.substring(0, key.indexOf(' (')).trim() : key;
        var keyNormLower = (keyNorm || '').toLowerCase();
        var keyDeep = (shazamKeyVariants(key || '') || []).slice(-1)[0] || null; // last variant is deep-normalized
        var trackUrl = shazamLookupMap(shazamTrackUrls, key, keyLower, keyNorm, keyNormLower, keyDeep)
            || (shazamLastData && shazamLastData.urls ? shazamLookupMap(shazamLastData.urls, key, keyLower, keyNorm, keyNormLower, keyDeep) : undefined)
            || null;
        const res = await fetch('/api/shazam-sync/download-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key: key || '', track_url: trackUrl || '' })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || 'Download failed');
            delete shazamPendingDownload[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            shazamBarUpdateActions();
            return;
        }
        if (data.download_queue && Array.isArray(data.download_queue)) {
            shazamCurrentDownloadQueue = data.download_queue;
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
        }
        if (data.status === 'started') {
            var msg = data.message || 'Downloading…';
            if (shazamCurrentDownloadQueue.length > 0) {
                msg = 'Downloading 1/' + shazamCurrentDownloadQueue.length + (data.message ? ': ' + data.message : '…');
            }
            shazamShowSyncProgress(msg);
        } else if (data.status === 'queued') {
            shazamShowSyncProgress(data.message || 'Download queued…');
        }
        /* Poll for both started and queued — otherwise pending UI clears before the worker reports current_key. */
        shazamStartDownloadPoll();
        shazamPollDownloadProgress();
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        shazamBarUpdateActions();
    } catch (e) {
        delete shazamPendingDownload[key];
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        shazamBarUpdateActions();
        alert('Error: ' + e.message);
    }
}

async function shazamShowDownloadLog() {
    try {
        const res = await fetch('/api/shazam-sync/download-log?lines=100');
        const text = await res.text();
        const overlay = document.createElement('div');
        overlay.className = 'modal active';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000;';
        const box = document.createElement('div');
        box.className = 'modal-content';
        box.style.cssText = 'max-width:90vw;max-height:85vh;display:flex;flex-direction:column;';
        const header = document.createElement('div');
        header.className = 'modal-header';
        header.innerHTML = '<h3>Download log (soundeo_download.log)</h3><button type="button" class="modal-close" aria-label="Close">&times;</button>';
        const body = document.createElement('div');
        body.className = 'modal-body';
        body.style.cssText = 'overflow:auto;flex:1;min-height:200px;';
        const pre = document.createElement('pre');
        pre.style.cssText = 'margin:0;font-size:12px;white-space:pre-wrap;word-break:break-all;';
        pre.textContent = text || '(empty)';
        body.appendChild(pre);
        box.appendChild(header);
        box.appendChild(body);
        overlay.appendChild(box);
        const close = () => overlay.remove();
        header.querySelector('.modal-close').onclick = close;
        overlay.onclick = (e) => { if (e.target === overlay) close(); };
        document.body.appendChild(overlay);
    } catch (e) {
        alert('Could not load log: ' + e.message);
    }
}

// Sticky download-error banner. Reads download_progress.error and shows a
// prominent red banner so subscription / network / no_credits failures don't
// silently disappear when the small progress bar fades after 2.5s.
let _shazamDownloadErrorBannerDismissed = false;  // session-scoped — re-emerges on next page load if the error is still set
function shazamUpdateDownloadErrorBanner(dp) {
    const banner = document.getElementById('downloadErrorBanner');
    if (!banner) return;
    const err = dp && dp.error;
    if (!err || _shazamDownloadErrorBannerDismissed) {
        banner.style.display = 'none';
        return;
    }
    const noCredits = !!(dp && dp.no_credits);
    const titleEl = document.getElementById('downloadErrorTitle');
    const msgEl = document.getElementById('downloadErrorMessage');
    const actionsEl = document.getElementById('downloadErrorActions');
    // Categorize the error so we can render an actionable message instead of
    // the raw backend string. The error is whatever the last failed track
    // produced — typically one of:
    //   • "Download returned 404 (from Soundeo)" — track de-listed from catalog
    //   • "Download returned 403 (from Soundeo|CDN)" — auth / referer mismatch
    //   • "No saved session" — cookies file missing
    //   • "no_credits" → handled separately via the `noCredits` flag
    //   • anything else — fall back to raw text
    const errStr = String(err || '');
    const is404Soundeo = /(?:returned|HTTP)\s*404/i.test(errStr) && /Soundeo/i.test(errStr);
    const is403 = /(?:returned|HTTP)\s*403/i.test(errStr);
    const isNoSession = /no saved session|cookies file/i.test(errStr);
    if (titleEl) {
        if (noCredits) titleEl.textContent = 'Soundeo subscription needed';
        else if (isNoSession) titleEl.textContent = 'Soundeo session missing';
        else if (is404Soundeo) titleEl.textContent = 'Tracks not found on Soundeo';
        else titleEl.textContent = 'Download blocked';
    }
    if (msgEl) {
        msgEl.innerHTML = '';
        if (noCredits) {
            msgEl.textContent = 'Your Soundeo download credits are depleted (subscription due, daily limit hit, or expired Premium). Renew to keep downloading — your queue is preserved.';
        } else if (isNoSession) {
            msgEl.textContent = "Your saved Soundeo session is missing or unreadable. Open Settings → Reconnect Soundeo to log in again, then re-queue.";
        } else if (is404Soundeo) {
            // Show what 404 actually means + list every track that hit it (up
            // to 8) so the user knows which entries to investigate or skip.
            const failedKeys = (dp && Array.isArray(dp.results))
                ? dp.results.filter(function (r) { return !r.ok && /404/.test(r.error || ''); }).map(function (r) { return r.key; }).filter(Boolean)
                : [];
            const intro = "Soundeo returned HTTP 404 for the download URL. The most common cause is that Soundeo removed the track from their catalog after it was matched here — the matched URL still resolves to a track page, but the file itself is no longer available. The queue continued with the remaining tracks.";
            msgEl.appendChild(document.createTextNode(intro));
            if (failedKeys.length) {
                const heading = document.createElement('div');
                heading.style.cssText = 'margin-top:8px;font-size:13px;color:#7a1a1a;';
                heading.textContent = 'Affected track' + (failedKeys.length === 1 ? '' : 's') + ':';
                msgEl.appendChild(heading);
                const ul = document.createElement('ul');
                ul.style.cssText = 'margin:4px 0 0 20px;padding:0;color:#5a1a1a;font-size:13px;';
                failedKeys.slice(0, 8).forEach(function (k) {
                    const li = document.createElement('li');
                    li.textContent = k;
                    ul.appendChild(li);
                });
                if (failedKeys.length > 8) {
                    const li = document.createElement('li');
                    li.style.cssText = 'list-style:none;margin-left:-16px;color:#7a1a1a;';
                    li.textContent = '…and ' + (failedKeys.length - 8) + ' more (see download log)';
                    ul.appendChild(li);
                }
                msgEl.appendChild(ul);
                const hint = document.createElement('div');
                hint.style.cssText = 'margin-top:8px;font-size:12px;color:#7a1a1a;';
                hint.textContent = 'Try Search → re-search Soundeo to find an alternate URL, or mark them as Skipped if Soundeo no longer carries the track.';
                msgEl.appendChild(hint);
            }
        } else if (is403) {
            msgEl.textContent = errStr + ' — usually means Soundeo rejected the request (auth or referer mismatch). Try Reconnect Soundeo from Settings, then re-queue. If it persists, your account may be region-restricted for this track.';
        } else {
            msgEl.textContent = errStr;
        }
    }
    if (actionsEl) {
        actionsEl.innerHTML = '';
        if (noCredits) {
            const a = document.createElement('a');
            a.href = 'https://soundeo.com/account/premium';
            a.target = '_blank';
            a.rel = 'noopener';
            a.textContent = 'Renew Soundeo Premium →';
            a.style.cssText = 'background:#d23535;color:#fff;padding:6px 14px;border-radius:4px;text-decoration:none;font-size:13px;font-weight:500;';
            actionsEl.appendChild(a);
        }
        const blocked = (dp && Array.isArray(dp.no_credits_keys)) ? dp.no_credits_keys.length : 0;
        if (blocked > 0) {
            const span = document.createElement('span');
            span.textContent = blocked + ' track' + (blocked === 1 ? '' : 's') + ' waiting in queue';
            span.style.cssText = 'color:#7a1a1a;font-size:12px;align-self:center;';
            actionsEl.appendChild(span);
        }
    }
    banner.style.display = 'block';
}
function shazamDismissDownloadErrorBanner() {
    _shazamDownloadErrorBannerDismissed = true;
    const banner = document.getElementById('downloadErrorBanner');
    if (banner) banner.style.display = 'none';
}

function shazamPollDownloadProgress() {
    fetch('/api/shazam-sync/status').then(r => r.json()).then(data => {
        // Update the sticky error banner FIRST so the user sees errors even
        // when the running-progress bar has timed out and gone away.
        shazamUpdateDownloadErrorBanner(data && data.download_progress);
        if (data.download_queue && Array.isArray(data.download_queue)) {
            shazamCurrentDownloadQueue = data.download_queue;
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
        }
        var prevDlSnap = { running: shazamDownloadProgressSnapshot.running, current_key: shazamDownloadProgressSnapshot.current_key };
        shazamMergeDownloadProgressFromPayload(data);
        const dp = data.download_progress;
        /* Any key that already has a row in results is done (success or fail) — clear client pending so the playbar/row never sit in a fake “busy” or blank gap. */
        if (dp && Array.isArray(dp.results)) {
            var anyOkMoved = false;
            dp.results.forEach(function (r) {
                if (r && r.key) delete shazamPendingDownload[r.key];
                // Optimistic: on success, move track from To DL → Have immediately so the UI updates even if another batch job is running.
                if (r && r.ok && r.key && r.filepath && shazamLastData) {
                    try {
                        var k = String(r.key).trim();
                        var sep = ' - ';
                        var ix = k.indexOf(sep);
                        var artist = ix !== -1 ? k.slice(0, ix).trim() : '';
                        var title = ix !== -1 ? k.slice(ix + sep.length).trim() : '';
                        if (artist || title) {
                            var toDl = (shazamLastData.to_download || []).slice();
                            var toDl2 = toDl.filter(function (t) {
                                var tk = ((t.artist || '').trim() + ' - ' + (t.title || '').trim()).trim();
                                // Also remove on deep-normalized key match (artist order + mix suffix)
                                var tkLower = tk.toLowerCase();
                                var kLower = k.toLowerCase();
                                var tkNorm = tk.indexOf(' (') !== -1 ? tk.substring(0, tk.indexOf(' (')).trim() : tk;
                                var kNorm = k.indexOf(' (') !== -1 ? k.substring(0, k.indexOf(' (')).trim() : k;
                                var tkDeep = (() => { let s = tkNorm.toLowerCase().replace(/ & /g, ', '); const d = s.indexOf(' - '); if (d !== -1) { const arts = s.substring(0, d).split(', ').map(a => a.trim()).filter(Boolean).sort().join(', '); s = arts + ' - ' + s.substring(d + 3); } return s; })();
                                var kDeep = (() => { let s = kNorm.toLowerCase().replace(/ & /g, ', '); const d = s.indexOf(' - '); if (d !== -1) { const arts = s.substring(0, d).split(', ').map(a => a.trim()).filter(Boolean).sort().join(', '); s = arts + ' - ' + s.substring(d + 3); } return s; })();
                                return !(tk === k || tkLower === kLower || tkDeep === kDeep);
                            });
                            if (toDl2.length !== toDl.length) {
                                shazamLastData.to_download = toDl2;
                                shazamLastData.to_download_count = toDl2.length;
                                shazamLastData.have_locally = (shazamLastData.have_locally || []).concat([{ artist: artist, title: title, filepath: r.filepath }]);
                                anyOkMoved = true;
                            }
                        }
                    } catch (e) { /* ignore */ }
                }
            });
            if (anyOkMoved) {
                shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
                shazamBarUpdateActions();
            }
        }
        var dlSnapChanged = prevDlSnap.running !== shazamDownloadProgressSnapshot.running || prevDlSnap.current_key !== shazamDownloadProgressSnapshot.current_key;
        if (dlSnapChanged && shazamLastData) {
            shazamScheduleRenderTrackList(shazamLastData, !shazamShouldThrottleTrackListRender());
        }
        /* Playbar download state reads shazamDownloadProgressSnapshot — refresh every poll so spinner/have state never goes blank between ticks. */
        shazamBarUpdateActions();
        const el = document.getElementById('shazamProgress');
        if (el && dp) {
            if (dp.running) {
                var qList = (data.download_queue && Array.isArray(data.download_queue)) ? data.download_queue : [];
                var frac = shazamDownloadProgressFractionFromStatus(qList, dp.current_key, dp);
                var trackSuffix = (dp.current_key ? ': ' + (dp.current_key.length > 50 ? dp.current_key.slice(0, 50) + '…' : dp.current_key) : '');
                el.textContent = 'Downloading ' + frac.current + '/' + frac.total + trackSuffix;
                const viewLogBtn = document.getElementById('shazamDownloadViewLogBtn');
                if (viewLogBtn) viewLogBtn.style.display = 'none';
            } else {
                var dlDone = dp.done || 0;
                var dlFail = dp.failed || 0;
                var dlSummary = 'Done. ' + dlDone + ' downloaded' + (dlFail > 0 ? ', ' + dlFail + ' failed.' : '.');
                el.textContent = dp.error || dp.message || dlSummary;
                const viewLogBtn = document.getElementById('shazamDownloadViewLogBtn');
                if (viewLogBtn) viewLogBtn.style.display = (dp.error ? 'inline-block' : 'none');
                if (shazamDownloadPollInterval) {
                    clearInterval(shazamDownloadPollInterval);
                    shazamDownloadPollInterval = null;
                }
                if (dp.current_key) delete shazamPendingDownload[dp.current_key];
                if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
                shazamBarUpdateActions();
                shazamLoadStatus();
                setTimeout(shazamHideSyncProgress, 2500);
            }
        }
        if (dp && !dp.running && shazamDownloadPollInterval) {
            clearInterval(shazamDownloadPollInterval);
            shazamDownloadPollInterval = null;
        }
    }).catch(() => {});
}

async function shazamDownloadAllToDownload() {
    const toDl = (shazamLastData && shazamLastData.to_download) ? shazamLastData.to_download : [];
    const urls = (shazamLastData && shazamLastData.urls) ? shazamLastData.urls : {};
    const keys = toDl.filter(t => {
        const k = (t.artist || '') + ' - ' + (t.title || '');
        return urls[k] || urls[k.toLowerCase()];
    }).map(t => (t.artist || '') + ' - ' + (t.title || ''));
    if (keys.length === 0) {
        alert('No tracks to download (all to-download tracks need a Soundeo link).');
        return;
    }
    try {
        const res = await fetch('/api/shazam-sync/download-queue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keys: keys })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || 'Download failed');
            return;
        }
        if (data.download_queue && Array.isArray(data.download_queue)) {
            shazamCurrentDownloadQueue = data.download_queue;
            shazamRenderDownloadQueue(shazamCurrentDownloadQueue);
        }
        if (data.status === 'started') {
            shazamShowSyncProgress(data.message || `Downloading ${keys.length} tracks…`);
        } else if (data.status === 'queued') {
            shazamShowSyncProgress(data.message || 'Download queued…');
        }
        if (data.status === 'started' || data.status === 'queued') {
            shazamStartDownloadPoll();
            shazamPollDownloadProgress();
        }
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function shazamSearchSingleOnSoundeo(key, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
    try {
        const res = await fetch('/api/shazam-sync/search-soundeo-single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_key: key || undefined, artist: artist || '', title: title || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            shazamClearActionPendingForKey(key);
            if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
            return;
        }
        var searchQueue = data.single_search_queue || [];
        shazamApplyQueueState(shazamCurrentStarQueue, searchQueue, data.unstar_queue !== undefined ? data.unstar_queue : shazamCurrentUnstarQueue);
        if (shazamProgressInterval) { clearInterval(shazamProgressInterval); shazamProgressInterval = null; }
        if (shazamProgressRestoreInterval) { clearInterval(shazamProgressRestoreInterval); shazamProgressRestoreInterval = null; }
        if (data.status === 'queued') {
            shazamShowSyncProgress(data.message || 'Searching… (queued)');
            if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
            shazamEnsureSingleSearchProgressPoll();
            return;
        }
        shazamShowSyncProgress(data.message || 'Searching…');
        if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
        shazamEnsureSingleSearchProgressPoll();
    } catch (e) {
        shazamClearActionPendingForKey(key);
        if (shazamLastData) shazamScheduleRenderTrackList(shazamLastData, true);
        alert('Error: ' + e.message);
    }
}

async function shazamSearchAllOnSoundeo(searchMode) {
    try {
        shazamStopSingleSearchProgressPoll();
        const body = searchMode ? JSON.stringify({ search_mode: searchMode }) : undefined;
        const res = await fetch('/api/shazam-sync/search-soundeo-global', {
            method: 'POST',
            headers: body ? { 'Content-Type': 'application/json' } : {},
            body: body
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            if (res.status === 400 && (data.error || '').toLowerCase().includes('already running')) {
                const label = searchMode === 'new' ? 'Search new' : 'Search unfound';
                shazamJobQueue.push({ id: ++shazamJobId, type: 'search', label: label, payload: { mode: searchMode } });
                shazamRenderJobQueue();
                shazamEnsureProgressVisibleWhenQueued();
            } else {
                alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            }
            return;
        }
        if (shazamProgressInterval) { clearInterval(shazamProgressInterval); shazamProgressInterval = null; }
        if (shazamProgressRestoreInterval) { clearInterval(shazamProgressRestoreInterval); shazamProgressRestoreInterval = null; }
        shazamShowSyncProgress(data.message || 'Searching…');
        const pollStartSearch = Date.now();
        const poll = setInterval(async () => {
            if (Date.now() - pollStartSearch > SHAZAM_INLINE_POLL_MAX_MS) {
                clearInterval(poll);
                shazamHideSyncProgress();
                shazamCurrentProgress = {};
                return;
            }
            const pRes = await fetch('/api/shazam-sync/progress');
            const p = await pRes.json();
            shazamCurrentProgress = p;
            shazamApplyQueueState(p.star_queue || [], p.single_search_queue || [], p.unstar_queue || []);
            const el = document.getElementById('shazamProgress');
            if (el) {
                if (p.running) {
                    const total = p.total != null && p.total > 0 ? p.total : null;
                    const cur = p.current != null ? p.current : 0;
                    let text;
                    if (total != null && p.mode === 'search_global') {
                        const label = p.search_mode === 'unfound' ? 'Unfound' : p.search_mode === 'new' ? 'New' : 'Search';
                        text = `${label}: ${cur}/${total}${p.message ? ' — ' + p.message : ''}`;
                    } else if (total != null) {
                        text = (p.current != null && p.total != null) ? `${p.current}/${p.total}: ${p.message || ''}` : (p.message || 'Searching…');
                    } else {
                        text = p.message || 'Searching…';
                    }
                    el.textContent = text;
                } else {
                    el.textContent = p.error ? 'Error: ' + p.error : (p.message || 'Done.');
                }
            }
            shazamSetProgressClickable(p.running && !!p.current_key);
            if (p.mode === 'search_global' && shazamLastData) {
                if (p.urls) {
                    Object.assign(shazamTrackUrls, p.urls);
                    // Merge into data so render sees cumulative urls (live green dots). Do NOT infer starred from urls.
                    shazamLastData.urls = { ...(shazamLastData.urls || {}), ...p.urls };
                    Object.keys(p.urls).forEach(k => {
                        delete shazamNotFound[k];
                        delete shazamNotFound[k.toLowerCase()];
                        if (shazamLastData.not_found) {
                            delete shazamLastData.not_found[k];
                            delete shazamLastData.not_found[k.toLowerCase()];
                        }
                    });
                }
                if (p.starred && typeof p.starred === 'object') {
                    Object.assign(shazamStarred, p.starred);
                    shazamLastData.starred = { ...(shazamLastData.starred || {}), ...p.starred };
                }
                if (p.not_found) {
                    Object.assign(shazamNotFound, p.not_found);
                    // Merge into data so render sees cumulative not_found (live orange dots) without refresh
                    shazamLastData.not_found = { ...(shazamLastData.not_found || {}), ...p.not_found };
                }
                if (p.soundeo_titles) {
                    Object.assign(shazamSoundeoTitles, p.soundeo_titles);
                    shazamLastData.soundeo_titles = { ...(shazamLastData.soundeo_titles || {}), ...p.soundeo_titles };
                }
                if (p.soundeo_match_scores) {
                    shazamLastData.soundeo_match_scores = { ...(shazamLastData.soundeo_match_scores || {}), ...p.soundeo_match_scores };
                }
            }
            if (p.running && shazamLastData) {
                shazamRenderTrackList(shazamLastData);
                if (shazamFollowCurrentRow && p.current_key) shazamScrollCurrentRowToCenter(false);
            }
            if (!p.running) {
                shazamFollowCurrentRow = false;
                shazamCurrentProgress = {};
                clearInterval(poll);
                shazamHideSyncProgress();
                const stopBtn = document.getElementById('shazamSyncStopBtn');
                const gotoBtn = document.getElementById('shazamProgressGotoBtn');
                if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stop'; }
                if (gotoBtn) gotoBtn.textContent = 'Follow row';
                if (p.mode === 'search_global') {
                    if (p.not_found) Object.assign(shazamNotFound, p.not_found);
                    if (p.urls) Object.assign(shazamTrackUrls, p.urls);
                    if (p.starred && typeof p.starred === 'object') {
                        Object.assign(shazamStarred, p.starred);
                        if (shazamLastData) shazamLastData.starred = { ...(shazamLastData.starred || {}), ...p.starred };
                    }
                    if (p.soundeo_titles) Object.assign(shazamSoundeoTitles, p.soundeo_titles);
                    if (p.soundeo_match_scores && shazamLastData) {
                        shazamLastData.soundeo_match_scores = { ...(shazamLastData.soundeo_match_scores || {}), ...p.soundeo_match_scores };
                    }
                    if (shazamLastData) shazamRenderTrackList(shazamLastData);
                }
                shazamLoadStatus();
                shazamMaybeStartQueuedJob();
            }
        }, 500);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function shazamResetNotFound() {
    try {
        const res = await fetch('/api/shazam-sync/reset-not-found', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || 'Failed to reset not-found state.');
            return;
        }
        shazamNotFound = {};
        await shazamLoadStatus();
        if (data.message) alert(data.message);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

