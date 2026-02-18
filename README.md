# 🎵 MP3 Tag Cleaner

A professional web-based application for cleaning and organizing MP3 metadata using online music databases (MusicBrainz).

## Features

- 📁 **Batch Processing**: Process entire folders of MP3 files at once
- 🔍 **Smart Lookup**: Automatically searches MusicBrainz database for clean metadata
- ✏️ **Manual Editing**: Review and edit tags before saving
- 📊 **Confidence Scoring**: See how confident the metadata match is
- 💾 **Safe Updates**: Preview changes before applying them
- 🎨 **Modern UI**: Clean, intuitive interface with real-time updates
- 🔄 **Shazam to Soundeo Sync**: Compare your Shazam library with local MP3 folders and favorite missing tracks on Soundeo for later download

## Installation

1. **Install Python** (3.8 or higher)
   - Download from [python.org](https://www.python.org/downloads/)

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **For Shazam-Soundeo Sync** (macOS only): Install Chrome; Selenium will auto-install ChromeDriver.

## Usage

1. **Start the Application**
   ```bash
   python app.py
   ```

2. **Open Your Browser**
   - Navigate to: `http://localhost:5002`

3. **Clean Your MP3s**
   - Paste your folder path (e.g., `/Users/keith/Music/Downloads` or `C:\Music\Downloads`)
   - Click "Scan Folder" to find all MP3 files
   - Click "Lookup All Metadata" to search for clean tags
   - Review the suggested changes
   - Click "Save All Changes" to update your files

## How It Works

1. **Folder Scanning**: The app recursively scans your folder for all MP3 files
2. **Current Tag Reading**: Extracts existing metadata from each file
3. **Online Lookup**: Searches MusicBrainz using:
   - Existing tags (title, artist)
   - Filename patterns (Artist - Title format)
   - Fuzzy matching to find the best match
4. **Confidence Scoring**: Shows how confident the match is (High/Medium/Low)
5. **Preview & Edit**: Review all changes before saving
6. **Batch Update**: Updates all MP3 tags with the cleaned metadata

## Metadata Sources

- **MusicBrainz**: Free, open-source music database with millions of tracks
- Supports: Title, Artist, Album, Year, Genre

## Shazam to Soundeo Sync (macOS)

Compare your Shazam library with local MP3 folders and favorite missing tracks on [Soundeo](https://soundeo.com/) for later download:

1. Open the **Shazam to Soundeo Sync** section
2. Add destination folders (where your MP3s live)
3. Click **Save Soundeo Session** → log in to Soundeo in the browser → click **I have logged in**
4. Click **Compare Shazam vs Local** to see which tracks you need
5. Click **Sync to Soundeo** to search and favorite missing tracks (you download manually later)

Requires Shazam for Mac and Chrome. Compare results, favorited track links, and skip list persist across refresh (see [docs/STATE_PERSISTENCE.md](docs/STATE_PERSISTENCE.md)).

## Tips

- For best results, ensure your filenames follow patterns like:
  - `Artist - Title.mp3`
  - `Title - Artist.mp3`
  - `01 Title.mp3`
- Review low-confidence matches (< 60%) before saving
- You can manually edit any field before saving
- The app preserves your original files until you click Save

## Technical Details

- **Backend**: Python Flask
- **MP3 Library**: Mutagen (ID3 tag reading/writing)
- **API**: MusicBrainz Web Service
- **Frontend**: Vanilla JavaScript, modern CSS

## Troubleshooting

**"Invalid folder path" error**
- Make sure you paste the full absolute path
- On Mac: `/Users/yourname/Music/folder`
- On Windows: `C:\Users\YourName\Music\folder`

**"No metadata found"**
- Try editing the title/artist fields manually
- Click "Lookup" again with better information
- Some obscure tracks may not be in the database

**API Rate Limiting**
- MusicBrainz has rate limits (1 request/second)
- The app automatically throttles requests
- For large batches, be patient

## Future Enhancements

- [ ] Audio fingerprinting with AcoustID (more accurate matching)
- [ ] Album artwork download
- [ ] Batch rename files based on clean tags
- [ ] Support for other audio formats (FLAC, M4A)
- [ ] Local database caching to reduce API calls

## License

MIT License - Feel free to use and modify!

## Credits

- Music metadata provided by [MusicBrainz](https://musicbrainz.org/)
- Built with ❤️ for music lovers who hate messy MP3 tags

