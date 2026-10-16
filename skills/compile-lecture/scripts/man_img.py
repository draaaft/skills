#!/usr/bin/env python3
"""Interactive manual image processor for course slides (compile-lecture skill).

Opens a tkinter GUI that lets the user crop the image, adjust tonal levels
(low / ratio / high, modeled after Paint.NET's Levels dialog), browse other
images in the same directory, review the suggested rename, read the purpose
description supplied by the caller (LLM), and finally approve or reject the
image with an optional free-text note.

Usage:
    python man_img.py <input.image> -o OUTPUT_DIR [--rename NAME] [--purpose TEXT] [--quality 95]

On approval the processed image is saved into OUTPUT_DIR and a machine-readable
log is printed to stdout, e.g.:

    Opened GUI window for the user.
    User has approved this image (additional response: none).
    User didn't rechoose the source image "D:\\foobar\\p001.png".
    User cropped the image to (12, 30, 640, 480).
    Saved to "D:\\out\\L03-foo.jpg"!

Self-contained bootstrap: Pillow is installed into a local ``.deps`` directory
next to this script if missing, so the user's global Python environment is
never touched. Re-running the script reuses the cached dependency.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEPS_DIR = Path(__file__).resolve().parent / ".deps"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
CANVAS_W, CANVAS_H = 900, 600
HANDLE_HALF = 4      # half size of a crop handle square, in display px
HIT_TOLERANCE = 7    # handle hit-test tolerance, in display px
MIN_CROP = 16        # minimum crop size, in original px


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _ensure_pillow() -> None:
    """Install Pillow into .deps if it is not importable."""
    try:
        import PIL  # noqa: F401
        return
    except ImportError:
        pass

    DEPS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            sys.executable, "-m", "pip", "install",
            "--target", str(DEPS_DIR),
            "--quiet",
            "--disable-pip-version-check",
            "pillow",
        ],
    )
    if str(DEPS_DIR) not in sys.path:
        sys.path.insert(0, str(DEPS_DIR))


def _natural_key(path: Path):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)]


class Levels:
    """Tonal levels: black point, white point and midtone gamma."""

    def __init__(self) -> None:
        self.low = 0
        self.high = 255
        self.ratio = 1.0

    def is_identity(self) -> bool:
        return self.low == 0 and self.high == 255 and abs(self.ratio - 1.0) < 1e-6

    def lut(self) -> list[int]:
        low, high = self.low, max(self.high, self.low + 1)
        ratio = min(max(self.ratio, 0.1), 10.0)
        table = []
        for value in range(256):
            v = (value - low) / (high - low)
            v = 0.0 if v < 0 else (1.0 if v > 1 else v)
            v = v ** (1.0 / ratio)
            table.append(round(v * 255))
        return table


class App:
    def __init__(self, root, initial_path: Path, out_dir: Path,
                 rename: str | None, purpose: str | None, quality: int) -> None:
        import tkinter as tk

        from PIL import Image, ImageOps, ImageTk

        self.tk = tk
        self.Image = Image
        self.ImageOps = ImageOps
        self.ImageTk = ImageTk

        self.root = root
        self.initial_path = initial_path
        self.current_path = initial_path
        self.out_dir = out_dir
        self.rename = rename
        self.purpose = purpose
        self.quality = quality

        self.levels = Levels()
        self.crop = (0, 0, 1, 1)          # x0, y0, x1, y1 in original px
        self.scale = 1.0
        self.ox = self.oy = 0.0           # image origin on canvas
        self.mode: str | None = None      # resize handle / 'move' / 'new'
        self.drag_anchor = (0.0, 0.0)

        self.approved: bool | None = None
        self.additional = ""
        self.saved_path: Path | None = None
        self.crop_result: tuple[int, int, int, int] | None = None

        self.siblings = sorted(
            (p for p in initial_path.parent.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
            key=_natural_key,
        )
        self.idx = self.siblings.index(initial_path)

        self._build_ui()
        self._load_image()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<Key>", self._on_key)

    # ------------------------------------------------------------- UI build

    def _build_ui(self) -> None:
        tk = self.tk
        root = self.root
        root.title("man_img")
        root.resizable(False, False)

        top = tk.Frame(root, padx=10, pady=6)
        top.pack(fill="x")
        tk.Label(top, text="Purpose:").pack(side="left", anchor="n")
        tk.Label(top, text=self.purpose or "none", anchor="w", justify="left",
                 wraplength=810).pack(side="left", fill="x", padx=(6, 0))

        nav = tk.Frame(root)
        nav.pack(fill="x", pady=2)
        tk.Button(nav, text="< Prev", width=8,
                  command=lambda: self._switch(-1)).pack(side="left", padx=8)
        self.file_label = tk.Label(nav, text="", font=("Consolas", 10))
        self.file_label.pack(side="left", expand=True)
        tk.Button(nav, text="Next >", width=8,
                  command=lambda: self._switch(1)).pack(side="right", padx=8)

        self.canvas = tk.Canvas(root, width=CANVAS_W, height=CANVAS_H,
                                bg="#202020", cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        panel = tk.Frame(root, padx=10, pady=6)
        panel.pack(fill="x")

        levels = tk.Frame(panel)
        levels.pack(side="left", anchor="n")
        self.low_var = tk.IntVar(value=0)
        self.high_var = tk.IntVar(value=255)
        self.mid_var = tk.IntVar(value=0)   # -100..100  ->  ratio = 10**(v/100)
        self.low_scale = tk.Scale(levels, from_=0, to=255, orient="horizontal",
                                  length=300, label="Low",
                                  variable=self.low_var, command=self._on_levels)
        self.mid_scale = tk.Scale(levels, from_=-100, to=100, orient="horizontal",
                                  length=300, label="Ratio 1.00",
                                  variable=self.mid_var, command=self._on_levels)
        self.high_scale = tk.Scale(levels, from_=0, to=255, orient="horizontal",
                                   length=300, label="High",
                                   variable=self.high_var, command=self._on_levels)
        for scale in (self.low_scale, self.mid_scale, self.high_scale):
            scale.pack(anchor="w")
        tk.Button(levels, text="Reset", command=self._reset_levels).pack(anchor="w", pady=2)

        right = tk.Frame(panel)
        right.pack(side="right", anchor="n")
        fields = tk.Frame(right)
        fields.pack(anchor="w")
        tk.Label(fields, text="Save as").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.name_var = tk.StringVar()
        tk.Entry(fields, textvariable=self.name_var, width=44).grid(row=0, column=1, sticky="w")
        tk.Label(fields, text="Note").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        self.note_var = tk.StringVar()
        tk.Entry(fields, textvariable=self.note_var, width=44).grid(row=1, column=1, sticky="w", pady=(8, 0))

        buttons = tk.Frame(right)
        buttons.pack(anchor="w", pady=10)
        tk.Button(buttons, text="Approve", width=16, bg="#2e7d32", fg="white",
                  command=self._approve).pack(side="left", padx=(0, 10))
        tk.Button(buttons, text="Reject", width=16, bg="#b3261e", fg="white",
                  command=self._reject).pack(side="left")

        self.crop_label = tk.Label(root, text="", anchor="w", padx=10, fg="#444")
        self.crop_label.pack(fill="x", pady=(0, 6))

    # ---------------------------------------------------------- image loading

    def _open_rgb(self, path: Path):
        Image = self.Image
        img = self.ImageOps.exif_transpose(Image.open(path))
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            return background
        return img.convert("RGB")

    def _load_image(self) -> None:
        self.img = self._open_rgb(self.current_path)
        width, height = self.img.size
        self.scale = min(CANVAS_W / width, CANVAS_H / height, 1.0)
        preview_size = (max(1, int(width * self.scale)), max(1, int(height * self.scale)))
        self.preview = self.img.resize(preview_size, self.Image.LANCZOS)
        self.crop = (0, 0, width, height)
        self._apply_level_vars(0, 255, 0)
        self._reset_name_entry()
        self.file_label.config(text=f"[{self.idx + 1}/{len(self.siblings)}] {self.current_path.name}")
        self._redraw()

    def _reset_name_entry(self) -> None:
        if self.rename and self.current_path == self.initial_path:
            self.name_var.set(self.rename)
        else:
            self.name_var.set(self.current_path.name)

    # -------------------------------------------------------------- redraw

    def _redraw(self) -> None:
        lut = self.levels.lut()
        display = self.preview.point(lut * 3)
        self.tkimg = self.ImageTk.PhotoImage(display)
        canvas = self.canvas
        canvas.delete("all")

        pw, ph = display.size
        self.ox = (CANVAS_W - pw) // 2
        self.oy = (CANVAS_H - ph) // 2
        canvas.create_image(self.ox, self.oy, anchor="nw", image=self.tkimg)

        x0, y0, x1, y1 = self.crop
        dx0 = self.ox + x0 * self.scale
        dy0 = self.oy + y0 * self.scale
        dx1 = self.ox + x1 * self.scale
        dy1 = self.oy + y1 * self.scale

        for ax0, ay0, ax1, ay1 in (
            (self.ox, self.oy, self.ox + pw, dy0),
            (self.ox, dy1, self.ox + pw, self.oy + ph),
            (self.ox, dy0, dx0, dy1),
            (dx1, dy0, self.ox + pw, dy1),
        ):
            if ax1 > ax0 and ay1 > ay0:
                canvas.create_rectangle(ax0, ay0, ax1, ay1,
                                        fill="#000000", stipple="gray25", outline="")
        canvas.create_rectangle(dx0, dy0, dx1, dy1, outline="#ff4d4f", width=2)

        handle_x = (dx0, (dx0 + dx1) / 2, dx1)
        handle_y = (dy0, (dy0 + dy1) / 2, dy1)
        for hx in handle_x:
            for hy in handle_y:
                if hx == handle_x[1] and hy == handle_y[1]:
                    continue
                canvas.create_rectangle(hx - HANDLE_HALF, hy - HANDLE_HALF,
                                        hx + HANDLE_HALF, hy + HANDLE_HALF,
                                        fill="#ffffff", outline="#ff4d4f")

        cx0, cy0, cx1, cy1 = (round(v) for v in self.crop)
        self.crop_label.config(text=f"Crop: {cx1 - cx0} × {cy1 - cy0} px")

    # -------------------------------------------------------------- levels

    def _apply_level_vars(self, low: int, high: int, mid: int) -> None:
        self.low_var.set(low)
        self.high_var.set(high)
        self.mid_var.set(mid)
        self._on_levels(None)

    def _on_levels(self, _value) -> None:
        self.levels.low = int(self.low_var.get())
        self.levels.high = int(self.high_var.get())
        self.levels.ratio = 10 ** (int(self.mid_var.get()) / 100.0)
        self.mid_scale.config(label=f"Ratio {self.levels.ratio:.2f}")
        if hasattr(self, "img"):
            self._redraw()

    def _reset_levels(self) -> None:
        self._apply_level_vars(0, 255, 0)

    # ------------------------------------------------------------ navigation

    def _switch(self, delta: int) -> None:
        self.idx = (self.idx + delta) % len(self.siblings)
        self.current_path = self.siblings[self.idx]
        self._load_image()

    def _on_key(self, event) -> None:
        if isinstance(self.root.focus_get(), self.tk.Entry):
            return
        if event.char == "<" or event.keysym in ("less", "Left"):
            self._switch(-1)
        elif event.char == ">" or event.keysym in ("greater", "Right"):
            self._switch(1)

    # --------------------------------------------------------------- crop

    def _canvas_to_image(self, cx: float, cy: float) -> tuple[float, float]:
        return ((cx - self.ox) / self.scale, (cy - self.oy) / self.scale)

    def _hit_test(self, cx: float, cy: float) -> str | None:
        x0, y0, x1, y1 = self.crop
        points = {
            "nw": (x0, y0), "n": ((x0 + x1) / 2, y0), "ne": (x1, y0),
            "e": (x1, (y0 + y1) / 2), "se": (x1, y1), "s": ((x0 + x1) / 2, y1),
            "sw": (x0, y1), "w": (x0, (y0 + y1) / 2),
        }
        for key, (ix, iy) in points.items():
            dx = self.ox + ix * self.scale
            dy = self.oy + iy * self.scale
            if abs(cx - dx) <= HIT_TOLERANCE and abs(cy - dy) <= HIT_TOLERANCE:
                return key

        pw, ph = self.preview.size
        if not (self.ox <= cx <= self.ox + pw and self.oy <= cy <= self.oy + ph):
            return None
        dx0 = self.ox + x0 * self.scale
        dx1 = self.ox + x1 * self.scale
        dy0 = self.oy + y0 * self.scale
        dy1 = self.oy + y1 * self.scale
        return "move" if (dx0 <= cx <= dx1 and dy0 <= cy <= dy1) else "new"

    def _on_press(self, event) -> None:
        mode = self._hit_test(event.x, event.y)
        if mode is None:
            return
        self.mode = mode
        self.drag_anchor = self._canvas_to_image(event.x, event.y)
        if mode == "new":
            ix, iy = self.drag_anchor
            self.crop = (ix, iy, ix, iy)

    def _on_drag(self, event) -> None:
        if self.mode is None:
            return
        ix, iy = self._canvas_to_image(event.x, event.y)
        iw, ih = self.img.size
        ix = min(max(ix, 0.0), iw)
        iy = min(max(iy, 0.0), ih)
        x0, y0, x1, y1 = self.crop

        if self.mode == "move":
            ax, ay = self.drag_anchor
            nx0 = min(max(x0 + (ix - ax), 0.0), iw - (x1 - x0))
            ny0 = min(max(y0 + (iy - ay), 0.0), ih - (y1 - y0))
            self.crop = (nx0, ny0, nx0 + (x1 - x0), ny0 + (y1 - y0))
            self.drag_anchor = (ix, iy)
        elif self.mode == "new":
            ax, ay = self.drag_anchor
            self.crop = (min(ax, ix), min(ay, iy), max(ax, ix), max(ay, iy))
        else:
            nx0, ny0, nx1, ny1 = x0, y0, x1, y1
            if "n" in self.mode:
                ny0 = iy
            if "s" in self.mode:
                ny1 = iy
            if "w" in self.mode:
                nx0 = ix
            if "e" in self.mode:
                nx1 = ix
            left, right = sorted((nx0, nx1))
            top, bottom = sorted((ny0, ny1))
            if right - left < MIN_CROP:
                if left + MIN_CROP <= iw:
                    right = left + MIN_CROP
                else:
                    right = iw
                    left = max(iw - MIN_CROP, 0)
            if bottom - top < MIN_CROP:
                if top + MIN_CROP <= ih:
                    bottom = top + MIN_CROP
                else:
                    bottom = ih
                    top = max(ih - MIN_CROP, 0)
            self.crop = (left, top, right, bottom)
        self._redraw()

    def _on_release(self, _event) -> None:
        self.mode = None
        x0, y0, x1, y1 = self.crop
        if x1 - x0 < MIN_CROP or y1 - y0 < MIN_CROP:
            self.crop = (0, 0, *self.img.size)
            self._redraw()

    # ------------------------------------------------------------- actions

    def _resolve_out_path(self, name: str) -> Path:
        candidate = Path(name.strip())
        if candidate.suffix.lower() not in IMAGE_EXTS:
            candidate = Path(candidate.name + self.current_path.suffix)
        safe = re.sub(r'[<>:"/\\|?*]', "_", candidate.name).strip(" .")
        return self.out_dir / safe

    def _approve(self) -> None:
        img = self.img
        iw, ih = img.size
        x0, y0, x1, y1 = (round(v) for v in self.crop)
        x0 = max(0, min(x0, iw - 1))
        y0 = max(0, min(y0, ih - 1))
        x1 = max(x0 + 1, min(x1, iw))
        y1 = max(y0 + 1, min(y1, ih))
        cropped = (x0, y0, x1, y1) != (0, 0, iw, ih)

        if cropped:
            img = img.crop((x0, y0, x1, y1))
        if not self.levels.is_identity():
            img = img.point(self.levels.lut() * 3)

        name = self.name_var.get().strip() or self.current_path.name
        self.out_dir.mkdir(parents=True, exist_ok=True)
        out_path = self._resolve_out_path(name)
        if out_path.suffix.lower() in (".jpg", ".jpeg", ".webp"):
            img.save(out_path, quality=self.quality)
        else:
            img.save(out_path)

        self.approved = True
        self.additional = self.note_var.get().strip()
        self.saved_path = out_path
        self.crop_result = (x0, y0, x1 - x0, y1 - y0) if cropped else None
        self._finish()

    def _reject(self) -> None:
        self.approved = False
        self.additional = self.note_var.get().strip()
        self._finish()

    def _on_close(self) -> None:
        if self.approved is None:
            self.approved = False
        self._finish()

    def _finish(self) -> None:
        note = "none" if not self.additional else f'"{self.additional}"'
        if self.approved:
            print(f"User has approved this image (additional response: {note}).", flush=True)
            if self.current_path != self.initial_path:
                print(f'User changed the source image from "{self.initial_path}" '
                      f'to "{self.current_path}".', flush=True)
            else:
                print(f'User didn\'t rechoose the source image "{self.initial_path}".', flush=True)
            if self.crop_result is not None:
                x, y, w, h = self.crop_result
                print(f"User cropped the image to ({x}, {y}, {w}, {h}).", flush=True)
            else:
                print("User didn't crop the image.", flush=True)
            print(f'Saved to "{self.saved_path}"!', flush=True)
        else:
            print(f"User has rejected this image (additional response: {note}).", flush=True)
        self.root.destroy()


def main() -> int:
    _ensure_utf8_stdio()

    parser = argparse.ArgumentParser(
        description="Interactively crop / adjust levels / rename a course image, "
                    "then approve or reject it.",
    )
    parser.add_argument("input", type=Path, help="path to the input image file")
    parser.add_argument("-o", "--out", type=Path, required=True,
                        help="output directory for approved images")
    parser.add_argument("--rename", default=None,
                        help="suggested file name (shown in the GUI, user may edit)")
    parser.add_argument("--purpose", default=None,
                        help="purpose description shown to the user in the GUI")
    parser.add_argument("--quality", type=int, default=95,
                        help="JPG quality for .jpg/.jpeg/.webp outputs (default: 95)")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"input image not found: {args.input}")

    _ensure_pillow()

    try:
        import tkinter as tk
    except ImportError:
        print("ERROR: tkinter is not available in this Python environment.", flush=True)
        return 1

    root = tk.Tk()
    App(root, args.input.resolve(), args.out.resolve(),
        args.rename, args.purpose, args.quality)
    root.update_idletasks()
    win_w, win_h = root.winfo_reqwidth(), root.winfo_reqheight()
    x = max((root.winfo_screenwidth() - win_w) // 2, 0)
    y = max((root.winfo_screenheight() - win_h) // 2 - 40, 0)
    root.geometry(f"+{x}+{y}")

    print("Opened GUI window for the user.", flush=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
