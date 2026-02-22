# How to avoid losing uncommitted work

## What likely happened

Your "changes since last commit" can disappear when:

1. **Git restore/checkout** – Running `git restore .` or `git checkout -- .` replaces modified files with the last committed version. Any uncommitted work is lost.
2. **Git reset --hard** – Same effect: working directory is forced to match a commit.
3. **IDE "Revert" / "Discard changes"** – Same as above; the editor runs git under the hood.
4. **Cloud sync (iCloud, Dropbox, OneDrive)** – If the project lives in a synced folder, an older version from another device can overwrite your files.
5. **Opening the wrong folder** – e.g. opening "MP3 Cleaner" instead of "SoundBridge", or a copy of the repo that’s still at an old commit.

So anything you hadn’t committed was at risk as soon as one of these happened.

## How to protect your progress

### 1. Commit often (best protection)

- After a small, working change:  
  `git add -A && git commit -m "Describe what you did"`
- At least at the end of a session or feature:  
  `git add -A && git commit -m "WIP: notification bubble and today's changes"`

Once it’s in a commit, normal "revert file" or "restore" actions won’t remove it (you’d need `git reset --hard` to lose it, and even then `git reflog` can often recover the commit).

### 2. Use a backup branch before risky steps

Before running anything that might reset or restore files:

```bash
git branch backup-$(date +%Y%m%d)    # e.g. backup-20260222
```

That keeps your current state in a named branch. If something goes wrong, you can compare or restore from it:

```bash
git diff backup-20260222 -- static/
git checkout backup-20260222 -- static/app.js   # restore one file from backup
```

### 3. Quick "save point" before experiments

```bash
git add -A && git commit -m "WIP: save point before X"
```

If the experiment goes wrong, you can always go back to this commit. No need to push unless you want a copy on the server.

### 4. Avoid losing work by default

- **Don’t run** `git restore .` or `git checkout -- .` unless you’re sure you want to throw away all local changes.
- If the project is in a **synced folder**, consider moving it to a non-synced path or pausing sync while you work, so another device can’t overwrite your files.

### 5. Check status before and after

- **Before** big or unclear git/IDE actions:  
  `git status`  
  If you see "modified" files you care about, commit or branch first.
- **After** a "revert" or "restore":  
  `git status` and `git diff`  
  If your changes are gone and you didn’t commit, they’re usually not recoverable unless you have a backup branch or backup copy.

## One-time: save today’s work now

To lock in the current state (including the re-applied notification bubble and any other uncommitted changes):

```bash
cd "/Users/keith/Desktop/Antigravity Projects/KeithKornson BV/SoundBridge"
git add -A
git status   # review what will be committed
git commit -m "Queue notification bubble: fixed overlay, in/out animations, no box"
```

After that, today’s progression is in git and much less likely to be undone by mistake.
