import json
import os
import random
import sys
import subprocess
import threading
import ctypes
import tempfile
import urllib.error
import urllib.request
from ctypes import wintypes
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageFilter, ImageTk
import pystray
from PIL import ImageDraw

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
APP_VERSION = "1.0.1"
GITHUB_REPOSITORY = "Ryzexing/randphot"
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_HOTKEY_CHANGED = 0x8001
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
hotkey_thread_id = None
pending_hotkey = (MOD_CONTROL | MOD_ALT, ord("O"))
preview_window = None
preview_image = None
preview_frames = []
preview_durations = []
preview_frame_index = 0
preview_after_id = None
background_image = None
background_source = None
photo_cache_folder = None
photo_cache = []
last_photo_path = None


def get_settings_path():
    base_path = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    return base_path / "settings.json"


def get_asset_path(filename):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).parent / filename


def update_background(event=None):
    global background_image

    if background_source is None or event is None:
        return
    width = max(root.winfo_width(), 1)
    height = max(root.winfo_height(), 1)
    image = background_source.copy()
    scale = max(width / image.width, height / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    left = max(0, (image.width - width) // 2)
    top = max(0, (image.height - height) // 2)
    image = image.crop((left, top, left + width, top + height))
    background_image = ImageTk.PhotoImage(image)
    background_label.configure(image=background_image)


def load_settings():
    try:
        with get_settings_path().open("r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, ValueError):
        return {"folder": "", "hotkey": "Ctrl+Alt+O"}
    return {
        "folder": settings.get("folder", ""),
        "hotkey": settings.get("hotkey", "Ctrl+Alt+O"),
    }


def save_settings():
    try:
        with get_settings_path().open("w", encoding="utf-8") as settings_file:
            json.dump(
                {"folder": folder_var.get().strip(), "hotkey": hotkey_var.get().strip()},
                settings_file,
                ensure_ascii=False,
                indent=2,
            )
    except OSError:
        pass


def version_tuple(version):
    return tuple(int(part) for part in version.lstrip("v").split(".")[:3])


def check_for_updates():
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
            headers={"User-Agent": "RandomPhoto-Updater"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            release = json.loads(response.read().decode("utf-8"))
        latest_version = release.get("tag_name", "").lstrip("v")
        if version_tuple(latest_version) <= version_tuple(APP_VERSION):
            root.after(0, lambda: status_var.set(f"Установлена последняя версия {APP_VERSION}"))
            return
        assets = release.get("assets", [])
        asset = next((item for item in assets if item.get("name") == "RandomPhoto.exe"), None)
        if asset is None:
            root.after(0, lambda: status_var.set(f"Доступна версия {latest_version}, но EXE не найден."))
            return
        root.after(0, lambda: offer_update(latest_version, asset["browser_download_url"]))
    except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
        error_message = str(error)
        root.after(0, lambda: status_var.set(f"Не удалось проверить обновления: {error_message}"))


def offer_update(latest_version, download_url):
    if messagebox.askyesno(
        "Доступно обновление",
        f"Доступна версия {latest_version}. Скачать и установить ее сейчас?",
    ):
        threading.Thread(
            target=download_update,
            args=(latest_version, download_url),
            daemon=True,
        ).start()


def download_update(latest_version, download_url):
    try:
        update_path = Path(tempfile.gettempdir()) / "RandomPhoto-update.exe"
        urllib.request.urlretrieve(download_url, update_path)
        if getattr(sys, "frozen", False):
            current_path = Path(sys.executable).resolve()
            script_path = Path(tempfile.gettempdir()) / "RandomPhoto-update.cmd"
            script_path.write_text(
                "@echo off\n"
                "timeout /t 2 /nobreak >nul\n"
                f'copy /Y "{update_path}" "{current_path}" >nul\n'
                f'start "" "{current_path}"\n'
                f'del "%~f0"\n',
                encoding="utf-8",
            )
            root.after(0, lambda: start_update(script_path, latest_version))
        else:
            root.after(0, lambda: status_var.set("Обновление доступно только в EXE-сборке."))
    except (OSError, urllib.error.URLError) as error:
        root.after(0, lambda: messagebox.showerror("Ошибка обновления", str(error)))


def start_update(script_path, latest_version):
    status_var.set(f"Устанавливаю обновление {latest_version}...")
    subprocess.Popen(["cmd", "/c", "start", "", str(script_path)], shell=False)
    close_app()


def choose_folder():
    folder = filedialog.askdirectory(title="Выберите папку с фотографиями")
    if folder:
        folder_var.set(folder)
        clear_photo_cache()
        save_settings()
        status_var.set("Папка выбрана. Нажмите кнопку, чтобы открыть фото.")


def find_photos(folder):
    photos = []

    def scan_directory(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            scan_directory(entry.path)
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS
                        ):
                            photos.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            return

    try:
        scan_directory(str(folder))
        return photos
    except OSError as error:
        messagebox.showerror("Ошибка доступа", f"Не удалось прочитать папку:\n{error}")
        return []


def clear_photo_cache():
    global photo_cache_folder, photo_cache
    photo_cache_folder = None
    photo_cache = []


def refresh_photo_cache():
    global photo_cache_folder, photo_cache

    folder = folder_var.get().strip()
    folder_path = Path(folder)
    if not folder_path.is_dir():
        messagebox.showerror("Папка не найдена", "Указанный путь не является существующей папкой.")
        return False

    status_var.set("Обновляю список фотографий...")
    root.update_idletasks()
    photos = find_photos(folder_path)
    photo_cache_folder = folder_path
    photo_cache = photos
    status_var.set(f"Найдено фотографий: {len(photos)}")
    return bool(photos)


def show_photo_location():
    if last_photo_path is None or not last_photo_path.exists():
        messagebox.showinfo("Фото не выбрано", "Сначала откройте фотографию.")
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(last_photo_path)], check=False)


def open_random_photo():
    global photo_cache_folder, photo_cache, last_photo_path

    folder = folder_var.get().strip()
    if not folder:
        messagebox.showwarning("Папка не выбрана", "Сначала выберите папку с фотографиями.")
        return

    folder_path = Path(folder)
    if not folder_path.is_dir():
        messagebox.showerror("Папка не найдена", "Указанный путь не является существующей папкой.")
        return

    if photo_cache_folder != folder_path:
        if not refresh_photo_cache():
            return
    photos = photo_cache
    if not photos:
        messagebox.showinfo(
            "Фотографии не найдены",
            "В выбранной папке нет поддерживаемых изображений.",
        )
        return

    photo = random.choice(photos)
    last_photo_path = photo
    try:
        show_photo(photo)
        photo_path_var.set(str(photo))
        status_var.set(f"Открыто: {photo.name}")
    except (OSError, ValueError) as error:
        messagebox.showerror("Не удалось открыть фото", str(error))


def show_photo(photo):
    global preview_window, preview_image, preview_frames
    global preview_durations, preview_frame_index, preview_after_id

    if preview_window is not None and preview_window.winfo_exists():
        close_preview()

    with Image.open(photo) as image:
        preview_frames = []
        preview_durations = []
        frame_count = getattr(image, "n_frames", 1)
        for frame_number in range(frame_count):
            image.seek(frame_number)
            frame = image.convert("RGBA").copy()
            frame.thumbnail((1100, 800), Image.Resampling.LANCZOS)
            preview_frames.append(frame)
            preview_durations.append(max(20, image.info.get("duration", 100) or 100))

    preview_frame_index = 0
    preview_image = ImageTk.PhotoImage(preview_frames[0])
    preview_window = tk.Toplevel(root)
    preview_window.title(photo.name)
    preview_window.configure(background="black")
    preview_window.protocol("WM_DELETE_WINDOW", close_preview)
    label = tk.Label(preview_window, image=preview_image, background="black")
    label.pack(padx=8, pady=8)
    preview_window.focus_force()
    if len(preview_frames) > 1:
        preview_after_id = root.after(preview_durations[0], animate_preview)


def animate_preview():
    global preview_image, preview_frame_index, preview_after_id

    if preview_window is None or not preview_window.winfo_exists():
        return

    preview_frame_index = (preview_frame_index + 1) % len(preview_frames)
    preview_image = ImageTk.PhotoImage(preview_frames[preview_frame_index])
    preview_window.winfo_children()[0].configure(image=preview_image)
    preview_after_id = root.after(
        preview_durations[preview_frame_index], animate_preview
    )


def close_preview():
    global preview_after_id, preview_frames, preview_durations

    if preview_after_id is not None:
        root.after_cancel(preview_after_id)
        preview_after_id = None
    preview_frames = []
    preview_durations = []
    if preview_window is not None and preview_window.winfo_exists():
        preview_window.destroy()


def parse_hotkey(value):
    parts = [part.strip().upper() for part in value.split("+") if part.strip()]
    if not parts:
        return None

    modifiers = 0
    modifier_map = {
        "CTRL": MOD_CONTROL,
        "CONTROL": MOD_CONTROL,
        "ALT": MOD_ALT,
        "SHIFT": MOD_SHIFT,
        "WIN": MOD_WIN,
        "WINDOWS": MOD_WIN,
    }
    for part in parts[:-1]:
        if part not in modifier_map:
            return None
        modifiers |= modifier_map[part]

    key = parts[-1]
    if len(key) == 1:
        virtual_key = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        virtual_key = 0x70 + int(key[1:]) - 1
    else:
        virtual_key = {
            "SPACE": 0x20,
            "TAB": 0x09,
            "ENTER": 0x0D,
            "ESC": 0x1B,
            "ESCAPE": 0x1B,
            "BACKSPACE": 0x08,
            "DELETE": 0x2E,
            "INSERT": 0x2D,
            "HOME": 0x24,
            "END": 0x23,
            "PAGEUP": 0x21,
            "PAGEDOWN": 0x22,
            "UP": 0x26,
            "DOWN": 0x28,
            "LEFT": 0x25,
            "RIGHT": 0x27,
        }.get(key)
    return (modifiers, virtual_key) if virtual_key is not None else None


def capture_hotkey(event):
    modifier_keys = {"Control_L", "Control_R", "Alt_L", "Alt_R", "Shift_L", "Shift_R", "Win_L", "Win_R"}
    if event.keysym in modifier_keys:
        status_var.set("Нажмите основную клавишу вместе с модификаторами.")
        return "break"

    modifiers = []
    if event.state & 0x0004:
        modifiers.append("Ctrl")
    if event.state & 0x0001:
        modifiers.append("Shift")
    if event.state & 0x0008:
        modifiers.append("Alt")

    key_names = {
        "space": "Space",
        "Return": "Enter",
        "Escape": "Esc",
        "Tab": "Tab",
        "BackSpace": "Backspace",
        "Delete": "Delete",
        "Insert": "Insert",
        "Home": "Home",
        "End": "End",
        "Prior": "PageUp",
        "Next": "PageDown",
        "Up": "Up",
        "Down": "Down",
        "Left": "Left",
        "Right": "Right",
    }
    key_name = key_names.get(event.keysym, event.keysym)
    if key_name.startswith("KP_"):
        key_name = key_name[3:]
    if len(key_name) == 1:
        key_name = key_name.upper()
    hotkey_var.set("+".join(modifiers + [key_name]))
    apply_hotkey()
    return "break"


def apply_hotkey():
    global pending_hotkey

    parsed = parse_hotkey(hotkey_var.get())
    if parsed is None:
        messagebox.showwarning(
            "Неверный бинд",
            "Нажмите клавишу или сочетание, например F6, A, Ctrl+Shift+P.",
        )
        return

    pending_hotkey = parsed
    save_settings()
    if sys.platform == "win32" and hotkey_thread_id is not None:
        ctypes.windll.user32.PostThreadMessageW(
            hotkey_thread_id, WM_HOTKEY_CHANGED, 0, 0
        )
    status_var.set(f"Новый бинд: {hotkey_var.get().strip().upper()}")


def register_global_hotkey():
    global hotkey_thread_id

    if sys.platform != "win32":
        return

    hotkey_thread_id = threading.get_native_id()
    user32 = ctypes.windll.user32
    while True:
        modifiers, virtual_key = pending_hotkey
        registered = user32.RegisterHotKey(
            None, HOTKEY_ID, modifiers, virtual_key
        )
        if registered:
            root.after(0, lambda: status_var.set("Горячая клавиша активна."))
        else:
            root.after(0, lambda: status_var.set("Этот бинд занят другой программой."))

        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                root.after(0, open_random_photo)
            elif message.message == WM_HOTKEY_CHANGED:
                break
        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        if message.message == WM_QUIT:
            return


def close_app():
    save_settings()
    tray_icon.stop()
    if sys.platform == "win32" and hotkey_thread_id is not None:
        ctypes.windll.user32.PostThreadMessageW(
            hotkey_thread_id, WM_QUIT, 0, 0
        )
    close_preview()
    root.destroy()


def show_app(icon=None, item=None):
    root.after(0, root.deiconify)
    root.after(0, root.lift)


def hide_app(icon=None, item=None):
    save_settings()
    root.after(0, root.withdraw)


def create_tray_icon():
    image = Image.new("RGB", (64, 64), "#1f6feb")
    draw = ImageDraw.Draw(image)
    draw.ellipse((12, 12, 52, 52), fill="#ffffff")
    draw.ellipse((24, 24, 40, 40), fill="#1f6feb")
    menu = pystray.Menu(
        pystray.MenuItem("Показать окно", show_app, default=True),
        pystray.MenuItem("Скрыть в трей", hide_app),
        pystray.MenuItem("Выход", lambda icon, item: root.after(0, close_app)),
    )
    return pystray.Icon("RandomPhoto", image, "Порно", menu)


initial_settings = load_settings()
initial_hotkey = initial_settings["hotkey"]
parsed_initial_hotkey = parse_hotkey(initial_hotkey)
if parsed_initial_hotkey is not None:
    pending_hotkey = parsed_initial_hotkey

root = tk.Tk()
root.title("Порно")
root.geometry("820x520")
root.minsize(720, 460)
root.columnconfigure(0, weight=1)
root.columnconfigure(1, weight=0)

try:
    background_source = Image.open(
        get_asset_path("20241220131224_1.jpg")
    ).convert("RGB").filter(ImageFilter.GaussianBlur(4))
except (OSError, ValueError):
    background_source = None

background_label = tk.Label(root, background="#d9a98f")
background_label.place(relx=0, rely=0, relwidth=1, relheight=1)
root.bind("<Configure>", update_background)
root.after(0, update_background)

folder_var = tk.StringVar(value=initial_settings["folder"])
hotkey_var = tk.StringVar(value=initial_hotkey)
status_var = tk.StringVar(value="Выберите папку с фотографиями.")
photo_path_var = tk.StringVar(value="Фото еще не открывалось")

style = ttk.Style()
style.configure("Overlay.TLabel", background="#634749", foreground="#fff8f2")
style.configure(
    "Title.TLabel",
    font=("Segoe UI", 24, "bold"),
    background="#634749",
    foreground="#fff8f2",
)
root.grid_rowconfigure(0, minsize=58)
root.grid_rowconfigure(1, minsize=30)
root.grid_rowconfigure(2, minsize=48)
root.grid_rowconfigure(3, minsize=52)
root.grid_rowconfigure(4, minsize=66)
root.grid_rowconfigure(5, minsize=30)
root.grid_rowconfigure(6, minsize=34)
root.grid_rowconfigure(7, minsize=40)
root.grid_rowconfigure(8, minsize=52)
root.grid_rowconfigure(9, minsize=40)
root.grid_rowconfigure(10, minsize=42)

ttk.Label(root, text="Порно", style="Title.TLabel").grid(
    row=0, column=0, columnspan=2, sticky="w", padx=(48, 48), pady=(30, 0)
)
ttk.Label(root, text="Создатель: Hes", style="Overlay.TLabel", foreground="#f2d7c8").grid(
    row=1, column=0, columnspan=2, sticky="w", padx=(50, 48)
)
ttk.Label(
    root,
    text="Выберите папку, и приложение откроет одну случайную фотографию.",
    style="Overlay.TLabel",
).grid(row=2, column=0, columnspan=2, sticky="w", padx=(50, 48))

ttk.Entry(root, textvariable=folder_var).grid(
    row=3, column=0, sticky="ew", padx=(50, 12)
)
ttk.Button(root, text="Выбрать папку...", command=choose_folder).grid(
    row=3, column=1, sticky="ew", padx=(0, 48)
)
ttk.Button(root, text="Открыть случайное фото", command=open_random_photo).grid(
    row=4, column=0, columnspan=2, sticky="ew", padx=50, pady=(12, 12), ipady=9
)
ttk.Button(root, text="Обновить список фотографий", command=refresh_photo_cache).grid(
    row=5, column=0, columnspan=2, sticky="w", padx=50, pady=(0, 4)
)
ttk.Label(
    root,
    textvariable=photo_path_var,
    style="Overlay.TLabel",
    foreground="#f2d7c8",
).grid(row=6, column=0, sticky="w", padx=(50, 12))
ttk.Button(root, text="Показать в папке", command=show_photo_location).grid(
    row=6, column=1, sticky="ew", padx=(0, 48)
)
ttk.Label(
    root,
    text="Нажмите поле и нажмите одну клавишу или сочетание:",
    style="Overlay.TLabel",
    foreground="#f2d7c8",
).grid(row=7, column=0, columnspan=2, sticky="w", padx=(50, 48))
hotkey_entry = ttk.Entry(root, textvariable=hotkey_var, state="readonly")
hotkey_entry.grid(
    row=8, column=0, sticky="ew", padx=(50, 12), pady=(2, 0)
)
ttk.Button(root, text="Применить бинд", command=apply_hotkey).grid(
    row=8, column=1, sticky="ew", padx=(0, 48), pady=(2, 0)
)
ttk.Label(root, textvariable=status_var, style="Overlay.TLabel", foreground="#f2d7c8").grid(
    row=9, column=0, columnspan=2, sticky="w", padx=(50, 48), pady=(0, 20)
)
ttk.Button(
    root,
    text=f"Проверить обновления (v{APP_VERSION})",
    command=lambda: threading.Thread(target=check_for_updates, daemon=True).start(),
).grid(row=10, column=0, columnspan=2, sticky="w", padx=50, pady=(0, 24))

hotkey_thread = threading.Thread(target=register_global_hotkey, daemon=True)
hotkey_entry.bind("<KeyPress>", capture_hotkey)
hotkey_thread_id = None
tray_icon = create_tray_icon()
tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
if sys.platform == "win32":
    hotkey_thread.start()
tray_thread.start()

root.protocol("WM_DELETE_WINDOW", hide_app)
root.mainloop()
