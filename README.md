# video-resizer

Takes your videos and makes landscape, portrait and square versions of them. Whatever doesn't fit gets a blurred copy of the video behind it, or black bars if you'd rather.

I got tired of doing this by hand for ad variants, so it's one Python file with a small window on top of ffmpeg.

## Setup

**Windows:** double-click `run.bat`. If Python or ffmpeg is missing it offers to install them for you (through winget, which comes with Windows 10/11). After installing Python you'll need to double-click it once more.

**Mac / Linux:** run `python3 video_resizer.py`. If ffmpeg is missing it offers to install it with Homebrew on a Mac, and on Linux it just tells you the apt command.

Doing it yourself works too: Python 3.9+ and [ffmpeg](https://ffmpeg.org/download.html) (with `ffprobe`) on your PATH. Nothing to pip install.

## Use

Open it with `run.bat` or `python video_resizer.py`. A window opens. Add a folder or some files, pick the sizes, and hit Start. You can choose blur or black per size.

Or from the command line:

```
python video_resizer.py "D:\ads" -s landscape square --black landscape
```

`--help` lists the rest.

## What you get

For each video, new files go in a folder next to it:

```
ads/
  clip.mp4
  landscape/clip_landscape.mp4
  square/clip_square.mp4
```

Your original is never touched. A portrait video won't get a portrait copy, and files that already exist are skipped unless you tick overwrite (`--overwrite`). Use `--flat` if you want everything in one folder instead.

Sizes are 1920x1080, 1080x1920 and 1080x1080. They're at the top of `video_resizer.py` if you want different ones. Output is H.264 (CRF 18) with AAC audio.

## License

MIT
