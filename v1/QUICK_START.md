# 🚀 Quick Start Guide

## Your MP3 Tag Cleaner is Ready!

### ✅ What's Been Built

A complete web-based MP3 tag cleaning application with:

1. **Flask Backend** (`app.py`)
   - Scans folders for MP3 files
   - Reads existing ID3 tags
   - Queries MusicBrainz API for clean metadata
   - Updates MP3 files with new tags
   - Batch processing support

2. **Modern Web Interface** (`templates/index.html`, `static/`)
   - Drag-and-drop folder selection
   - Real-time file list display
   - Before/after tag comparison
   - Confidence scoring for matches
   - Individual or batch operations

3. **Smart Metadata Lookup**
   - Uses MusicBrainz database (millions of tracks)
   - Searches by existing tags
   - Parses filenames intelligently
   - Handles common patterns (Artist - Title, etc.)

## 🎯 How to Use

### Step 1: Start the Application

**Option A: Using the script (easiest)**
```bash
./run.sh
```

**Option B: Manual start**
```bash
python3 app.py
```

### Step 2: Open in Browser

Navigate to: **http://localhost:5002**

### Step 3: Clean Your MP3s

1. **Paste your folder path** in the input field
   - Mac example: `/Users/keith/Music/Downloads`
   - Windows example: `C:\Users\Keith\Music\Downloads`

2. **Click "Scan Folder"**
   - The app will find all MP3 files recursively

3. **Review Current Tags**
   - You'll see all files with their current metadata
   - Old tags are shown with strikethrough
   - New suggested tags appear below

4. **Lookup Metadata**
   - Click "Lookup All Metadata" to search MusicBrainz for all files
   - Or click individual "Lookup" buttons
   - Watch the confidence scores (High = 80%+, Medium = 60-80%, Low < 60%)

5. **Edit if Needed**
   - Click in any field to manually adjust
   - Re-lookup after editing for better matches

6. **Save Changes**
   - Click "Save All Changes" to update all files at once
   - Or click individual "Save" buttons
   - Your original files are updated with new metadata

## 📝 Example Folder Paths

**Mac:**
```
/Users/keith/Music/Downloads
/Users/keith/Desktop/MP3s
/Volumes/External/Music
```

**Windows:**
```
C:\Users\Keith\Music\Downloads
D:\MP3 Collection
C:\Users\Keith\Desktop\Music
```

## 💡 Tips for Best Results

1. **Filename Patterns**: The app recognizes these patterns:
   - `Artist - Title.mp3`
   - `Title - Artist.mp3`
   - `01 Artist - Title.mp3`
   - `Artist_-_Title.mp3`

2. **Low Confidence Matches**: 
   - Review matches under 60% carefully
   - Edit the title/artist and re-lookup
   - Some obscure tracks may not be in the database

3. **Batch Processing**:
   - Process folders in smaller batches for faster results
   - MusicBrainz has rate limits (1 request/second)
   - Be patient with large collections

4. **Backup Important Files**:
   - The app modifies files in place
   - Consider backing up before major changes

## 🛑 Stopping the Server

Press `Ctrl+C` in the terminal to stop the Flask server

## 🐛 Troubleshooting

**Can't connect to localhost:5002**
- Check if the server is running (look for "Running on http://127.0.0.1:5002")
- Try http://127.0.0.1:5002 instead

**"Invalid folder path"**
- Make sure you use the full absolute path
- Check for typos in the path
- Ensure the folder exists and is accessible

**No metadata found**
- Try editing title/artist fields with better information
- Some tracks are too obscure for the database
- Check if filename follows recognizable patterns

**Server crashes**
- Check the terminal for error messages
- Make sure all dependencies are installed
- Restart with `python3 app.py`

## 🎉 You're All Set!

Your MP3 Tag Cleaner is running and ready to clean up those messy MP3 files!

**Current Status**: ✅ Server running on http://localhost:5000

Open your browser and start cleaning! 🎵

