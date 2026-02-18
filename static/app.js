let currentFiles = [];
let processedCount = 0;
let successCount = 0;

const APP_STATE_KEY = 'mp3cleaner_app_state';

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
            <button type="button" onclick="togglePlay(${index})" id="play-btn-${index}" class="play-btn" style="width:28px;height:28px;border-radius:50%;border:none;background:var(--accent);color:white;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center;flex-shrink:0;">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="margin-left:2px;"><use href="#icon-play"/></svg>
            </button>
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
    const overlay = document.getElementById('loadingOverlay');
    if (!overlay) return;
    if (_loadingTimeoutId) clearTimeout(_loadingTimeoutId);
    document.getElementById('loadingText').textContent = text || 'Processing...';
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
        if (otherBtn) otherBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="margin-left:2px;"><use href="#icon-play"/></svg>';
        const otherProgress = document.getElementById(`progress-${currentlyPlaying}`);
        const otherTime = document.getElementById(`time-${currentlyPlaying}`);
        if (otherProgress) otherProgress.style.width = '0%';
        if (otherTime) otherTime.textContent = '0:00';
    }

    if (audio.paused) {
        audio.play().then(() => {
            playBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><use href="#icon-pause"/></svg>';
            currentlyPlaying = index;
        }).catch(() => {});

        if (timeUpdateListeners[index]) audio.removeEventListener('timeupdate', timeUpdateListeners[index]);
        if (endedListeners[index]) audio.removeEventListener('ended', endedListeners[index]);

        timeUpdateListeners[index] = function() { updateProgress(index); };
        endedListeners[index] = function() {
            playBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="margin-left:2px;"><use href="#icon-play"/></svg>';
            currentlyPlaying = null;
            document.getElementById(`progress-${index}`).style.width = '0%';
            document.getElementById(`time-${index}`).textContent = '0:00';
        };

        audio.addEventListener('timeupdate', timeUpdateListeners[index]);
        audio.addEventListener('ended', endedListeners[index]);
    } else {
        audio.pause();
        playBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" style="margin-left:2px;"><use href="#icon-play"/></svg>';
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
/** Shown when an action is rejected (e.g. another operation running) so the user gets context. */
const SHAZAM_ACTION_REJECTED_MSG = 'Another operation is already running. Wait for it to finish or click Stop.';
let shazamComparePollInterval = null;
let shazamFolderInputs = [];
let shazamProgressInterval = null;
let shazamProgressRestoreInterval = null;
/** Latest sync/search progress from server (running, current, total, message, current_key). Used to show spinner in the row being processed. */
let shazamCurrentProgress = {};
let shazamTrackUrls = {};
/** Per-track "starred in Soundeo" state (key: "Artist - Title"). Restored from status on load. */
let shazamStarred = {};
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

async function shazamLoadSettings() {
    try {
        const res = await fetch('/api/settings');
        const cfg = await res.json();
        shazamApplySettings(cfg);
        return cfg;
    } catch (e) {
        console.error(e);
        shazamApplySettings({});
        return {};
    }
}

function shazamApplySettings(cfg) {
    shazamFolderInputs = (cfg.destination_folders || []).slice();
    shazamRenderFolderList();
    const statusEl = document.getElementById('soundeoSessionStatus');
    const pathEl = document.getElementById('soundeoSessionPath');
    const btn = document.getElementById('shazamSaveSessionBtn');
    const hasSession = !!(cfg.soundeo_cookies_path || cfg.soundeo_cookies_path_resolved);
    if (statusEl) statusEl.textContent = hasSession ? '· connected' : '· not connected';
    if (btn) btn.textContent = hasSession ? 'Reconnect' : 'Connect Soundeo';
    if (pathEl) pathEl.style.display = 'none';
}

async function shazamBootstrapLoad() {
    const trackList = document.getElementById('shazamTrackList');
    if (trackList) trackList.innerHTML = '<p class="shazam-info-msg">Loading...</p>';
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 15000);
        const res = await fetch('/api/shazam-sync/bootstrap', { signal: controller.signal });
        clearTimeout(timeoutId);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Bootstrap failed');
        const cfg = data.settings || {};
        const status = data.status || {};
        shazamApplySettings(cfg);
        shazamApplyStatus(status);
    } catch (e) {
        console.error('Bootstrap failed:', e);
        const msg = e.name === 'AbortError' ? 'Request timed out. Server may be busy.' : (e.message || 'Could not load data.');
        if (trackList) trackList.innerHTML =
            '<p class="shazam-info-msg shazam-warning">' + msg +
            ' <button type="button" class="btn btn-small" onclick="shazamBootstrapLoad()">Retry</button></p>';
        shazamLoadSettings();
        shazamLoadStatus();
    }
}

function shazamRenderFolderList() {
    const el = document.getElementById('shazamFolderList');
    const rows = shazamFolderInputs.length ? shazamFolderInputs : [''];
    el.innerHTML = rows.map((path, i) =>
        `<div class="folder-list-item"><input type="text" value="${(path || '').replace(/"/g, '&quot;')}" placeholder="${i === 0 && !path ? 'Paste folder path or click Add Folder' : ''}" data-idx="${i}" onchange="shazamFolderChanged(this)" />${path ? `<button onclick="shazamRescanFolder(${i})" class="btn btn-small" title="Rescan this folder only">Rescan</button>` : ''}<button onclick="shazamRemoveFolder(${i})" class="btn btn-small" title="Remove folder" ${rows.length === 1 && !path ? 'style="visibility:hidden"' : ''}>✕</button></div>`
    ).join('');
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
            alert(data.error);
            return;
        }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0);
            const start = Date.now();
            shazamComparePollInterval = setInterval(() => shazamComparePoll(start), 500);
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        document.getElementById('shazamSyncBtn').disabled = !(data.to_download && data.to_download.length > 0);
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
            alert(data.error);
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
        shazamLoadStatus();
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
    const btn = document.getElementById('shazamCompareBtn');
    const rescanBtn = document.getElementById('shazamRescanBtn');
    if (!el) return;
    if (btn) btn.disabled = show;
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
            alert(data.error || 'Compare failed');
            shazamRenderTrackList(data);
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
            const start = Date.now();
            setTimeout(function () { shazamComparePoll(start); }, 120);
            shazamComparePollInterval = setInterval(function () { shazamComparePoll(start); }, 500);
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        document.getElementById('shazamSyncBtn').disabled = !(data.to_download && data.to_download.length > 0);
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
    } catch (e) {
        if (shazamComparePollInterval) {
            clearInterval(shazamComparePollInterval);
            shazamComparePollInterval = null;
        }
        shazamShowCompareProgress(false);
        alert('Compare status check failed: ' + (e && e.message ? e.message : 'Unknown error'));
    }
}

