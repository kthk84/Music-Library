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
        <span style="text-align: center;" title="Match confidence">✓%</span>
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
        // Use rank_score if available (more accurate), otherwise use confidence
        const score = file.rank_score !== undefined ? file.rank_score : file.confidence;
        const percentage = Math.round(Math.min(Math.max(score * 100, 0), 100));
        
        let badgeClass = 'confidence-low';
        let badgeColor = '#ef4444'; // red
        let icon = '⚠️';
        
        if (percentage >= 80) {
            badgeClass = 'confidence-high';
            badgeColor = '#10b981'; // green
            icon = '✓';
        } else if (percentage >= 60) {
            badgeClass = 'confidence-medium';
            badgeColor = '#f59e0b'; // orange
            icon = '~';
        }
        
        confidenceBadge = `
            <div class="confidence-badge-large ${badgeClass}" 
                 style="background: ${badgeColor}15; color: ${badgeColor}; border: 2px solid ${badgeColor};"
                 title="Match confidence: ${percentage}%">
                <span class="confidence-icon">${icon}</span>
                <span class="confidence-number">${percentage}%</span>
            </div>
        `;
    } else if (status === 'processing') {
        confidenceBadge = '<span class="spinner-small" title="Looking up...">⏳</span>';
    } else if (status === 'lookup_error') {
        confidenceBadge = '<span style="color: var(--danger); font-size: 1.2rem;" title="Not found">❌</span>';
    } else if (status === 'success') {
        confidenceBadge = '<span style="color: var(--success); font-size: 1.2rem;" title="Saved">💾</span>';
    } else if (file.has_spam) {
        confidenceBadge = '<span style="color: var(--warning); font-size: 1.2rem;" title="Has spam">⚠️</span>';
    }

    // Album cover thumbnail
    const cover = file.newCover || file.cover;
    let coverHtml = '';
    if (cover) {
        coverHtml = `<img src="data:image/jpeg;base64,${cover}" 
                          class="album-cover-thumb" 
                          style="width: 50px; height: 50px; max-width: 50px; max-height: 50px; object-fit: cover;"
                          title="Click to view full size"
                          data-cover-index="${index}">`;
    } else {
        coverHtml = `<div class="no-cover" title="No album cover - click Lookup to find it">
                        📀
                     </div>`;
    }
    
    div.innerHTML = `
        <div class="cover-cell" style="text-align: center; width: 50px; height: 52px; display: flex; align-items: center; justify-content: center; overflow: hidden;">
            ${coverHtml}
        </div>
        <div style="display: flex; align-items: center; justify-content: center; height: 52px;">
            <button onclick="togglePlay(${index})" id="play-btn-${index}" class="play-btn" style="background: linear-gradient(135deg, var(--primary) 0%, #7c3aed 100%); border: none; cursor: pointer; font-size: 1.3rem; padding: 0; width: 46px; height: 46px; border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; box-shadow: 0 3px 10px rgba(99, 102, 241, 0.4); transition: all 0.2s; flex-shrink: 0;">
                <span style="display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; padding-left: 3px;">▶</span>
            </button>
            <audio id="audio-${index}" src="/file/${encodeURIComponent(file.filename)}" preload="metadata"></audio>
        </div>
        <div style="display: flex; flex-direction: column; gap: 1px; height: 52px; justify-content: center; overflow: visible; min-width: 0; padding: 2px 0;">
            <div class="file-name" title="${file.filename}" style="margin: 0; margin-bottom: 0; height: auto; font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #1f2937; font-weight: 500; line-height: 1.2;">${filenameDisplay}</div>
            <div style="display: flex; align-items: center; gap: 10px; height: 20px;">
                <div class="progress-container" onclick="scrubAudio(event, ${index})" style="flex: 1; height: 10px; background: #e5e7eb; border-radius: 5px; cursor: pointer; position: relative; box-shadow: inset 0 1px 3px rgba(0,0,0,0.12); padding: 2px; min-width: 100px;">
                    <div id="progress-${index}" class="progress-bar" style="width: 0%; height: 100%; background: linear-gradient(90deg, var(--primary) 0%, #7c3aed 100%); border-radius: 4px; transition: width 0.1s; box-shadow: 0 1px 4px rgba(99, 102, 241, 0.5);"></div>
                </div>
                <span id="time-${index}" style="font-size: 0.78rem; color: #374151; min-width: 45px; font-family: 'SF Mono', 'Monaco', 'Courier New', monospace; text-align: right; font-weight: 600; letter-spacing: 0.3px; line-height: 1.4; display: flex; align-items: center; justify-content: flex-end;">0:00</span>
            </div>
        </div>
        <div class="${titleClass}">${titleDisplay}</div>
        <div class="${artistClass}">${artistDisplay}</div>
        <div class="${albumClass}">${albumDisplay}</div>
        <div class="${yearClass}">${yearDisplay}</div>
        <div class="${genreClass}">${genreDisplay}</div>
        <div id="confidence-${index}" style="text-align: center; min-height: 52px; max-height: 52px; display: flex; justify-content: center; align-items: center;">
            ${confidenceBadge}
        </div>
        <div class="file-actions">
            <button onclick="lookupMetadata(${index}, true)" class="btn btn-primary btn-small" title="Auto lookup (uses best match)">
                🔍
            </button>
            <button onclick="lookupMetadata(${index}, false)" class="btn btn-secondary btn-small" title="Manual lookup (choose from results)">
                📝
            </button>
            <button onclick="revertLookup(${index})" class="btn btn-warning btn-small" title="Revert to original tags" style="display: ${file.newTitle || file.newArtist ? 'inline-flex' : 'none'};">
                ↶
            </button>
            <button onclick="viewMetadata(${index})" class="btn btn-secondary btn-small" title="View all metadata">
                📋
            </button>
            <button onclick="saveFile(${index})" class="btn btn-success btn-small" title="Save changes">
                💾
            </button>
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
    console.log('=== TOGGLE PLAY DEBUG ===');
    console.log('Index:', index);
    console.log('Current file:', currentFiles[index]);
    
    const audio = document.getElementById(`audio-${index}`);
    const playBtn = document.getElementById(`play-btn-${index}`);
    
    if (!audio) {
        console.error('❌ Audio element not found for index', index);
        alert('Audio element not found!');
        return;
    }
    
    console.log('Audio element:', audio);
    console.log('Audio src:', audio.src);
    console.log('Audio readyState:', audio.readyState);
    console.log('Audio networkState:', audio.networkState);
    console.log('Audio error:', audio.error);
    
    if (audio.error) {
        console.error('❌ Audio has error:', audio.error.code, audio.error.message);
        const errorMessages = {
            1: 'MEDIA_ERR_ABORTED - Loading was aborted',
            2: 'MEDIA_ERR_NETWORK - Network error',
            3: 'MEDIA_ERR_DECODE - Decode error',
            4: 'MEDIA_ERR_SRC_NOT_SUPPORTED - Source not supported'
        };
        alert('Audio Error: ' + errorMessages[audio.error.code] + '\n\nSrc: ' + audio.src);
        return;
    }
    
    // Stop any other playing audio
    if (currentlyPlaying !== null && currentlyPlaying !== index) {
        console.log('Stopping currently playing track:', currentlyPlaying);
        const otherAudio = document.getElementById(`audio-${currentlyPlaying}`);
        const otherBtn = document.getElementById(`play-btn-${currentlyPlaying}`);
        if (otherAudio) {
            otherAudio.pause();
            otherAudio.currentTime = 0;
        }
        if (otherBtn) {
            otherBtn.innerHTML = '<span style="display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; padding-left: 3px;">▶</span>';
        }
        // Reset progress bar
        const otherProgress = document.getElementById(`progress-${currentlyPlaying}`);
        const otherTime = document.getElementById(`time-${currentlyPlaying}`);
        if (otherProgress) otherProgress.style.width = '0%';
        if (otherTime) otherTime.textContent = '0:00';
    }
    
    if (audio.paused) {
        console.log('▶️ Attempting to play audio...');
        audio.play().then(() => {
            console.log('✅ Audio playing successfully!', audio.src);
            playBtn.innerHTML = '<span style="display: flex; align-items: center; justify-content: center; width: 100%; height: 100%;">⏸</span>';
            currentlyPlaying = index;
        }).catch(error => {
            console.error('❌ Error playing audio:', error);
            console.error('Error name:', error.name);
            console.error('Error message:', error.message);
            console.error('Audio src:', audio.src);
            console.error('Audio readyState:', audio.readyState);
            alert('Could not play audio:\n' + error.message + '\n\nSrc: ' + audio.src);
        });
        
        // Remove old listeners if they exist
        if (timeUpdateListeners[index]) {
            audio.removeEventListener('timeupdate', timeUpdateListeners[index]);
        }
        if (endedListeners[index]) {
            audio.removeEventListener('ended', endedListeners[index]);
        }
        
        // Add new listeners
        timeUpdateListeners[index] = function() {
            updateProgress(index);
        };
        endedListeners[index] = function() {
            console.log('🏁 Audio ended for index', index);
            playBtn.innerHTML = '<span style="display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; padding-left: 3px;">▶</span>';
            currentlyPlaying = null;
            document.getElementById(`progress-${index}`).style.width = '0%';
            document.getElementById(`time-${index}`).textContent = '0:00';
        };
        
        audio.addEventListener('timeupdate', timeUpdateListeners[index]);
        audio.addEventListener('ended', endedListeners[index]);
        
        // Add error listener
        audio.addEventListener('error', function(e) {
            console.error('❌ Audio error event:', e);
            console.error('Audio error object:', audio.error);
        });
    } else {
        console.log('⏸️ Pausing audio');
        audio.pause();
        playBtn.innerHTML = '<span style="display: flex; align-items: center; justify-content: center; width: 100%; height: 100%; padding-left: 3px;">▶</span>';
        currentlyPlaying = null;
    }
    
    console.log('=== END DEBUG ===');
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
let shazamComparePollInterval = null;
let shazamSectionOpen = true;
let shazamFolderInputs = [];
let shazamProgressInterval = null;
let shazamTrackUrls = {};
/** Per-track "starred in Soundeo" state (key: "Artist - Title"). Restored from status on load. */
let shazamStarred = {};

function toggleShazamSection() {
    shazamSectionOpen = !shazamSectionOpen;
    const content = document.getElementById('shazamSyncContent');
    const icon = document.getElementById('shazamToggleIcon');
    if (content) content.style.display = shazamSectionOpen ? 'block' : 'none';
    if (icon) icon.textContent = shazamSectionOpen ? '▼' : '▶';
}

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
    const hasSession = !!(cfg.soundeo_cookies_path || cfg.soundeo_cookies_path_resolved);
    if (statusEl) statusEl.textContent = hasSession ? 'Soundeo session: Saved' : 'Soundeo session: Not saved';
    if (pathEl) {
        const resolved = cfg.soundeo_cookies_path_resolved || cfg.soundeo_cookies_path;
        if (resolved) {
            pathEl.style.display = 'block';
            pathEl.textContent = 'Session file: ' + resolved;
        } else {
            pathEl.style.display = 'none';
        }
    }
    const headedCb = document.getElementById('shazamHeadedMode');
    if (headedCb) headedCb.checked = cfg.headed_mode !== false;
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
}

function shazamRemoveFolder(idx) {
    shazamFolderInputs.splice(idx, 1);
    shazamRenderFolderList();
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
        }
    } catch (e) {
        hideLoading();
        alert('Error: ' + e.message);
    }
}

async function shazamSaveSettings() {
    const inputs = document.querySelectorAll('#shazamFolderList input');
    shazamFolderInputs = Array.from(inputs).map(i => i.value.trim()).filter(Boolean);
    const headedCb = document.getElementById('shazamHeadedMode');
    const headedMode = headedCb ? headedCb.checked : true;
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                destination_folders: shazamFolderInputs,
                headed_mode: headedMode
            })
        });
        alert('Settings saved.');
        shazamRenderFolderList();
        shazamLoadSettings();
    } catch (e) {
        alert('Error saving: ' + e.message);
    }
}

async function shazamSaveSession() {
    try {
        const res = await fetch('/api/soundeo/start-save-session', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.error) {
            alert(data.error);
            return;
        }
        document.getElementById('shazamLoggedInBtn').style.display = 'inline-block';
        alert('Browser opened. Log in to Soundeo. Wait until you see the main Soundeo page (not the login form), then click "I have logged in".');
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

async function shazamSessionSaved() {
    try {
        await fetch('/api/soundeo/session-saved', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        document.getElementById('shazamLoggedInBtn').style.display = 'none';
        document.getElementById('soundeoSessionStatus').textContent = 'Soundeo session: Saved';
        shazamLoadSettings();
    } catch (e) {
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
        // #region agent log
        fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamCompare',message:'compare response',data:{running:!!data.running,error:data.error||null,hasShazamCount:data.shazam_count!=null},timestamp:Date.now(),hypothesisId:'H1'})}).catch(function(){});
        // #endregion
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
            // #region agent log
            fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamCompare',message:'showing progress',data:{},timestamp:Date.now(),hypothesisId:'H2'})}).catch(function(){});
            // #endregion
            shazamShowCompareProgress(true, 0, 0, 'Starting compare...');
            const start = Date.now();
            setTimeout(function () { shazamComparePoll(start); }, 120);
            shazamComparePollInterval = setInterval(function () { shazamComparePoll(start); }, 500);
            return;
        }
        // #region agent log
        fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamCompare',message:'immediate status',hypothesisId:'H5',data:{have_len:(data.have_locally&&data.have_locally.length)||0,to_download_count:data.to_download_count},timestamp:Date.now()})}).catch(function(){});
        // #endregion
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
            const cur = sp.current || 0;
            const tot = sp.total || 0;
            const msg = sp.message || (tot > 0 ? 'Scanning files...' : 'Discovering files...');
            // #region agent log
            fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamComparePoll',message:'poll running',data:{cur:cur,tot:tot},timestamp:Date.now(),hypothesisId:'H4'})}).catch(function(){});
            // #endregion
            shazamShowCompareProgress(true, cur, tot, tot > 0 ? (cur.toLocaleString() + ' / ' + tot.toLocaleString() + ' files') : msg);
            return;
        }
        if (shazamComparePollInterval) {
            clearInterval(shazamComparePollInterval);
            shazamComparePollInterval = null;
        }
        shazamShowCompareProgress(false);
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
    // #region agent log
    var _u = data.urls ? Object.keys(data.urls) : [];
    var _s = data.starred ? Object.keys(data.starred) : [];
    fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamApplyStatus',message:'applying',hypothesisId:'H3H4',data:{urls_keys:_u,starred_keys:_s,have_len:(data.have_locally&&data.have_locally.length)||0,to_download_count:data.to_download_count},timestamp:Date.now()})}).catch(function(){});
    // #endregion
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
    const statsEl = document.getElementById('shazamFolderStats');
    if (statsEl && data.folder_stats && data.folder_stats.length > 0) {
        statsEl.innerHTML = '<strong>Per folder:</strong> ' + data.folder_stats.map(function (f) {
            var name = f.path.split(/[/\\]/).filter(Boolean).pop() || f.path;
            return name + ': ' + f.scanned + ' scanned, ' + f.matched + ' matched';
        }).join(' · ');
        statsEl.style.display = 'block';
    } else if (statsEl) {
        statsEl.style.display = 'none';
    }
    const syncBtn = document.getElementById('shazamSyncBtn');
    if (syncBtn) syncBtn.disabled = !(data.to_download && data.to_download.length > 0);
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.urls && (!data.starred || Object.keys(data.starred).length === 0)) { Object.keys(data.urls).forEach(k => { shazamStarred[k] = true; }); }
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
let shazamCurrentlyPlaying = null;
let shazamAudioEl = null;

function shazamApplyFilters(merged) {
    const now = Math.floor(Date.now() / 1000);
    const week = 7 * 86400, quarter = 91 * 86400, year = 365 * 86400;
    let out = merged;
    if (shazamFilterTime !== 'all') {
        const cutoff = now - (shazamFilterTime === 'week' ? week : shazamFilterTime === 'quarter' ? quarter : year);
        out = out.filter(t => (t.shazamed_at ?? 0) >= cutoff);
    }
    if (shazamFilterStatus !== 'all') {
        out = out.filter(t => t.status === shazamFilterStatus);
    }
    return out;
}

function shazamRenderTrackList(data) {
    if (!data) data = {};
    shazamLastData = data;
    if (data.urls) Object.assign(shazamTrackUrls, data.urls);
    if (data.starred) Object.assign(shazamStarred, data.starred);
    if (data.urls && (!data.starred || Object.keys(data.starred).length === 0)) { Object.keys(data.urls).forEach(k => { shazamStarred[k] = true; }); }
    // #region agent log
    var _rows = (data.to_download || []).slice(0, 3);
    var _sample = _rows.map(function(r) { var k = r.artist + ' - ' + r.title; return { key: k, hasUrl: !!shazamTrackUrls[k], hasStarred: !!shazamStarred[k] }; });
    fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamRenderTrackList',message:'sample keys',hypothesisId:'H4',data:{sample:_sample},timestamp:Date.now()})}).catch(function(){});
    // #endregion
    const have = (data.have_locally || []).map(t => ({ ...t, status: 'have' }));
    const toDl = (data.to_download || []).map((t, i) => ({ ...t, status: 'todl', _idx: i }));
    shazamToDownloadTracks = data.to_download || [];
    const el = document.getElementById('shazamTrackList');
    const selectionBar = document.getElementById('shazamSelectionBar');
    if (!el) return;
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
    if (have.length === 0 && toDl.length === 0) {
        if (!data.error) {
            html += '<p class="shazam-info-msg">Click <strong>Fetch Shazam</strong> to load tracks, add destination folders in Settings, then <strong>Compare</strong>.</p>';
        }
        el.innerHTML = html || '<p class="shazam-info-msg">Run Compare to see tracks.</p>';
        if (selectionBar) selectionBar.style.display = 'none';
        return;
    }
    const merged = [...have, ...toDl];
    merged.sort((a, b) => { const sa = a.shazamed_at ?? 0; const sb = b.shazamed_at ?? 0; return sb - sa; });
    const filtered = shazamApplyFilters(merged);
    const hasPlayable = have.some(t => t.filepath);
    html += '<table class="shazam-track-table"><thead><tr><th>Status</th><th>Shazam</th><th>Artist</th><th>Title</th>';
    if (hasPlayable) html += '<th class="shazam-play-col"></th>';
    html += '<th>Starred</th><th>Link</th>';
    if (filtered.some(r => r.status === 'todl')) html += '<th class="shazam-select-col"><input type="checkbox" id="shazamSelectAll" onchange="shazamToggleSelectAll(this)" title="Select all" /></th>';
    html += '</tr></thead><tbody>';
    filtered.forEach((row, i) => {
        const when = shazamFormatRelativeTime(row.shazamed_at);
        const isTodl = row.status === 'todl';
        const idx = row._idx;
        const key = `${row.artist} - ${row.title}`;
        const url = shazamTrackUrls[key] || (data.urls || {})[key];
        const starred = !!(shazamStarred[key] || (data.starred || {})[key]);
        const starredCell = isTodl ? (starred ? '<td class="shazam-starred">★ Starred</td>' : '<td class="shazam-not-starred">—</td>') : '<td></td>';
        const link = isTodl ? (url ? `<a href="${escapeHtml(url)}" target="_blank">Open in Soundeo</a>` : '-') : '';
        let playCell = '';
        if (hasPlayable) {
            if (row.filepath) {
                const pathB64 = btoa(unescape(encodeURIComponent(row.filepath)));
                playCell = `<td class="shazam-play-col"><button type="button" class="shazam-play-btn" data-path="${escapeHtml(pathB64)}" onclick="shazamTogglePlay(this.dataset.path, this)" title="Play">▶</button></td>`;
            } else {
                playCell = '<td class="shazam-play-col"></td>';
            }
        }
        if (isTodl) {
            html += `<tr class="to-download" data-idx="${idx}"><td>To download</td><td class="shazam-when">${escapeHtml(when)}</td><td>${escapeHtml(row.artist)}</td><td>${escapeHtml(row.title)}</td>${playCell}${starredCell}<td>${link}</td>`;
            html += `<td class="shazam-select-col"><input type="checkbox" class="shazam-track-cb" data-idx="${idx}" onchange="shazamUpdateSelectionCount()" /></td>`;
        } else {
            const haveStarred = !!(shazamStarred[key] || (data.starred || {})[key]);
            const haveStarredCell = haveStarred ? '<td class="shazam-starred">★</td>' : '<td class="shazam-not-starred">—</td>';
            html += `<tr class="have-local"><td>✓ Have</td><td class="shazam-when">${escapeHtml(when)}</td><td>${escapeHtml(row.artist)}</td><td>${escapeHtml(row.title)}</td>${playCell}${haveStarredCell}<td></td>`;
            if (filtered.some(r => r.status === 'todl')) html += '<td></td>';
        }
        html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
    if (selectionBar) selectionBar.style.display = filtered.some(r => r.status === 'todl') ? 'flex' : 'none';
    shazamUpdateSelectionCount();
}

function shazamTogglePlay(pathB64, btn) {
    const streamUrl = '/api/shazam-sync/stream-file?path=' + encodeURIComponent(pathB64);
    if (!shazamAudioEl) {
        shazamAudioEl = document.createElement('audio');
    }
    const playingBtn = document.querySelector('.shazam-play-btn.playing');
    if (playingBtn) {
        playingBtn.textContent = '▶';
        playingBtn.classList.remove('playing');
    }
    if (shazamCurrentlyPlaying === pathB64) {
        shazamAudioEl.pause();
        shazamCurrentlyPlaying = null;
        return;
    }
    shazamAudioEl.src = streamUrl;
    shazamAudioEl.play().then(() => {
        btn.textContent = '⏸';
        btn.classList.add('playing');
        shazamCurrentlyPlaying = pathB64;
    }).catch(() => {});
    shazamAudioEl.onended = () => {
        if (btn) { btn.textContent = '▶'; btn.classList.remove('playing'); }
        shazamCurrentlyPlaying = null;
    };
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

async function shazamSyncSelected() {
    const tracks = shazamGetSelectedTracks();
    if (!tracks.length) { alert('Select tracks first'); return; }
    try {
        const res = await fetch('/api/shazam-sync/run-soundeo', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tracks }),
        });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        shazamShowPreviewAndControls(true);
        document.getElementById('shazamVideoFeed').src = '/api/shazam-sync/video-feed';
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

function shazamHidePreview() {
    const container = document.querySelector('.shazam-preview-container');
    const img = document.getElementById('shazamVideoFeed');
    const btn = document.getElementById('shazamHidePreviewBtn');
    if (!container || !btn) return;
    if (container.classList.contains('hide-video')) {
        container.classList.remove('hide-video');
        if (img) img.src = '/api/shazam-sync/video-feed';
        btn.textContent = 'Hide preview';
    } else {
        container.classList.add('hide-video');
        if (img) img.src = '';
        btn.textContent = 'Show preview';
    }
}

function shazamShowPreviewAndControls(showVideo) {
    const liveView = document.getElementById('shazamLiveView');
    const container = document.querySelector('.shazam-preview-container');
    const stopBtn = document.getElementById('shazamSyncStopBtn');
    const hideBtn = document.getElementById('shazamHidePreviewBtn');
    if (liveView) liveView.style.display = 'block';
    if (container) {
        if (showVideo) container.classList.remove('hide-video');
    }
    if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = 'Stop sync'; }
    if (hideBtn) hideBtn.textContent = 'Hide preview';
}

function escapeHtml(s) {
    if (!s) return '';
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

async function shazamRunSync() {
    try {
        const res = await fetch('/api/shazam-sync/run-soundeo', { method: 'POST', headers: { 'Content-Type': 'application/json' } });
        const data = await res.json();
        if (data.error) {
            alert(data.error);
            return;
        }
        shazamShowPreviewAndControls(true);
        document.getElementById('shazamVideoFeed').src = '/api/shazam-sync/video-feed';
        shazamProgressInterval = setInterval(shazamPollProgress, 500);
    } catch (e) {
        alert('Error: ' + e.message);
    }
}

function shazamPollProgress() {
    fetch('/api/shazam-sync/progress').then(r => r.json()).then(p => {
        const el = document.getElementById('shazamProgress');
        const stopBtn = document.getElementById('shazamSyncStopBtn');
        const doneMsg = p.stopped
            ? `Stopped. Favorited: ${p.done || 0}, Failed: ${p.failed || 0}`
            : (p.error ? `Error: ${p.error}` : `Done. Favorited: ${p.done || 0}, Failed: ${p.failed || 0}`);
        if (el) el.textContent = p.running
            ? `Syncing ${p.current}/${p.total}: ${p.message || ''}`
            : doneMsg;
        if (p.urls) {
            Object.assign(shazamTrackUrls, p.urls);
            Object.keys(p.urls).forEach(k => { shazamStarred[k] = true; });
        }
        if (!p.running && shazamProgressInterval) {
            clearInterval(shazamProgressInterval);
            shazamProgressInterval = null;
            document.getElementById('shazamVideoFeed').src = '';
            document.getElementById('shazamLiveView').style.display = 'none';
            if (stopBtn) { stopBtn.disabled = false; stopBtn.textContent = 'Stop sync'; }
            const container = document.querySelector('.shazam-preview-container');
            if (container) container.classList.remove('hide-video');
            const hideBtn = document.getElementById('shazamHidePreviewBtn');
            if (hideBtn) hideBtn.textContent = 'Hide preview';
            // #region agent log
            fetch('http://127.0.0.1:7242/ingest/d42056e9-4ace-4e98-8de1-2a37a10359ad',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'app.js:shazamPollProgress',message:'sync stopped',hypothesisId:'H5',data:{progress_urls_keys:p.urls?Object.keys(p.urls):[],calling:'shazamCompare'},timestamp:Date.now()})}).catch(function(){});
            // #endregion
            shazamCompare();
        }
    }).catch(() => {});
}

document.addEventListener('DOMContentLoaded', () => {
    restoreAppState();
    const folderInput = document.getElementById('folderPath');
    if (folderInput) {
        folderInput.addEventListener('blur', () => {
            const path = (folderInput.value || '').trim();
            saveAppStateToStorage({ last_folder_path: path });
        });
    }
    shazamBootstrapLoad();
    document.querySelectorAll('.shazam-filter-btn[data-time]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.shazam-filter-btn[data-time]').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            shazamFilterTime = btn.dataset.time;
            if (shazamLastData) shazamRenderTrackList(shazamLastData);
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
});

