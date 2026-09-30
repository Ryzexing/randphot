import json
import os
import random
import sys
import subprocess
import shutil
import threading
import ctypes
import tempfile
import urllib.error
import urllib.request
from ctypes import wintypes
from queue import Empty, Queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageFilter, ImageTk
import pystray
from PIL import ImageDraw

VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mpeg", ".mpg"}
IMAGE_EXTENSIONS = set(Image.registered_extensions()) - VIDEO_EXTENSIONS
APP_VERSION = "1.0.10"
GITHUB_REPOSITORY = "Ryzexing/randphot"
AUTOSTART_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "RandomPhoto"
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
video_process = None
background_image = None
background_source = None
background_rendered_size = None
background_pending_size = None
background_resize_after_id = None
photo_cache_folder = None
photo_cache = []
last_photo_path = None
seen_media_paths = set()
seen_media_folder = ""
update_available_version = None
update_download_url = None
update_button = None
settings_window = None
media_scan_results = Queue()
media_scan_generation = 0
media_scan_in_progress = False
media_scan_folder = None
media_scan_include_videos = False
media_scan_open_when_done = False
media_scan_poll_after_id = None


def get_settings_path():
    if getattr(sys, "frozen", False):
        app_data = os.environ.get("APPDATA")
        if not app_data:
            app_data = Path.home() / "AppData" / "Roaming"
        return Path(app_data) / "RandomPhoto" / "settings.json"
    return Path(__file__).parent / "settings.json"


def get_legacy_settings_path():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return None


def get_asset_path(filename):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).parent / filename


def update_background(event=None):
    global background_pending_size, background_resize_after_id

    if background_source is None:
        return
    width = max(event.width if event is not None else root.winfo_width(), 1)
    height = max(event.height if event is not None else root.winfo_height(), 1)
    requested_size = (width, height)
    if requested_size in (background_rendered_size, background_pending_size):
        return
    if background_resize_after_id is not None:
        root.after_cancel(background_resize_after_id)
    background_pending_size = requested_size
    background_resize_after_id = root.after(60, render_background)