async function shazamRescan() {
    const progressEl = document.getElementById('shazamCompareProgress');
    const progressText = document.getElementById('shazamCompareProgressText');
    try {
        if (progressEl) {
            progressEl.style.display = 'flex';
            progressEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            if (progressText) progressText.textContent = 'Starting rescan...';
        }
        const res = await fetch('/api/shazam-sync/rescan', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (!res.ok) {
            shazamShowCompareProgress(false);
            alert(data.error || 'Rescan failed');
            return;
        }
        if (data.error) {
            shazamShowCompareProgress(false);
            alert(data.error);
            shazamRenderTrackList(data);
            return;
        }
        if (data.running) {
            shazamShowCompareProgress(true, 0, 0, 'Starting rescan...');
            const start = Date.now();
            setTimeout(function () { shazamComparePoll(start); }, 120);
            shazamComparePollInterval = setInterval(function () { shazamComparePoll(start); }, 500);
            return;
        }
        shazamShowCompareProgress(false);
        document.getElementById('shazamCount').textContent = data.shazam_count || 0;
        document.getElementById('shazamLocalCount').textContent = data.local_count || 0;
        var haveEl = document.getElementById('shazamHaveCount');
        if (haveEl) haveEl.textContent = (data.have_locally && data.have_locally.length) || 0;
        document.getElementById('shazamToDownloadCount').textContent = data.to_download_count || 0;
        document.getElementById('shazamSyncBtn').disabled = !(data.to_download && data.to_download.length > 0);
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
    const syncBtn = document.getElementById('shazamSyncBtn');
    if (syncBtn) syncBtn.disabled = !(data.to_download && data.to_download.length > 0);
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.urls && (!data.starred || Object.keys(data.starred).length === 0)) { Object.keys(data.urls).forEach(k => { shazamStarred[k] = true; }); }
    if (data.dismissed_manual_check && Array.isArray(data.dismissed_manual_check)) {
        shazamDismissedManualCheck = {};
        data.dismissed_manual_check.forEach(k => { shazamDismissedManualCheck[k] = true; });
    }
    if (data.soundeo_titles && typeof data.soundeo_titles === 'object') {
        Object.assign(shazamSoundeoTitles, data.soundeo_titles);
    }
    // not_found: only replace when applying fresh server data (so reset/refresh shows grey). Never replace inside shazamRenderTrackList or we wipe per-row search updates.
    if (data.hasOwnProperty('not_found') && typeof data.not_found === 'object') {
        shazamNotFound = {};
        Object.assign(shazamNotFound, data.not_found);
    }
    shazamRenderTrackList(data);
    if (data.compare_running && !shazamComparePollInterval) {
        const sp = data.scan_progress || {};
        const cur = sp.current || 0;
        const tot = sp.total || 0;
        const msg = sp.message || (tot > 0 ? null : 'Discovering files...');
        shazamShowCompareProgress(true, cur, tot, msg || (tot > 0 ? (cur.toLocaleString() + ' / ' + tot.toLocaleString()) : undefined));
        setTimeout(function () { shazamComparePoll(Date.now()); }, 120);
        shazamComparePollInterval = setInterval(function () { shazamComparePoll(Date.now()); }, 500);
    } else if (!data.compare_running) {
        shazamShowCompareProgress(false);
    }
    shazamRestoreProgressIfRunning();
}

/** If a sync/search job is still running on the server, show the progress bar and poll until done. */
function shazamRestoreProgressIfRunning() {
    fetch('/api/shazam-sync/progress')
        .then(r => r.json())
        .then(p => {
            if (!p.running) return;
            shazamCurrentProgress = p;
            shazamSetProgressClickable(!!p.current_key);
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            const barEl = document.getElementById('shazamSyncProgress');
            if (barEl && barEl.style.display === 'flex') return; /* already visible and likely already polling */
            if (shazamProgressRestoreInterval) clearInterval(shazamProgressRestoreInterval);
            const stopBtn = document.getElementById('shazamSyncStopBtn');
            shazamShowSyncProgress(
                (p.current != null && p.total != null) ? `${p.current}/${p.total}: ${p.message || ''}` : (p.message || 'Running…')
            );
            if (stopBtn) stopBtn.disabled = false;
            shazamProgressRestoreInterval = setInterval(function () {
                fetch('/api/shazam-sync/progress')
                    .then(r => r.json())
                    .then(p => {
                        shazamCurrentProgress = p;
                        const el = document.getElementById('shazamProgress');
                        const stopBtn = document.getElementById('shazamSyncStopBtn');
                        if (el) {
                            if (p.running) {
                                let text = (p.current != null && p.total != null)
                                    ? `${p.current}/${p.total}: ${p.message || ''}`
                                    : (p.message || 'Running…');
                                if (p.last_url) {
                                    const urlDisplay = p.last_url.replace(/^https?:\/\//, '');
                                    text += ' — ' + urlDisplay.slice(0, 50) + (urlDisplay.length > 50 ? '…' : '');
                                }
                                el.textContent = text;
                            }
                        }
                        shazamSetProgressClickable(p.running && !!p.current_key);
                        if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
                        if (!p.running) {
                            shazamCurrentProgress = {};
                            if (shazamProgressRestoreInterval) {
                                clearInterval(shazamProgressRestoreInterval);
                                shazamProgressRestoreInterval = null;
                            }
                            if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stop'; }
                            shazamHideSyncProgress();
                            shazamLoadStatus();
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
let shazamFilterTime = 'all';
let shazamFilterStatus = 'all';
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

function shazamPlayerBarShow(label) {
    const bar = document.getElementById('shazamPlayerBar');
    const labelEl = document.getElementById('shazamBarTrackLabel');
    const playPauseBtn = document.getElementById('shazamBarPlayPause');
    if (!bar || !labelEl || !playPauseBtn) return;
    labelEl.textContent = label || '—';
    bar.style.display = 'flex';
    playPauseBtn.textContent = '⏸';
    playPauseBtn.classList.remove('paused');
    if (shazamAudioEl) {
        if (shazamBarTimeUpdate) shazamAudioEl.removeEventListener('timeupdate', shazamBarTimeUpdate);
        if (shazamBarEnded) shazamAudioEl.removeEventListener('ended', shazamBarEnded);
        shazamBarTimeUpdate = function () { shazamPlayerBarUpdateProgress(); };
        shazamBarEnded = function () {
            shazamPlayerBarHide();
            const playingBtn = document.querySelector('.shazam-play-btn.playing');
            if (playingBtn) { playingBtn.textContent = '▶'; playingBtn.classList.remove('playing'); }
            shazamCurrentlyPlaying = null;
        };
        shazamAudioEl.addEventListener('timeupdate', shazamBarTimeUpdate);
        shazamAudioEl.addEventListener('ended', shazamBarEnded);
    }
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
    if (shazamPlayingBtn) { shazamPlayingBtn.textContent = '▶'; shazamPlayingBtn.classList.remove('playing'); shazamPlayingBtn = null; }
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
        if (playPauseBtn) { playPauseBtn.textContent = '⏸'; playPauseBtn.classList.remove('paused'); }
        if (shazamPlayingBtn) { shazamPlayingBtn.textContent = '⏸'; shazamPlayingBtn.classList.add('playing'); }
    } else {
        shazamAudioEl.pause();
        if (playPauseBtn) { playPauseBtn.textContent = '▶'; playPauseBtn.classList.add('paused'); }
        if (shazamPlayingBtn) { shazamPlayingBtn.textContent = '▶'; shazamPlayingBtn.classList.remove('playing'); }
    }
}

function shazamPlayerBarClose() {
    if (shazamAudioEl) shazamAudioEl.pause();
    shazamCurrentlyPlaying = null;
    shazamPlayerBarHide();
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
    if (shazamFilterStatus === 'ignored') {
        out = out.filter(t => {
            const key = `${t.artist || ''} - ${t.title || ''}`;
            return shazamDismissed[key] || shazamDismissed[key.toLowerCase()];
        });
    } else if (shazamFilterStatus !== 'all') {
        out = out.filter(t => t.status === shazamFilterStatus);
    }
    return out;
}

function shazamRenderTrackList(data) {
    const progressCaptured = shazamCaptureSyncProgress();
    if (!data) data = {};
    shazamLastData = data;
    if (data.urls) Object.assign(shazamTrackUrls, data.urls);
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.urls && (!data.starred || Object.keys(data.starred).length === 0)) { Object.keys(data.urls).forEach(k => { shazamStarred[k] = true; }); }
    if (data.dismissed) Object.assign(shazamDismissed, data.dismissed);
    if (data.dismissed_manual_check && Array.isArray(data.dismissed_manual_check)) {
        shazamDismissedManualCheck = {};
        data.dismissed_manual_check.forEach(k => { shazamDismissedManualCheck[k] = true; });
    }
    if (data.soundeo_titles && typeof data.soundeo_titles === 'object') {
        Object.assign(shazamSoundeoTitles, data.soundeo_titles);
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
    if (have.length === 0 && toDl.length === 0 && skipped.length === 0) {
        if (!data.error) {
            html += '<p class="shazam-info-msg">Click <strong>Fetch Shazam</strong> to load tracks, add destination folders in Settings, then <strong>Compare</strong>.</p>';
        }
        el.innerHTML = html || '<p class="shazam-info-msg">Run Compare to see tracks.</p>';
        if (selectionBar) selectionBar.style.display = 'none';
        shazamRestoreSyncProgress(progressCaptured);
        return;
    }
    const merged = [...have, ...toDl, ...skipped];
    merged.sort((a, b) => { const sa = a.shazamed_at ?? 0; const sb = b.shazamed_at ?? 0; return sb - sa; });
    const filtered = shazamApplyFilters(merged);
    const hasTodl = filtered.some(r => r.status === 'todl');
    const hasSkipped = filtered.some(r => r.status === 'skipped');
    html += '<table class="shazam-track-table"><thead><tr><th></th><th>When</th><th>Artist</th><th>Title</th><th class="shazam-match-col">Match</th>';
    html += '<th></th><th></th><th>Actions</th>';
    html += '<th class="shazam-select-col">' + (hasTodl ? '<input type="checkbox" id="shazamSelectAll" onchange="shazamToggleSelectAll(this)" title="Select all" />' : '') + '</th>';
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
        const url = _lu(shazamTrackUrls, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.urls || {}, key, keyLower, keyNorm, keyNormLower, keyDeep);
        const soundeoTitle = _lu(shazamSoundeoTitles, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.soundeo_titles || {}, key, keyLower, keyNorm, keyNormLower, keyDeep);
        const starred = !!(_lu(shazamStarred, key, keyLower, keyNorm, keyNormLower, keyDeep) || _lu(data.starred || {}, key, keyLower, keyNorm, keyNormLower, keyDeep));
        // Only exact key or keyLower for not_found (paper trail: searched but no link). Avoid keyNorm/keyDeep so different tracks don't match the same entry and we get correct grey dots.
        const isSearchedNotFound = !!(_lu(shazamNotFound, key, keyLower) || _lu(data.not_found || {}, key, keyLower));
        const soundeoScoreMap = data.soundeo_match_scores || {};
        const soundeoMatchScore = _lu(soundeoScoreMap, key, keyLower, keyNorm, keyNormLower, keyDeep) || null;
        const score = row.match_score != null ? row.match_score : null;
        const isSynced = !!url;
        const isDismissed = !!(shazamDismissed[key] || shazamDismissed[keyLower]);
        const manualCheckDismissed = !!shazamDismissedManualCheck[key];
        const isNonExtendedVersion = soundeoTitle && /\((original\s+mix|radio\s+edit|radio\s+version|short\s+version)\)/i.test(soundeoTitle.trim()) && !/extended/i.test(soundeoTitle.trim());
        const showManualCheck = isTodl && !isDismissed && isSynced && !manualCheckDismissed && isNonExtendedVersion;
        const isPending = !!shazamActionPending[key];
        const escapedKey = escapeHtml(key);
        const escapedArtist = escapeHtml(row.artist);
        const escapedTitle = escapeHtml(row.title);
        const currentKey = shazamCurrentProgress.current_key;
        const isCurrentTrack = !!(shazamCurrentProgress.running && currentKey && (currentKey === key || currentKey.toLowerCase() === keyLower));

        let statusCell = '';
        if (isCurrentTrack) {
            statusCell = '<td class="status-cell"><span class="status-spinner" title="Processing…"></span></td>';
        } else if (isDismissed) {
            statusCell = '<td class="status-cell"><span class="status-dot status-dismissed" title="Dismissed">\u00d7</span></td>';
        } else if (row.status === 'skipped') {
            statusCell = '<td class="status-cell"><span class="status-dot status-skipped" title="Skipped">\u2014</span></td>';
        } else if (row.status === 'have' && starred) {
            statusCell = '<td class="status-cell"><span class="status-dot status-have-starred" title="Have locally, starred"></span></td>';
        } else if (row.status === 'have' && !starred) {
            statusCell = '<td class="status-cell"><span class="status-dot status-found" title="Have locally, found on Soundeo"></span></td>';
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
        const starCellClass = 'starred-cell' + (starInactive ? ' starred-cell-inactive' : '');
        const starIconClass = 'star-icon' + (starFilled ? ' starred' : '');
        const starSvgFilled = '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>';
        const starSvgOutline = '<path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/>';
        let starredCell = '<td class="' + starCellClass + '" title="' + escapeHtml(starTitle) + '"><svg class="' + starIconClass + '" width="14" height="14" viewBox="0 0 24 24" ' + (starFilled ? 'fill="currentColor"' : 'fill="none" stroke="currentColor" stroke-width="2"') + '>' + (starFilled ? starSvgFilled : starSvgOutline) + '</svg></td>';

        let matchCell = '';
        if (isSkipped || isDismissed) {
            matchCell = '<td class="shazam-match-col">\u2014</td>';
        } else if (isTodl) {
            const manualIcon = showManualCheck
                ? '<span class="manual-check-icon" title="Soundeo link is Original Mix / Radio Edit \u2013 check for Extended" data-track-key="' + escapedKey + '" onclick="shazamDismissManualCheck(this)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg></span>'
                : '';
            const sPct = soundeoMatchScore != null ? Math.round(soundeoMatchScore * 100) : null;
            const scoreHtml = sPct != null ? '<span class="shazam-match-pct">' + sPct + '%</span>' : '';
            matchCell = '<td class="shazam-match-col">' + (scoreHtml || manualIcon || '\u2014') + '</td>';
        } else {
            const pct = score != null ? Math.round(score * 100) : null;
            matchCell = '<td class="shazam-match-col">' + (pct != null ? '<span class="shazam-match-pct">' + pct + '%</span>' : '\u2014') + '</td>';
        }

        const trackLabel = (soundeoTitle || key).replace(/"/g, '&quot;');
        let playCell = '';
        if (row.filepath) {
            const pathNorm = String(row.filepath).replace(/\\/g, '/');
            const lastSlash = pathNorm.lastIndexOf('/');
            const dir = lastSlash >= 0 ? pathNorm.substring(0, lastSlash) : '';
            const file = lastSlash >= 0 ? pathNorm.substring(lastSlash + 1) : pathNorm;
            const dirB64 = dir ? btoa(unescape(encodeURIComponent(dir))) : '';
            const pathB64 = pathNorm ? btoa(unescape(encodeURIComponent(pathNorm))) : '';
            const localFile = file || pathNorm;
            playCell = `<td class="shazam-play-col"><button type="button" class="shazam-play-btn" data-dir-b64="${escapeHtml(dirB64)}" data-file="${escapeHtml(file)}" data-path-b64="${escapeHtml(pathB64)}" data-track-label="${escapeHtml(trackLabel)}" onclick="shazamTogglePlay(this)" title="Play local file: ${escapeHtml(localFile)}" style="width:24px;height:24px;">\u25b6</button></td>`;
        } else if (url) {
            const previewTip = soundeoTitle ? `Stream Soundeo preview: ${escapeHtml(soundeoTitle)}` : 'Stream Soundeo preview';
            playCell = `<td class="shazam-play-col"><button type="button" class="shazam-play-btn shazam-soundeo-play" data-soundeo-url="${escapeHtml(url)}" data-track-label="${escapeHtml(trackLabel)}" onclick="shazamToggleSoundeoPlay(this)" title="${previewTip}" style="width:24px;height:24px;">\u25b6</button></td>`;
        } else {
            playCell = '<td class="shazam-play-col"></td>';
        }

        const safeAttr = s => escapeHtml(s).replace(/'/g, '&#39;');
        const inactive = ' shazam-row-action-inactive';
        const searchInactive = isDismissed || isSkipped ? inactive : '';
        const syncInactive = isDismissed || isSkipped || isSynced || !isTodl ? inactive : '';
        const starActionInactive = isDismissed || isSkipped || !isSynced || starred ? inactive : '';
        const dismissInactive = isSkipped || !isTodl ? inactive : '';
        const skipInactive = isDismissed || !isTodl ? inactive : '';

        let actionsCell = '<td class="shazam-actions-col">';
        if (isPending) {
            actionsCell += '<span class="shazam-action-spinner" title="Processing\u2026">&#8987;</span>';
        } else {
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-search-action${searchInactive}" data-action="search" data-key="${safeAttr(key)}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Search on Soundeo (find link, no favorite)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></button>`;
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-sync-action${syncInactive}" data-action="sync" data-key="${safeAttr(key)}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Find &amp; star on Soundeo"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></button>`;
            actionsCell += `<button type="button" class="shazam-row-action-btn shazam-star-action${starActionInactive}" data-action="star" data-key="${safeAttr(key)}" data-track-url="${safeAttr(url || '')}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="${starActionInactive ? (starred ? 'In Soundeo favorites' : isDismissed ? 'Dismissed' : isSkipped ? 'Skipped' : 'Find link first (Search)') : 'Add to Soundeo favorites (just star)'}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg></button>`;
            if (isDismissed) {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-undo-action" data-action="undismiss" data-key="${safeAttr(key)}" data-url="${safeAttr(url || '')}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Undo dismiss (re-star on Soundeo)">Undo</button>`;
            } else {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-dismiss-action${dismissInactive}" data-action="dismiss" data-key="${safeAttr(key)}" data-url="${safeAttr(url || '')}" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Dismiss (unstar on Soundeo)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>`;
            }
            if (isSkipped) {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-undo-action" onclick="shazamUnskipRow(this)" title="Undo skip">Undo</button>`;
            } else {
                actionsCell += `<button type="button" class="shazam-row-action-btn shazam-skip-action${skipInactive}" data-action="skip" data-artist="${safeAttr(row.artist)}" data-title="${safeAttr(row.title)}" title="Skip (hide locally)"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg></button>`;
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
        const rowAttrs = (isSkipped
            ? ` data-artist="${escapedArtist}" data-title="${escapedTitle}"`
            : (isTodl ? ` data-idx="${idx}"` : '')) + ` data-track-key="${escapedKey}"`;

        let titleCellContent = escapeHtml(row.title);
        if (soundeoTitle && !isDismissed && !isSkipped) {
            if (url) {
                titleCellContent += `<div class="soundeo-source-title"><a href="${escapeHtml(url)}" target="_blank" rel="noopener" title="Open on Soundeo: ${escapeHtml(soundeoTitle)}">${escapeHtml(soundeoTitle)}</a></div>`;
            } else {
                titleCellContent += `<div class="soundeo-source-title" title="${escapeHtml(soundeoTitle)}">${escapeHtml(soundeoTitle)}</div>`;
            }
        }

        html += `<tr class="${rowClass}"${rowAttrs}>${statusCell}<td class="shazam-when">${escapeHtml(when)}</td><td>${escapeHtml(row.artist)}</td><td>${titleCellContent}</td>${matchCell}${playCell}${starredCell}${actionsCell}${selectCell}</tr>`;
    });
    html += '</tbody></table>';
    el.innerHTML = html;
    if (selectionBar) selectionBar.style.display = filtered.some(r => r.status === 'todl') ? 'flex' : 'none';
    shazamUpdateSelectionCount();
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
    try {
        const dirB64 = (btn.dataset.dirB64 || '').trim();
        const file = btn.dataset.file;
        const pathB64 = (btn.dataset.pathB64 || '').trim();
        const streamUrl = (dirB64 && file != null)
            ? '/api/shazam-sync/stream-file?dir=' + encodeURIComponent(dirB64) + '&file=' + encodeURIComponent(file)
            : (pathB64 ? '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pathB64) : null);
        if (!streamUrl) return;
        const playKey = streamUrl;
    if (!shazamAudioEl) {
        shazamAudioEl = document.createElement('audio');
    }
    const playingBtn = document.querySelector('.shazam-play-btn.playing');
    if (playingBtn) {
        playingBtn.textContent = '▶';
        playingBtn.classList.remove('playing');
    }
    if (shazamCurrentlyPlaying === playKey) {
        shazamAudioEl.pause();
        shazamCurrentlyPlaying = null;
        shazamPlayerBarHide();
        return;
    }
    releaseShazamProxy();
    let playErrorAlertShown = false;
    const showPlayError = (msg) => { if (!playErrorAlertShown) { playErrorAlertShown = true; alert(msg); } };
    const resetBtn = () => { if (btn) { btn.textContent = '▶'; btn.classList.remove('playing'); } shazamCurrentlyPlaying = null; shazamPlayingBtn = null; shazamPlayerBarHide(); };
    const fileLower = (btn.dataset.file || '').toLowerCase();
    const isAiffOrWav = /\.(aiff?|wav)$/.test(fileLower);

    // AIFF/WAV: temp MP3 proxy for instant playback + scrubbing (prepare → mp3_url → release on end/close/switch)
    if (isAiffOrWav) {
        (async function () {
            btn.textContent = '…';
            btn.disabled = true;
            const body = (dirB64 && file != null) ? { dir_b64: dirB64, file: file } : (pathB64 ? { path_b64: pathB64 } : null);
            if (!body) { btn.disabled = false; btn.textContent = '▶'; return; }
            try {
                const res = await fetch('/api/shazam-sync/prepare-proxy', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json().catch(function () { return {}; });
                if (!res.ok) {
                    showPlayError(data.error || res.status === 403 ? 'Add your music folder to Sync \u2192 Settings \u2192 Destination folders.' : res.status === 404 ? 'File not found.' : 'Prepare failed.');
                    btn.disabled = false;
                    btn.textContent = '▶';
                    return;
                }
                const mp3Url = data.mp3_url;
                const proxyId = data.proxy_id;
                if (!mp3Url || !proxyId) {
                    showPlayError('Invalid prepare response.');
                    btn.disabled = false;
                    btn.textContent = '▶';
                    return;
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
                shazamAudioEl.onended = function () { resetBtn(); };
                shazamAudioEl.src = mp3Url;
                shazamAudioEl.load();
                await shazamAudioEl.play();
                btn.textContent = '⏸';
                btn.classList.add('playing');
                btn.disabled = false;
                shazamCurrentlyPlaying = playKey;
                shazamPlayingBtn = btn;
                shazamPlayerBarShow(btn.dataset.trackLabel || '—');
            } catch (e) {
                showPlayError('Playback failed: ' + (e.message || String(e)));
                btn.disabled = false;
                btn.textContent = '▶';
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
    shazamAudioEl.onended = () => { resetBtn(); };
    shazamAudioEl.src = streamUrl;
    shazamAudioEl.play().then(() => {
        btn.textContent = '⏸';
        btn.classList.add('playing');
        shazamCurrentlyPlaying = playKey;
        shazamPlayingBtn = btn;
        shazamPlayerBarShow(btn.dataset.trackLabel || '—');
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
    const trackUrl = btn.dataset.soundeoUrl;
    if (!trackUrl) return;

    if (!shazamAudioEl) {
        shazamAudioEl = document.createElement('audio');
    }
    const playingBtn = document.querySelector('.shazam-play-btn.playing');
    if (playingBtn && playingBtn !== btn) {
        playingBtn.textContent = '▶';
        playingBtn.classList.remove('playing');
    }
    if (shazamCurrentlyPlaying === trackUrl) {
        shazamAudioEl.pause();
        btn.textContent = '▶';
        btn.classList.remove('playing');
        shazamCurrentlyPlaying = null;
        shazamPlayerBarHide();
        return;
    }

    btn.textContent = '…';
    btn.disabled = true;
    const resetBtn = () => { btn.textContent = '▶'; btn.classList.remove('playing'); btn.disabled = false; };
    try {
        const streamUrl = '/api/soundeo/stream-preview?track_url=' + encodeURIComponent(trackUrl);
        shazamAudioEl.onerror = () => {
            console.warn('Soundeo preview audio error');
            resetBtn();
            shazamCurrentlyPlaying = null;
        };
        shazamAudioEl.onended = () => {
            resetBtn();
            shazamCurrentlyPlaying = null;
        };
        shazamAudioEl.src = streamUrl;
        shazamAudioEl.load();
        await shazamAudioEl.play();
        btn.textContent = '⏸';
        btn.classList.add('playing');
        btn.disabled = false;
        shazamCurrentlyPlaying = trackUrl;
        shazamPlayingBtn = btn;
        shazamPlayerBarShow(btn.dataset.trackLabel || '—');
    } catch (e) {
        console.warn('Soundeo preview playback failed:', e);
        resetBtn();
        shazamCurrentlyPlaying = null;
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
            shazamDismissed[key] = true;
            shazamStarred[key] = false;
        } else if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
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
            delete shazamDismissed[key];
            shazamStarred[key] = true;
            if (data.url) shazamTrackUrls[key] = data.url;
            await shazamLoadStatus();
        } else if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
}

async function shazamStarTrack(key, trackUrl, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/star-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ key, track_url: trackUrl || undefined, artist: artist || '', title: title || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            shazamStarred[key] = true;
            shazamStarred[key.toLowerCase()] = true;
            if (data.url) {
                shazamTrackUrls[key] = data.url;
                shazamTrackUrls[key.toLowerCase()] = data.url;
            }
            await shazamLoadStatus();
        } else {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG || 'Could not star track');
        }
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
}

async function shazamSkipSingleTrack(artist, title) {
    const key = `${artist} - ${title}`;
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/skip-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist, title }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            shazamLoadStatus();
            delete shazamActionPending[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            return;
        }
        alert(data.error || SHAZAM_ACTION_REJECTED_MSG || 'Skip failed');
    } catch (e) {
        alert('Error: ' + (e.message || 'Request failed'));
    }
    delete shazamActionPending[key];
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
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
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            delete shazamActionPending[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            return;
        }
        if (data.status === 'started') {
            shazamShowSyncProgress();
            const poll = setInterval(async () => {
                const pRes = await fetch('/api/shazam-sync/progress');
                const p = await pRes.json();
                shazamCurrentProgress = p;
                const el = document.getElementById('shazamProgress');
                if (el) el.textContent = p.running ? (p.message || 'Finding & starring…') : (p.error || p.message || 'Done.');
                shazamSetProgressClickable(p.running && !!p.current_key);
                if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
                if (!p.running) {
                    shazamCurrentProgress = {};
                    clearInterval(poll);
                    shazamHideSyncProgress();
                    if (p.mode === 'sync_single') {
                        if (p.done === 1 && p.url) {
                            shazamTrackUrls[key] = p.url;
                            shazamStarred[key] = true;
                            shazamStarred[key.toLowerCase()] = true;
                            if (p.soundeo_title) shazamSoundeoTitles[key] = p.soundeo_title;
                            shazamLoadStatus();
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
    if (el) el.textContent = checked.length + ' selected';
}

function shazamGetSelectedTracks() {
    const checked = document.querySelectorAll('.shazam-track-cb:checked');
    return Array.from(checked).map(cb => shazamToDownloadTracks[parseInt(cb.dataset.idx, 10)]).filter(Boolean);
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

async function shazamSyncSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    try {
        const timeRange = shazamFilterTime || 'all';
        const res = await fetch('/api/shazam-sync/run-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks, time_range: timeRange }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) { alert(data.error || SHAZAM_ACTION_REJECTED_MSG); return; }
        shazamShowSyncProgress();
        shazamProgressInterval = setInterval(shazamPollProgress, 500);
    } catch (e) { alert('Error: ' + e.message); }
}

async function shazamStopSync() {
    try {
        await fetch('/api/shazam-sync/stop', { method: 'POST' });
        const stopBtn = document.getElementById('shazamSyncStopBtn');
        if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopping…'; }
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamShowSyncProgress(initialMessage) {
    const el = document.getElementById('shazamSyncProgress');
    const textEl = document.getElementById('shazamProgress');
    const stopBtn = document.getElementById('shazamSyncStopBtn');
    if (el) el.style.display = 'flex';
    if (textEl) textEl.textContent = initialMessage || 'Starting…';
    if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = 'Stop'; }
}

function shazamHideSyncProgress() {
    const el = document.getElementById('shazamSyncProgress');
    if (el) el.style.display = 'none';
    shazamSetProgressClickable(false);
}

/** When progress has current_key, show progress bar as clickable (cursor + title + Go to row button). */
function shazamSetProgressClickable(clickable) {
    const el = document.getElementById('shazamSyncProgress');
    const btn = document.getElementById('shazamProgressGotoBtn');
    if (!el) return;
    el.classList.toggle('shazam-progress-goto-row', !!clickable);
    el.title = clickable ? 'Click to go to current row' : '';
    if (btn) btn.style.display = clickable ? 'inline-block' : 'none';
}

/** Scroll the current track row into view and briefly highlight it (anchor-link style). */
function shazamScrollToCurrentTrack(ev) {
    if (ev && ev.target && ev.target.closest && ev.target.closest('#shazamSyncStopBtn')) return;
    const key = shazamCurrentProgress && shazamCurrentProgress.current_key;
    if (!key) return;
    const rows = document.querySelectorAll('.shazam-track-table tbody tr[data-track-key]');
    for (const row of rows) {
        const rowKey = row.getAttribute('data-track-key');
        if (rowKey === key || (rowKey && rowKey.toLowerCase() === key.toLowerCase())) {
            row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            row.classList.add('shazam-row-highlight');
            clearTimeout(row._highlightTimeout);
            row._highlightTimeout = setTimeout(function () {
                row.classList.remove('shazam-row-highlight');
            }, 2500);
            break;
        }
    }
}

/** Capture current progress bar visibility and text so we can restore after re-render (e.g. row action). */
function shazamCaptureSyncProgress() {
    const el = document.getElementById('shazamSyncProgress');
    const textEl = document.getElementById('shazamProgress');
    const visible = el && el.style.display === 'flex';
    return { visible: !!visible, text: (textEl && textEl.textContent) || '' };
}

/** Restore progress bar if it was visible before a re-render, so the "Searching X of Y" cue is not lost. */
function shazamRestoreSyncProgress(captured) {
    if (!captured || !captured.visible) return;
    const el = document.getElementById('shazamSyncProgress');
    const textEl = document.getElementById('shazamProgress');
    if (el) el.style.display = 'flex';
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
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            return;
        }
        shazamShowSyncProgress(data.message || 'Syncing to Soundeo…');
        shazamProgressInterval = setInterval(shazamPollProgress, 500);
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
        if (!res.ok || data.error) { alert(data.error || SHAZAM_ACTION_REJECTED_MSG); return; }
        shazamShowSyncProgress('Syncing favorites from Soundeo…');
        shazamProgressInterval = setInterval(function () {
            fetch('/api/shazam-sync/progress').then(r => r.json()).then(p => {
                shazamCurrentProgress = p;
                const el = document.getElementById('shazamProgress');
                const stopBtn = document.getElementById('shazamSyncStopBtn');
                if (el) {
                    if (p.running) {
                        const pagePart = p.current_page != null ? `Page ${p.current_page} — ` : '';
                        el.textContent = pagePart + (p.message || 'Syncing favorites...');
                    } else {
                        el.textContent = p.error ? 'Error: ' + p.error : ('Done. ' + (p.message || 'Favorites synced.'));
                    }
                }
                shazamSetProgressClickable(p.running && !!p.current_key);
                if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
                if (!p.running) {
                    shazamCurrentProgress = {};
                    if (shazamProgressInterval) clearInterval(shazamProgressInterval);
                    shazamProgressInterval = null;
                    if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopped'; }
                    shazamHideSyncProgress();
                    if (!p.error) shazamLoadStatus();
                }
            });
        }, 500);
    } catch (e) { alert('Error: ' + e.message); }
}

function shazamPollProgress() {
    fetch('/api/shazam-sync/progress').then(r => r.json()).then(p => {
        shazamCurrentProgress = p;
        const el = document.getElementById('shazamProgress');
        const stopBtn = document.getElementById('shazamSyncStopBtn');
        const doneMsg = p.stopped
            ? `Stopped. Favorited: ${p.done || 0}, Failed: ${p.failed || 0}`
            : (p.error ? `Error: ${p.error}` : `Done. Favorited: ${p.done || 0}, Failed: ${p.failed || 0}`);
        if (el) {
            if (p.running) {
                let text = `Syncing ${p.current}/${p.total}: ${p.message || ''}`;
                if (p.last_url) {
                    const urlDisplay = p.last_url.replace(/^https?:\/\//, '');
                    text += ' — ' + urlDisplay.slice(0, 60) + (urlDisplay.length > 60 ? '…' : '');
                }
                el.textContent = text;
            } else {
                el.textContent = doneMsg;
            }
        }
        shazamSetProgressClickable(p.running && !!p.current_key);
        if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
        if (p.urls) {
            Object.assign(shazamTrackUrls, p.urls);
            Object.keys(p.urls).forEach(k => { shazamStarred[k] = true; shazamStarred[k.toLowerCase()] = true; });
        }
        if (!p.running && shazamProgressInterval) {
            shazamCurrentProgress = {};
            clearInterval(shazamProgressInterval);
            shazamProgressInterval = null;
            if (stopBtn) { stopBtn.disabled = true; stopBtn.textContent = 'Stopped'; }
            shazamHideSyncProgress();
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            shazamCompare();
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
    saveAppStateToStorage({ active_tab: tabId });
}

document.addEventListener('DOMContentLoaded', () => {
    restoreAppState();
    const savedTab = loadAppStateFromStorage().active_tab;
    if (savedTab === 'shazam' || savedTab === 'mp3') {
        switchTab(savedTab);
    }
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    const folderInput = document.getElementById('folderPath');
    if (folderInput) {
        folderInput.addEventListener('blur', () => {
            const path = (folderInput.value || '').trim();
            saveAppStateToStorage({ last_folder_path: path });
        });
    }
    shazamBootstrapLoad();
    document.querySelectorAll('.shazam-scan-range-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.shazam-scan-range-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            shazamScanRange = btn.dataset.scanRange || 'all';
            shazamSyncFavoritesFromSoundeo();
        });
    });
    document.querySelectorAll('.shazam-filter-btn[data-status]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.shazam-filter-btn[data-status]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            shazamFilterStatus = btn.dataset.status;
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
        });
    });
    const searchDropdownWrap = document.querySelector('.search-dropdown-wrap');
    const searchDropdownBtn = document.getElementById('shazamSearchDropdownBtn');
    const searchDropdownMenu = document.getElementById('shazamSearchDropdownMenu');
    if (searchDropdownBtn && searchDropdownMenu) {
        searchDropdownBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const open = searchDropdownWrap.classList.toggle('open');
            searchDropdownBtn.setAttribute('aria-expanded', open);
        });
        searchDropdownMenu.querySelectorAll('.search-dropdown-item').forEach(item => {
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                searchDropdownWrap.classList.remove('open');
                searchDropdownBtn.setAttribute('aria-expanded', 'false');
                const mode = item.dataset.mode;
                shazamSearchAllOnSoundeo(mode);
            });
        });
    }
    document.addEventListener('click', (e) => {
        if (searchDropdownWrap && !e.target.closest('.search-dropdown-wrap')) {
            searchDropdownWrap.classList.remove('open');
            if (searchDropdownBtn) searchDropdownBtn.setAttribute('aria-expanded', 'false');
        }
    });
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;
        if (btn.classList.contains('shazam-row-action-inactive')) return;
        const action = btn.dataset.action;
        if (action === 'dismiss') {
            shazamDismissTrack(btn.dataset.key, btn.dataset.url, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'undismiss') {
            shazamUndismissTrack(btn.dataset.key, btn.dataset.url, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'skip') {
            shazamSkipSingleTrack(btn.dataset.artist, btn.dataset.title);
        } else if (action === 'sync') {
            shazamSyncSingleTrack(btn.dataset.key, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'search') {
            shazamSearchSingleOnSoundeo(btn.dataset.key, btn.dataset.artist, btn.dataset.title);
        } else if (action === 'star') {
            shazamStarTrack(btn.dataset.key, btn.dataset.trackUrl, btn.dataset.artist, btn.dataset.title);
        }
    });
});

async function shazamSearchSingleOnSoundeo(key, artist, title) {
    if (shazamActionPending[key]) return;
    shazamActionPending[key] = true;
    if (shazamLastData) shazamRenderTrackList(shazamLastData);
    try {
        const res = await fetch('/api/shazam-sync/search-soundeo-single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_key: key || undefined, artist: artist || '', title: title || '' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            delete shazamActionPending[key];
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
            return;
        }
        shazamShowSyncProgress(data.message || 'Searching…');
        const poll = setInterval(async () => {
            const pRes = await fetch('/api/shazam-sync/progress');
            const p = await pRes.json();
            shazamCurrentProgress = p;
            const el = document.getElementById('shazamProgress');
            if (el) el.textContent = p.running ? (p.message || 'Searching…') : (p.error || p.message || 'Done.');
            shazamSetProgressClickable(p.running && !!p.current_key);
            if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
            if (!p.running) {
                shazamCurrentProgress = {};
                clearInterval(poll);
                shazamHideSyncProgress();
                if (p.mode === 'search_single') {
                    var trackKey = (p.key != null && p.key !== '') ? p.key : key;
                    var trackKeyLower = trackKey.toLowerCase();
                    if (p.done === 1 && p.url) {
                        shazamTrackUrls[trackKey] = p.url;
                        shazamTrackUrls[trackKeyLower] = p.url;
                        if (p.soundeo_title) {
                            shazamSoundeoTitles[trackKey] = p.soundeo_title;
                            shazamSoundeoTitles[trackKeyLower] = p.soundeo_title;
                        }
                        delete shazamNotFound[trackKey];
                        delete shazamNotFound[trackKeyLower];
                    } else if (p.done === 0 && p.failed === 1) {
                        shazamNotFound[trackKey] = true;
                        shazamNotFound[trackKeyLower] = true;
                    }
                    // NOTE: Grey must turn to orange live (no refresh). We re-render only; do NOT call shazamLoadStatus() here or it can overwrite this update. Replace of shazamNotFound happens only in shazamApplyStatus (fresh server data), not in shazamRenderTrackList.
                }
                delete shazamActionPending[key];
                if (shazamLastData) shazamRenderTrackList(shazamLastData);
            }
        }, 500);
    } catch (e) {
        delete shazamActionPending[key];
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        alert('Error: ' + e.message);
    }
}

async function shazamSearchAllOnSoundeo(searchMode) {
    try {
        const body = searchMode ? JSON.stringify({ search_mode: searchMode }) : undefined;
        const res = await fetch('/api/shazam-sync/search-soundeo-global', {
            method: 'POST',
            headers: body ? { 'Content-Type': 'application/json' } : {},
            body: body
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || data.error) {
            alert(data.error || SHAZAM_ACTION_REJECTED_MSG);
            return;
        }
        shazamShowSyncProgress(data.message || 'Searching…');
        const poll = setInterval(async () => {
            const pRes = await fetch('/api/shazam-sync/progress');
            const p = await pRes.json();
            shazamCurrentProgress = p;
            const el = document.getElementById('shazamProgress');
            if (el) {
                if (p.running) {
                    el.textContent = (p.current != null && p.total != null) ? `${p.current}/${p.total}: ${p.message || ''}` : (p.message || 'Searching…');
                } else {
                    el.textContent = p.error ? 'Error: ' + p.error : (p.message || 'Done.');
                }
            }
            shazamSetProgressClickable(p.running && !!p.current_key);
            if (p.mode === 'search_global') {
                if (p.urls) {
                    Object.assign(shazamTrackUrls, p.urls);
                    Object.keys(p.urls).forEach(k => { shazamStarred[k] = true; shazamStarred[k.toLowerCase()] = true; });
                }
                if (p.not_found) Object.assign(shazamNotFound, p.not_found);
                if (p.soundeo_titles) Object.assign(shazamSoundeoTitles, p.soundeo_titles);
            }
            if (p.running && shazamLastData) shazamRenderTrackList(shazamLastData);
            if (!p.running) {
                shazamCurrentProgress = {};
                clearInterval(poll);
                shazamHideSyncProgress();
                if (p.mode === 'search_global') {
                    if (p.not_found) Object.assign(shazamNotFound, p.not_found);
                    if (p.urls) Object.assign(shazamTrackUrls, p.urls);
                    if (p.soundeo_titles) Object.assign(shazamSoundeoTitles, p.soundeo_titles);
                    if (shazamLastData) shazamRenderTrackList(shazamLastData);
                }
                shazamLoadStatus();
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
        Object.keys(shazamNotFound).forEach(k => delete shazamNotFound[k]);
        shazamNotFound = {};
        if (shazamLastData) shazamRenderTrackList(shazamLastData);
        if (data.message) alert(data.message);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

