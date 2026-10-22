#!/usr/bin/env python3
"""
video_resizer - make landscape / portrait / square versions of your videos.

Run with no arguments to open the window, or pass paths to use it from the
command line (see --help). Needs ffmpeg and ffprobe on your PATH.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

# Edit these if you need different sizes (e.g. "vertical45": (1080, 1350)).
# A size only counts as landscape/portrait/square if its name says so, since the
# tool skips the orientation your video already has.
SIZES = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}
EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
BLUR = 40   # strength of the blurred background
CRF = 18    # x264 quality, lower = better quality / bigger file
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hides console popups on Windows


# ---------------------------------------------------------------- ffmpeg setup

def _found():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def refresh_path():
    """Windows: installers only change PATH for newly opened programs, so re-read it."""
    import winreg
    parts = []
    for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, sub) as key:
                parts.append(os.path.expandvars(winreg.QueryValueEx(key, "Path")[0]))
        except OSError:
            pass
    links = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links")
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *parts, links])


def have_ffmpeg():
    if not _found() and sys.platform == "win32":
        refresh_path()  # it may have been installed since this program was opened
    return _found()


def install_ffmpeg(log=print):
    """Try to install ffmpeg with winget (Windows) or Homebrew (macOS)."""
    if sys.platform == "win32":
        cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e",
               "--accept-package-agreements", "--accept-source-agreements"]
    elif sys.platform == "darwin":
        cmd = ["brew", "install", "ffmpeg"]
    else:
        log("Install ffmpeg with your package manager, for example: sudo apt install ffmpeg")
        return False
    if not shutil.which(cmd[0]):
        log(f"{cmd[0]} isn't available on this machine.\n"
            "Get ffmpeg from https://ffmpeg.org/download.html and make sure it is on your PATH.")
        return False
    log("Installing ffmpeg, this can take a minute...")
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                            creationflags=NO_WINDOW)
    if have_ffmpeg():
        log("ffmpeg is ready.")
        return True
    log("Couldn't install ffmpeg automatically.\n" + (result.stdout + result.stderr).strip()[-400:])
    log("Get it from https://ffmpeg.org/download.html and make sure it is on your PATH.")
    return False


def offer_install_cli():
    print("ffmpeg isn't installed.")
    if sys.stdin.isatty() and input("Install it now? [y/N] ").strip().lower() == "y":
        return install_ffmpeg()
    return False


# ---------------------------------------------------------------- core

def probe(f):
    """Return the displayed (width, height) of a video, rotation included."""
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
         "-of", "json", str(f)],
        creationflags=NO_WINDOW)
    s = json.loads(out)["streams"][0]
    w, h = s["width"], s["height"]
    rot = s.get("tags", {}).get("rotate")
    for sd in s.get("side_data_list", []):
        rot = sd.get("rotation", rot)
    if rot is not None and abs(int(float(rot))) % 180 == 90:
        w, h = h, w  # phone videos are often stored sideways and flagged as rotated
    return w, h


def orientation(w, h):
    return "landscape" if w > h else "portrait" if h > w else "square"


def build_filter(W, H, bg):
    fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease"
    if bg == "black":
        return f"[0:v]{fit},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[v]"
    return (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma={BLUR}[bg];"
        f"[b]{fit}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )


def encode(src, dst, name, bg, log, stop=None):
    """Returns 'ok', 'failed' or 'cancelled'."""
    W, H = SIZES[name]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part.mp4")  # so a half-written file never looks finished
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-filter_complex", build_filter(W, H, bg),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-crf", str(CRF), "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", str(tmp),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            text=True, errors="replace", creationflags=NO_WINDOW)
    while True:
        try:
            _, err = proc.communicate(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            if stop is not None and stop.is_set():
                proc.kill()
                proc.communicate()
                tmp.unlink(missing_ok=True)
                return "cancelled"
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        log(f"failed: {dst.name}\n{err.strip()}")
        return "failed"
    tmp.replace(dst)
    return "ok"


def find_videos(paths, recursive=True):
    found = []
    for p in map(Path, paths):
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            found += sorted(f for f in it if f.is_file() and f.suffix.lower() in EXTS)
        elif p.suffix.lower() in EXTS:
            found.append(p)
    found = dict.fromkeys(found)  # drop duplicates, keep order
    # ignore files this tool made itself
    return [f for f in found if not any(f.stem.endswith(f"_{n}") for n in SIZES)]


def process(files, sizes, black=(), folders=True, overwrite=False,
            log=print, progress=None, stop=None):
    if not have_ffmpeg():
        raise RuntimeError("ffmpeg / ffprobe not found. Install ffmpeg and make sure it is on your PATH.")

    jobs = []
    for f in files:
        try:
            own = orientation(*probe(f))
        except Exception:
            log(f"skipped, can't read: {f.name}")
            continue
        for name in sizes:
            if name == own:
                continue  # already that orientation
            out_dir = f.parent / name if folders else f.parent
            dst = out_dir / f"{f.stem}_{name}.mp4"
            if dst.exists() and not overwrite:
                log(f"exists, skipped: {dst.name}")
                continue
            jobs.append((f, name, dst))

    total, ok = len(jobs), 0
    if progress:
        progress(0, total)
    for i, (src, name, dst) in enumerate(jobs, 1):
        if stop is not None and stop.is_set():
            break
        log(f"[{i}/{total}] {src.name} -> {name}")
        result = encode(src, dst, name, "black" if name in black else "blur", log, stop)
        if result == "cancelled":
            break
        if result == "ok":
            ok += 1
        if progress:
            progress(i, total)

    if stop is not None and stop.is_set():
        log("cancelled.")
    else:
        log(f"finished, {ok} of {total} created.")


# ---------------------------------------------------------------- window

def gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("Video Resizer")
    root.geometry("560x620")
    root.minsize(480, 520)
    pad = {"padx": 10, "pady": 6}

    paths = []
    stop = threading.Event()
    msgs = queue.Queue()

    # --- inputs
    box = ttk.LabelFrame(root, text="Videos")
    box.pack(fill="x", **pad)
    listbox = tk.Listbox(box, height=5)
    listbox.pack(fill="x", padx=8, pady=(8, 4))
    row = ttk.Frame(box)
    row.pack(fill="x", padx=8, pady=(0, 8))

    def add(p):
        if p and p not in paths:
            paths.append(p)
            listbox.insert("end", p)

    def add_folder():
        add(filedialog.askdirectory(title="Pick a folder with videos"))

    def add_files():
        pat = " ".join(f"*{e}" for e in sorted(EXTS))
        for p in filedialog.askopenfilenames(filetypes=[("Videos", pat)]):
            add(p)

    def clear():
        paths.clear()
        listbox.delete(0, "end")

    ttk.Button(row, text="Add folder...", command=add_folder).pack(side="left")
    ttk.Button(row, text="Add files...", command=add_files).pack(side="left", padx=6)
    ttk.Button(row, text="Clear", command=clear).pack(side="left")

    # --- sizes
    sbox = ttk.LabelFrame(root, text="Make these sizes")
    sbox.pack(fill="x", **pad)
    on, bg = {}, {}
    ttk.Label(sbox, text="Empty space filled with:").grid(row=0, column=2, sticky="w", padx=8)
    for r, (name, (w, h)) in enumerate(SIZES.items(), 1):
        on[name] = tk.BooleanVar(value=True)
        bg[name] = tk.StringVar(value="Blur")
        ttk.Checkbutton(sbox, text=f"{name.capitalize()}  ({w}x{h})",
                        variable=on[name]).grid(row=r, column=0, sticky="w", padx=8, pady=3)
        ttk.Combobox(sbox, textvariable=bg[name], values=["Blur", "Black"],
                     state="readonly", width=8).grid(row=r, column=2, sticky="w", padx=8)
    sbox.columnconfigure(1, weight=1)

    # --- options
    obox = ttk.LabelFrame(root, text="Options")
    obox.pack(fill="x", **pad)
    recursive = tk.BooleanVar(value=True)
    folders = tk.BooleanVar(value=True)
    overwrite = tk.BooleanVar(value=False)
    ttk.Checkbutton(obox, text="Include subfolders", variable=recursive).pack(anchor="w", padx=8, pady=2)
    ttk.Checkbutton(obox, text="Put each size in its own folder next to the video",
                    variable=folders).pack(anchor="w", padx=8, pady=2)
    ttk.Checkbutton(obox, text="Overwrite files that already exist",
                    variable=overwrite).pack(anchor="w", padx=8, pady=(2, 6))

    # --- run
    ctl = ttk.Frame(root)
    ctl.pack(fill="x", **pad)
    start_btn = ttk.Button(ctl, text="Start")
    cancel_btn = ttk.Button(ctl, text="Cancel", state="disabled", command=stop.set)
    start_btn.pack(side="left")
    cancel_btn.pack(side="left", padx=6)
    bar = ttk.Progressbar(ctl, mode="determinate")
    bar.pack(side="left", fill="x", expand=True, padx=(10, 0))

    logbox = scrolledtext.ScrolledText(root, height=10, state="disabled", font=("Consolas", 9))
    logbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def write(text):
        logbox.config(state="normal")
        logbox.insert("end", text + "\n")
        logbox.see("end")
        logbox.config(state="disabled")

    def poll():
        try:
            while True:
                m = msgs.get_nowait()
                if m[0] == "log":
                    write(m[1])
                elif m[0] == "progress":
                    bar.config(maximum=max(m[2], 1), value=m[1])
                else:
                    start_btn.config(state="normal")
                    cancel_btn.config(state="disabled")
        except queue.Empty:
            pass
        root.after(100, poll)

    def start():
        if not paths:
            messagebox.showinfo("Video Resizer", "Add a folder or some videos first.")
            return
        sizes = [n for n in SIZES if on[n].get()]
        if not sizes:
            messagebox.showinfo("Video Resizer", "Tick at least one size.")
            return
        black = {n for n in sizes if bg[n].get() == "Black"}
        opts = (recursive.get(), folders.get(), overwrite.get())
        stop.clear()
        start_btn.config(state="disabled")
        cancel_btn.config(state="normal")

        def work():
            try:
                files = find_videos(list(paths), opts[0])
                if not files:
                    msgs.put(("log", "no videos found."))
                else:
                    process(files, sizes, black, opts[1], opts[2],
                            log=lambda t: msgs.put(("log", t)),
                            progress=lambda d, t: msgs.put(("progress", d, t)),
                            stop=stop)
            except Exception as e:
                msgs.put(("log", f"error: {e}"))
            msgs.put(("done",))

        threading.Thread(target=work, daemon=True).start()

    def on_close():
        stop.set()
        root.after(500, root.destroy)  # give ffmpeg a moment to be killed

    def check_ffmpeg():
        if have_ffmpeg():
            return
        if not messagebox.askyesno(
                "ffmpeg not found",
                "This tool needs ffmpeg and it isn't installed.\n\n"
                "Install it now? (needs an internet connection)"):
            write("ffmpeg is missing. Install it and restart the tool.")
            return
        start_btn.config(state="disabled")

        def work():
            try:
                install_ffmpeg(lambda t: msgs.put(("log", t)))
            except Exception as e:
                msgs.put(("log", f"error: {e}"))
            msgs.put(("done",))

        threading.Thread(target=work, daemon=True).start()

    start_btn.config(command=start)
    root.protocol("WM_DELETE_WINDOW", on_close)
    poll()
    root.after(300, check_ffmpeg)
    root.mainloop()


# ---------------------------------------------------------------- command line

def main():
    ap = argparse.ArgumentParser(
        description="Make landscape / portrait / square versions of videos. "
                    "Run with no arguments to open the window.")
    ap.add_argument("paths", nargs="*", help="video files or folders")
    ap.add_argument("-s", "--sizes", nargs="+", choices=list(SIZES), default=list(SIZES),
                    help="which sizes to make (default: all)")
    ap.add_argument("--black", nargs="+", choices=list(SIZES), default=[], metavar="SIZE",
                    help="use black bars instead of a blurred background for these sizes")
    ap.add_argument("--no-recursive", action="store_true", help="don't look inside subfolders")
    ap.add_argument("--flat", action="store_true",
                    help="save next to the original instead of in landscape/portrait/square folders")
    ap.add_argument("--overwrite", action="store_true", help="redo files that already exist")
    args = ap.parse_args()

    if not args.paths:
        gui()
        return
    if not have_ffmpeg() and not offer_install_cli():
        sys.exit("ffmpeg / ffprobe not found. Install ffmpeg and make sure it is on your PATH.")
    files = find_videos(args.paths, not args.no_recursive)
    if not files:
        sys.exit("no videos found.")
    try:
        process(files, args.sizes, set(args.black), not args.flat, args.overwrite)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