def render_background():
    global background_image, background_rendered_size
    global background_pending_size, background_resize_after_id

    background_resize_after_id = None
    background_pending_size = None
    if background_source is None:
        return

    width = max(root.winfo_width(), 1)
    height = max(root.winfo_height(), 1)
    rendered_size = (width, height)
    if rendered_size == background_rendered_size:
        return

    image = background_source.copy()
    scale = max(width / image.width, height / image.height)
    size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    left = max(0, (image.width - width) // 2)
    top = max(0, (image.height - height) // 2)
    image = image.crop((left, top, left + width, top + height))
    background_image = ImageTk.PhotoImage(image)
    background_label.configure(image=background_image)
    background_rendered_size = rendered_size


def load_settings():
    settings_path = get_settings_path()
    legacy_path = get_legacy_settings_path()
    source_path = settings_path
    if not settings_path.is_file() and legacy_path is not None and legacy_path.is_file():
        source_path = legacy_path

    try:
        with source_path.open("r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except (OSError, ValueError):
        return {
            "folder": "",
            "hotkey": "Ctrl+Alt+O",
            "play_videos": False,
            "autostart": True,
            "no_repeat": False,
            "seen_media": [],
            "seen_media_folder": "",
        }

    if source_path != settings_path:
        try:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            with settings_path.open("w", encoding="utf-8") as settings_file:
                json.dump(settings, settings_file, ensure_ascii=False, indent=2)
            legacy_path.unlink()
        except OSError:
            pass
    elif legacy_path is not None and legacy_path.is_file():
        try:
            legacy_path.unlink()
        except OSError:
            pass

    return {
        "folder": settings.get("folder", ""),
        "hotkey": settings.get("hotkey", "Ctrl+Alt+O"),
        "play_videos": settings.get("play_videos", False),
        "autostart": settings.get("autostart", True),
        "no_repeat": settings.get("no_repeat", False),
        "seen_media": settings.get("seen_media", []),
        "seen_media_folder": settings.get("seen_media_folder", ""),
    }


def save_settings():
    settings_path = get_settings_path()
    temporary_path = settings_path.with_suffix(".tmp")
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8") as settings_file:
            json.dump(
                {
                    "folder": folder_var.get().strip(),
                    "hotkey": hotkey_var.get().strip(),
                    "play_videos": play_videos_var.get(),
                    "autostart": autostart_var.get(),
                    "no_repeat": no_repeat_var.get(),
                    "seen_media": sorted(seen_media_paths),
                    "seen_media_folder": seen_media_folder,
                },
                settings_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_path.replace(settings_path)
    except OSError:
        pass


def configure_autostart(enabled):
    if sys.platform != "win32":
        return False, "Автозапуск доступен только в Windows."

    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            AUTOSTART_REGISTRY_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as registry_key:
            if enabled:
                command = [str(sys.executable)]
                if not getattr(sys, "frozen", False):
                    command.append(str(Path(__file__).resolve()))
                winreg.SetValueEx(
                    registry_key,
                    AUTOSTART_VALUE_NAME,
                    0,
                    winreg.REG_SZ,
                    subprocess.list2cmdline(command),
                )
            else:
                try:
                    winreg.DeleteValue(registry_key, AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as error:
        return False, str(error)
    return True, ""


def toggle_autostart():
    enabled = autostart_var.get()
    success, error = configure_autostart(enabled)
    if not success:
        autostart_var.set(not enabled)
        status_var.set(f"Не удалось изменить автозапуск: {error}")
        return

    save_settings()
    status_var.set(
        "Программа будет запускаться вместе с Windows."
        if enabled
        else "Автозапуск программы выключен."
    )


def show_settings():
    global settings_window

    if settings_window is not None and settings_window.winfo_exists():
        settings_window.deiconify()
        settings_window.lift()
        return

    settings_window = tk.Toplevel(root)
    settings_window.title("Настройки")
    settings_window.geometry("390x205")
    settings_window.resizable(False, False)
    settings_window.transient(root)
    ttk.Label(settings_window, text="Настройки приложения").pack(
        anchor="w", padx=20, pady=(16, 8)
    )
    ttk.Checkbutton(
        settings_window,
        text="Не повторять, пока все файлы не открыты",
        variable=no_repeat_var,
        command=toggle_no_repeat,
    ).pack(anchor="w", padx=20, pady=5)
    ttk.Checkbutton(
        settings_window,
        text="Запускать вместе с Windows",
        variable=autostart_var,
        command=toggle_autostart,
    ).pack(anchor="w", padx=20, pady=5)
    ttk.Button(settings_window, text="Закрыть", command=settings_window.destroy).pack(
        anchor="e", padx=20, pady=(12, 14)
    )


def version_tuple(version):
    normalized = version.strip().lstrip("vV").replace(",", ".")
    return tuple(int(part) for part in normalized.split(".")[:3])


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
            root.after(0, show_current_version)
            return
        assets = release.get("assets", [])
        asset = next((item for item in assets if item.get("name") == "RandomPhoto.exe"), None)
        if asset is None:
            root.after(0, lambda: status_var.set(f"Доступна версия {latest_version}, но EXE не найден."))
            return
        root.after(
            0,
            lambda version=latest_version, url=asset["browser_download_url"]: mark_update_available(
                version, url
            ),
        )
    except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
        error_message = str(error)
        root.after(0, lambda: show_update_error(error_message))


def mark_update_available(latest_version, download_url):
    global update_available_version, update_download_url

    update_available_version = latest_version
    update_download_url = download_url
    update_button.configure(
        text=f"Найдено обновление: v{latest_version}",
        state="normal",
        background="#25834b",
        activebackground="#1d6b3c",
        foreground="#ffffff",
    )
    status_var.set(f"Доступно обновление {latest_version}. Нажмите зелёную кнопку, чтобы установить.")


def show_current_version():
    update_button.configure(
        text=f"Проверить обновления (v{APP_VERSION})",
        state="normal",
        background="#76585e",
        activebackground="#63484e",
        foreground="#fff8f2",
    )
    status_var.set(f"Установлена последняя версия {APP_VERSION}")


def show_update_error(error_message):
    update_button.configure(state="normal")
    status_var.set(f"Не удалось проверить обновления: {error_message}")


def on_update_button_click():
    if update_available_version is not None and update_download_url is not None:
        offer_update(update_available_version, update_download_url)
        return
    update_button.configure(text="Проверяю обновления...", state="disabled")
    threading.Thread(target=check_for_updates, daemon=True).start()


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
        update_id = os.getpid()
        update_path = Path(tempfile.gettempdir()) / f"RandomPhoto-update-{update_id}.exe"
        urllib.request.urlretrieve(download_url, update_path)
        with update_path.open("rb") as update_file:
            if update_file.read(2) != b"MZ" or update_path.stat().st_size < 1_000_000:
                raise ValueError("Скачанный файл не похож на полный Windows EXE.")
        if getattr(sys, "frozen", False):
            current_path = Path(sys.executable).resolve()
            script_path = Path(tempfile.gettempdir()) / f"RandomPhoto-update-{update_id}.cmd"
            script_path.write_text(
                "@echo off\n"
                f'set "UPDATE={update_path}"\n'
                f'set "TARGET={current_path}"\n'
                f'set "APP_PID={update_id}"\n'
                "for /L %%i in (1,1,60) do (\n"
                "  tasklist /FI \"PID eq %APP_PID%\" /NH | find \"%APP_PID%\" >nul\n"
                "  if errorlevel 1 goto replace\n"
                "  timeout /t 1 /nobreak >nul\n"
                ")\n"
                "exit /b 1\n"
                ":replace\n"
                "for /L %%i in (1,1,10) do (\n"
                "  copy /Y \"%UPDATE%\" \"%TARGET%\" >nul\n"
                "  if not errorlevel 1 goto launch\n"
                "  timeout /t 1 /nobreak >nul\n"
                ")\n"
                "exit /b 2\n"
                ":launch\n"
                "start \"\" \"%TARGET%\"\n"
                "del \"%UPDATE%\" >nul 2>&1\n"
                f'del "%~f0"\n',
                encoding="utf-8",
            )
            root.after(0, lambda: start_update(script_path, latest_version))
        else:
            root.after(0, lambda: status_var.set("Обновление доступно только в EXE-сборке."))
    except (OSError, ValueError, urllib.error.URLError) as error:
        error_message = str(error)
        root.after(0, lambda: messagebox.showerror("Ошибка обновления", error_message))


def start_update(script_path, latest_version):
    status_var.set(f"Устанавливаю обновление {latest_version}...")
    subprocess.Popen(["cmd", "/c", str(script_path)], shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
    close_app()


def choose_folder():
    global seen_media_paths, seen_media_folder

    folder = filedialog.askdirectory(title="Выберите папку с фотографиями")
    if folder:
        folder_var.set(folder)
        seen_media_paths.clear()
        seen_media_folder = os.path.normcase(os.path.abspath(folder))
        clear_photo_cache()
        save_settings()
        status_var.set("Папка выбрана. Нажмите кнопку, чтобы открыть фото.")


def find_media(folder, include_videos):
    media_files = []

    def scan_directory(directory):
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            scan_directory(entry.path)
                        elif (
                            entry.is_file(follow_symlinks=False)
                            and (
                                Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS
                                or (
                                    include_videos
                                    and Path(entry.name).suffix.lower() in VIDEO_EXTENSIONS
                                )
                            )
                        ):
                            media_files.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            return

    try:
        scan_directory(str(folder))
        return media_files
    except OSError as error:
        messagebox.showerror("Ошибка доступа", f"Не удалось прочитать папку:\n{error}")
        return []


def clear_photo_cache():
    global photo_cache_folder, photo_cache
    photo_cache_folder = None
    photo_cache = []


def refresh_photo_cache(open_when_done=False):
    global media_scan_generation, media_scan_in_progress
    global media_scan_folder, media_scan_include_videos
    global media_scan_open_when_done

    folder = folder_var.get().strip()
    folder_path = Path(folder)
    if not folder_path.is_dir():
        messagebox.showerror("Папка не найдена", "Указанный путь не является существующей папкой.")
        return False

    include_videos = play_videos_var.get()
    if (
        media_scan_in_progress
        and media_scan_folder == folder_path
        and media_scan_include_videos == include_videos
    ):
        media_scan_open_when_done = media_scan_open_when_done or open_when_done
        return True

    media_scan_generation += 1
    generation = media_scan_generation
    media_scan_in_progress = True
    media_scan_folder = folder_path
    media_scan_include_videos = include_videos
    media_scan_open_when_done = open_when_done
    status_var.set("Обновляю список файлов...")
    threading.Thread(
        target=scan_media_worker,
        args=(generation, folder_path, include_videos),
        daemon=True,
    ).start()
    schedule_media_scan_poll()
    return True


def scan_media_worker(generation, folder_path, include_videos):
    try:
        media_files = find_media(folder_path, include_videos)
        error = None
    except OSError as scan_error:
        media_files = []
        error = str(scan_error)
    media_scan_results.put((generation, folder_path, include_videos, media_files, error))


def schedule_media_scan_poll():
    global media_scan_poll_after_id

    if media_scan_poll_after_id is None:
        media_scan_poll_after_id = root.after(100, poll_media_scan_result)


def poll_media_scan_result():
    global photo_cache_folder, photo_cache, media_scan_in_progress
    global media_scan_open_when_done, media_scan_poll_after_id

    media_scan_poll_after_id = None
    try:
        generation, folder_path, include_videos, media_files, error = (
            media_scan_results.get_nowait()
        )
    except Empty:
        if media_scan_in_progress:
            schedule_media_scan_poll()
        return

    if generation != media_scan_generation:
        schedule_media_scan_poll()
        return

    media_scan_in_progress = False
    if error:
        messagebox.showerror("Ошибка доступа", f"Не удалось прочитать папку:\n{error}")
        return
    if (
        Path(folder_var.get().strip()) != folder_path
        or play_videos_var.get() != include_videos
    ):
        status_var.set("Параметры изменились. Обновите список файлов.")
        should_open_media = media_scan_open_when_done
        media_scan_open_when_done = False
        if Path(folder_var.get().strip()).is_dir():
            refresh_photo_cache(open_when_done=should_open_media)
        return

    photo_cache_folder = folder_path
    photo_cache = media_files
    status_var.set(f"Найдено файлов: {len(media_files)}")
    should_open_media = media_scan_open_when_done
    media_scan_open_when_done = False
    if should_open_media and media_files:
        open_random_photo()
    elif should_open_media:
        messagebox.showinfo(
            "Файлы не найдены",
            "В выбранной папке нет поддерживаемых изображений или видео.",
        )


def toggle_video_option():
    clear_photo_cache()
    save_settings()
    state = "включено" if play_videos_var.get() else "выключено"
    status_var.set(f"Воспроизведение видео {state}. Список файлов обновится при следующем выборе.")


def toggle_no_repeat():
    global seen_media_paths, seen_media_folder

    if no_repeat_var.get():
        seen_media_paths.clear()
        folder = folder_var.get().strip()
        seen_media_folder = os.path.normcase(os.path.abspath(folder)) if folder else ""
        status_var.set("Повторы выключены до просмотра всех файлов.")
    else:
        status_var.set("Повторы разрешены.")
    save_settings()


def show_photo_location():
    if last_photo_path is None or not last_photo_path.exists():
        messagebox.showinfo("Файл не выбран", "Сначала откройте фотографию или видео.")
        return
    if sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(last_photo_path)], check=False)


def choose_unseen_media(media_files, seen_paths):
    available_media = [
        media_file
        for media_file in media_files
        if os.path.normcase(os.path.abspath(str(media_file))) not in seen_paths
    ]
    if not available_media:
        seen_paths.clear()
        available_media = media_files
    return random.choice(available_media)


def open_random_photo():
    global photo_cache_folder, photo_cache, last_photo_path
    global seen_media_paths, seen_media_folder

    folder = folder_var.get().strip()
    if not folder:
        messagebox.showwarning("Папка не выбрана", "Сначала выберите папку с фотографиями.")
        return

    folder_path = Path(folder)
    if not folder_path.is_dir():
        messagebox.showerror("Папка не найдена", "Указанный путь не является существующей папкой.")
        return

    if media_scan_in_progress:
        refresh_photo_cache(open_when_done=True)
        return
    if photo_cache_folder != folder_path:
        refresh_photo_cache(open_when_done=True)
        return
    photos = photo_cache
    if not photos:
        messagebox.showinfo(
            "Файлы не найдены",
            "В выбранной папке нет поддерживаемых изображений или видео.",
        )
        return

    if no_repeat_var.get():
        folder_key = os.path.normcase(os.path.abspath(str(folder_path)))
        if seen_media_folder != folder_key:
            seen_media_paths.clear()
            seen_media_folder = folder_key
        photo = choose_unseen_media(photos, seen_media_paths)
    else:
        photo = random.choice(photos)
    last_photo_path = photo
    try:
        if photo.suffix.lower() in VIDEO_EXTENSIONS:
            open_video(photo)
        else:
            show_photo(photo)
        if no_repeat_var.get():
            seen_media_paths.add(os.path.normcase(os.path.abspath(str(photo))))
            save_settings()
        photo_path_var.set(str(photo))
        status_var.set(f"Открыто: {photo.name}")
    except (OSError, ValueError, RuntimeError) as error:
        messagebox.showerror("Не удалось открыть файл", str(error))


def show_photo(photo):
    global preview_window, preview_image, preview_frames
    global preview_durations, preview_frame_index, preview_after_id

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


def open_video(video_path):
    global video_process

    close_preview()
    vlc_candidates = [
        shutil.which("vlc.exe"),
        Path(os.environ.get("ProgramFiles", "")) / "VideoLAN" / "VLC" / "vlc.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "VideoLAN" / "VLC" / "vlc.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VideoLAN" / "VLC" / "vlc.exe",
    ]
    vlc_path = next(
        (Path(candidate) for candidate in vlc_candidates if candidate and Path(candidate).is_file()),
        None,
    )
    if vlc_path is not None:
        video_process = subprocess.Popen(
            [str(vlc_path), "--no-one-instance", "--play-and-exit", str(video_path)],
            close_fds=True,
        )
    elif sys.platform == "win32":
        os.startfile(str(video_path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(video_path)])
    else:
        subprocess.Popen(["xdg-open", str(video_path)])


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
    global preview_after_id, preview_frames, preview_durations, video_process

    if preview_after_id is not None:
        root.after_cancel(preview_after_id)
        preview_after_id = None
    if video_process is not None:
        if video_process.poll() is None:
            video_process.terminate()
            try:
                video_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                video_process.kill()
        video_process = None
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
play_videos_var = tk.BooleanVar(value=initial_settings["play_videos"])
autostart_var = tk.BooleanVar(value=initial_settings["autostart"])
no_repeat_var = tk.BooleanVar(value=initial_settings["no_repeat"])
seen_media_paths = set(initial_settings["seen_media"])
seen_media_folder = initial_settings["seen_media_folder"]
status_var = tk.StringVar(value="Выберите папку с фотографиями.")
photo_path_var = tk.StringVar(value="Фото еще не открывалось")

autostart_configured, autostart_error = configure_autostart(autostart_var.get())
if not autostart_configured:
    autostart_var.set(False)
    status_var.set(f"Не удалось настроить автозапуск: {autostart_error}")

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
    text="Выберите папку, чтобы открыть случайное фото или видео.",
    style="Overlay.TLabel",
).grid(row=2, column=0, columnspan=2, sticky="w", padx=(50, 48))

ttk.Entry(root, textvariable=folder_var).grid(
    row=3, column=0, sticky="ew", padx=(50, 12)
)
ttk.Button(root, text="Выбрать папку...", command=choose_folder).grid(
    row=3, column=1, sticky="ew", padx=(0, 48)
)
ttk.Button(root, text="Открыть случайный файл", command=open_random_photo).grid(
    row=4, column=0, columnspan=2, sticky="ew", padx=50, pady=(12, 12), ipady=9
)
ttk.Button(root, text="Обновить список файлов", command=refresh_photo_cache).grid(
    row=5, column=0, sticky="w", padx=(50, 12), pady=(0, 4)
)
ttk.Checkbutton(
    root,
    text="Воспроизводить видео",
    variable=play_videos_var,
    command=toggle_video_option,
).grid(
    row=5, column=1, sticky="w", padx=(0, 48), pady=(0, 4)
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
update_button = tk.Button(
    root,
    text=f"Проверить обновления (v{APP_VERSION})",
    command=on_update_button_click,
    background="#76585e",
    activebackground="#63484e",
    foreground="#fff8f2",
    activeforeground="#fff8f2",
    relief="flat",
    padx=12,
    pady=6,
)
update_button.grid(row=10, column=0, sticky="w", padx=(50, 0), pady=(0, 24))
ttk.Button(root, text="Настройки", command=show_settings).grid(
    row=10, column=1, sticky="e", padx=(0, 48), pady=(0, 24)
)

hotkey_thread = threading.Thread(target=register_global_hotkey, daemon=True)
hotkey_entry.bind("<KeyPress>", capture_hotkey)
hotkey_thread_id = None
tray_icon = create_tray_icon()
tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
if sys.platform == "win32":
    hotkey_thread.start()
tray_thread.start()

root.protocol("WM_DELETE_WINDOW", hide_app)
root.after(700, lambda: threading.Thread(target=check_for_updates, daemon=True).start())
root.mainloop()
