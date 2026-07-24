import os
import sys
import json
import time
import winreg
import ctypes
import threading
import tempfile
import subprocess
import winsound
import hashlib
import cv2
import numpy as np
import pyperclip
from PIL import ImageGrab, Image, ImageDraw
import psutil
import telebot
from telebot import types
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import pystray

# --- Портативные пути (всё лежит рядом с .exe или скриптом) ---
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Защита от повторного запуска одной и той же копии (Single Instance) ---
def ensure_single_instance():
    # Хеш от абсолютного пути папки, чтобы блокировать запуск именно этой конкретной копии
    path_hash = hashlib.md5(APP_DIR.lower().encode('utf-8')).hexdigest()
    mutex_name = f"Global\\PCControlBot_{path_hash}"
    
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    
    # 183 = ERROR_ALREADY_EXISTS
    if kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(
            0, 
            "Эта копия программы уже запущена!", 
            "Ошибка запуска", 
            0x10 | 0x00 # MB_ICONERROR | MB_OK
        )
        sys.exit(0)
    return mutex

# Сохраняем ссылку на мьютекс в глобальной переменной, чтобы сборщик мусора не закрыл его
APP_MUTEX = ensure_single_instance()

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
ICON_FILE = "icon.ico"

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = APP_DIR
    return os.path.join(base_path, relative_path)

# --- Эмуляция клавиш Windows ---

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_LWIN = 0x5B
VK_KEY_D = 0x44

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

def press_key(vk_code):
    try:
        ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
        ctypes.windll.user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception as e:
        print(f"Ошибка эмуляции: {e}")

def minimize_all_windows():
    try:
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_KEY_D, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_KEY_D, 0, KEYEVENTF_KEYUP, 0)
        ctypes.windll.user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
    except Exception as e:
        print(f"Ошибка сворачивания окон: {e}")

def turn_off_monitor():
    try:
        ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
    except Exception as e:
        print(f"Ошибка выключения монитора: {e}")

# --- Работа с конфигом и автозагрузкой ---

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "token": "", 
        "admin_id": "", 
        "autostart": False,
        "use_password": False,
        "password": "",
        "allowed_dirs": "C:\\",
        "blocked_dirs": "C:\\Windows, C:\\Program Files",
        "shortcuts": {},
        "disclaimer_accepted": False
    }

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения конфига: {e}")
    set_autostart(config_data.get("autostart", False))

def set_autostart(enable=True):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    app_name = "PCControlBotGUI"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
        if enable:
            if getattr(sys, 'frozen', False):
                executable = f'"{sys.executable}" --minimized'
            else:
                executable = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, executable)
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Ошибка автозагрузки: {e}")

# --- Генератор/Загрузчик значка ---

def get_tray_image():
    icon_p = resource_path(ICON_FILE)
    if os.path.exists(icon_p):
        try:
            return Image.open(icon_p)
        except Exception:
            pass
    
    img = Image.new('RGB', (64, 64), color=(30, 144, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, 48, 48], fill=(255, 255, 255))
    return img

# --- Черный экран (Штора) ---

class BlackoutScreen:
    def __init__(self):
        self.root = None
        self.is_active = False

    def toggle(self):
        if self.is_active:
            self.hide()
            return False
        else:
            self.show()
            return True

    def show(self):
        if self.is_active:
            return
        def _run():
            self.root = tk.Tk()
            self.root.attributes('-fullscreen', True)
            self.root.attributes('-topmost', True)
            self.root.configure(background='black')
            self.root.config(cursor="none")
            self.is_active = True
            self.root.mainloop()
        
        threading.Thread(target=_run, daemon=True).start()

    def hide(self):
        if self.root and self.is_active:
            self.is_active = False
            self.root.destroy()
            self.root = None

blackout = BlackoutScreen()

# --- Логика Telegram-бота ---

class PCBot:
    def __init__(self, config):
        self.config = config
        self.token = config.get("token", "").strip()
        self.admin_id = str(config.get("admin_id", "")).strip()
        self.use_password = config.get("use_password", False)
        self.password = config.get("password", "").strip()
        
        self.allowed_dirs = [os.path.abspath(d.strip()) for d in config.get("allowed_dirs", "").split(',') if d.strip()]
        self.blocked_dirs = [os.path.abspath(d.strip()) for d in config.get("blocked_dirs", "").split(',') if d.strip()]
        self.shortcuts = config.get("shortcuts", {})
        
        self.path_cache = {}
        self.path_counter = 0

        self.bot = telebot.TeleBot(self.token)
        self.is_running = False
        
        self.authenticated_users = {}
        self._setup_handlers()
        self._notify_startup()

    def get_path_id(self, path):
        self.path_counter += 1
        key = f"p_{self.path_counter}"
        self.path_cache[key] = path
        return key

    def _notify_startup(self):
        try:
            self.bot.send_message(self.admin_id, "🟢 **Компьютер включен и бот запущен!**", parse_mode="Markdown")
        except Exception as e:
            print(f"Не удалось отправить сообщение о старте: {e}")

    def safe_delete_message(self, chat_id, message_id):
        try:
            self.bot.delete_message(chat_id, message_id)
        except Exception:
            pass

    def clean_command(self, text):
        if ' ' in text and text.startswith('@'):
            return text.split(' ', 1)[1].strip()
        return text.strip()

    def check_access(self, user_id):
        if str(user_id) != self.admin_id:
            return False
        if not self.use_password:
            return True
        return self.authenticated_users.get(str(user_id), False)

    def is_safe_path(self, target_path):
        abs_target = os.path.abspath(target_path)
        for b_dir in self.blocked_dirs:
            if abs_target == b_dir or abs_target.startswith(b_dir + os.sep):
                return False
        if not self.allowed_dirs:
            return True
        for a_dir in self.allowed_dirs:
            if abs_target == a_dir or abs_target.startswith(a_dir + os.sep):
                return True
        return False

    def speak_text_native(self, text):
        def _speak():
            try:
                safe_text = text.replace('"', '`"')
                ps_cmd = f'Add-Type -AssemblyName System.Speech; $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $synth.Speak("{safe_text}")'
                subprocess.run(["powershell", "-Command", ps_cmd], creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as e:
                print(f"Ошибка речи: {e}")
        threading.Thread(target=_speak, daemon=True).start()

    def show_topmost_popup(self, title, text):
        def _create_popup():
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass
            popup = tk.Tk()
            popup.title(title)
            window_width, window_height = 400, 180
            screen_width, screen_height = popup.winfo_screenwidth(), popup.winfo_screenheight()
            center_x = int((screen_width / 2) - (window_width / 2))
            center_y = int((screen_height / 2) - (window_height / 2))
            popup.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')
            popup.resizable(False, False)
            popup.attributes('-topmost', True)
            popup.lift()
            popup.focus_force()

            frame = ttk.Frame(popup, padding="15")
            frame.pack(fill=tk.BOTH, expand=True)
            ttk.Label(frame, text=title, font=("Helvetica", 12, "bold")).pack(anchor=tk.W, pady=(0, 8))
            ttk.Label(frame, text=text, font=("Helvetica", 10), wraplength=360).pack(anchor=tk.W, fill=tk.BOTH, expand=True, pady=(0, 15))
            ttk.Button(frame, text="ОК", command=popup.destroy).pack(anchor=tk.E)
            popup.mainloop()
        threading.Thread(target=_create_popup, daemon=True).start()

    def get_main_menu(self):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        buttons = [
            "📸 Скриншот", "📷 Веб-камера",
            "🚀 Быстрый запуск", "🎵 Медиа и Громкость",
            "🖤 Экран-Штора (Вкл/Выкл)", "🙈 Свернуть все окна",
            "🎥 Запись видео", "📁 Файлы (FTP)",
            "📋 Буфер обмена", "📊 Диспетчер задач",
            "⚡ Управление питанием", "⏱️ Таймеры выключения",
            "ℹ️ Статус ПК", "💬 Полезные команды"
        ]
        if self.use_password:
            buttons.append("🔒 Выйти из сессии")
        markup.add(*buttons)
        return markup

    def record_screen_logic(self, chat_id, duration):
        status_msg = self.bot.send_message(chat_id, f"🎥 Записываю видео экрана ({duration} сек)...")
        try:
            path = os.path.join(tempfile.gettempdir(), 'screen_record.mp4')
            screen_size = ImageGrab.grab().size
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            out = cv2.VideoWriter(path, fourcc, 10.0, screen_size)
            start_time = time.time()
            while time.time() - start_time < duration:
                img = ImageGrab.grab()
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                out.write(frame)
            out.release()
            with open(path, 'rb') as video:
                self.bot.send_video(chat_id, video, caption=f"🎥 Видео экрана ({duration} сек)")
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Ошибка записи видео: {e}")
        finally:
            self.safe_delete_message(chat_id, status_msg.message_id)

    def send_dir_contents(self, chat_id, target_dir=""):
        if not target_dir:
            target_dir = self.allowed_dirs[0] if self.allowed_dirs else "C:\\"
        target_dir = os.path.abspath(target_dir)
        if not self.is_safe_path(target_dir):
            self.bot.send_message(chat_id, "⛔ Доступ к этой папке заблокирован!")
            return
        if not os.path.exists(target_dir):
            self.bot.send_message(chat_id, "❌ Папка не найдена!")
            return

        try:
            items = os.listdir(target_dir)
            markup = types.InlineKeyboardMarkup(row_width=1)
            parent_dir = os.path.dirname(target_dir)
            if parent_dir and parent_dir != target_dir and self.is_safe_path(parent_dir):
                parent_id = self.get_path_id(parent_dir)
                markup.add(types.InlineKeyboardButton("🔙 .. (Вверх)", callback_data=f"f_d:{parent_id}"))

            text = f"📁 **Содержимое папки:**\n`{target_dir}`\n\n"
            count = 0
            for item in sorted(items):
                if count >= 25:
                    text += "\n*...показаны первые 25 элементов*"
                    break
                full_item_path = os.path.join(target_dir, item)
                if not self.is_safe_path(full_item_path):
                    continue
                item_id = self.get_path_id(full_item_path)
                if os.path.isdir(full_item_path):
                    markup.add(types.InlineKeyboardButton(f"📁 {item}", callback_data=f"f_d:{item_id}"))
                else:
                    markup.add(types.InlineKeyboardButton(f"📄 {item}", callback_data=f"f_g:{item_id}"))
                count += 1
            self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
        except Exception as e:
            self.bot.send_message(chat_id, f"❌ Ошибка чтения папки: {e}")

    def _setup_handlers(self):
        @self.bot.message_handler(commands=['start', 'menu'])
        def send_welcome(message):
            user_id = str(message.from_user.id)
            if user_id != self.admin_id:
                self.bot.send_message(message.chat.id, "⛔ Доступ запрещен!")
                return
            if self.use_password and not self.authenticated_users.get(user_id, False):
                self.bot.send_message(message.chat.id, "🔐 **Требуется авторизация!**\n\nВведите пароль доступа:", reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
                return
            self.bot.send_message(message.chat.id, "💻 **Главное меню управления ПК**\n\nВыберите нужный раздел:", reply_markup=self.get_main_menu(), parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: str(m.from_user.id) == self.admin_id and self.use_password and not self.authenticated_users.get(str(m.from_user.id), False))
        def handle_password_input(message):
            user_id = str(message.from_user.id)
            self.safe_delete_message(message.chat.id, message.message_id)
            if message.text.strip() == self.password:
                self.authenticated_users[user_id] = True
                self.bot.send_message(message.chat.id, "✅ **Пароль верен!** Авторизация прошла успешно.", reply_markup=self.get_main_menu(), parse_mode="Markdown")
            else:
                status_msg = self.bot.send_message(message.chat.id, "❌ Неверный пароль!")
                time.sleep(3)
                self.safe_delete_message(message.chat.id, status_msg.message_id)

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🔒 Выйти из сессии")
        def logout(message):
            user_id = str(message.from_user.id)
            self.authenticated_users[user_id] = False
            self.bot.send_message(message.chat.id, "🔒 Сессия завершена. Для продолжения работы введите `/start`.", reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🎵 Медиа и Громкость")
        def media_control_menu(message):
            markup = types.InlineKeyboardMarkup(row_width=3)
            markup.add(
                types.InlineKeyboardButton("⏮️ Назад", callback_data="m_prev"),
                types.InlineKeyboardButton("⏯️ Пауза / Плей", callback_data="m_play"),
                types.InlineKeyboardButton("⏭️ Вперед", callback_data="m_next")
            )
            markup.add(
                types.InlineKeyboardButton("🔉 Тише", callback_data="m_vdown"),
                types.InlineKeyboardButton("🔇 Вкл/Выкл звук", callback_data="m_mute"),
                types.InlineKeyboardButton("🔊 Громче", callback_data="m_vup")
            )
            self.bot.send_message(message.chat.id, "🎵 **Управление медиаплеером и звуком ПК:**", reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "⚡ Управление питанием")
        def power_control_menu(message):
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("🔴 Выключить ПК", callback_data="p_off"),
                types.InlineKeyboardButton("🔄 Перезагрузить", callback_data="p_reboot"),
                types.InlineKeyboardButton("🖤 Погасить экран", callback_data="p_monoff"),
                types.InlineKeyboardButton("🌙 Спящий режим", callback_data="p_sleep")
            )
            self.bot.send_message(message.chat.id, "⚡ **Выберите действие управления питанием:**", reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "⏱️ Таймеры выключения")
        def timers_menu(message):
            markup = types.InlineKeyboardMarkup(row_width=3)
            markup.add(
                types.InlineKeyboardButton("15 мин", callback_data="t_15"),
                types.InlineKeyboardButton("30 мин", callback_data="t_30"),
                types.InlineKeyboardButton("60 мин", callback_data="t_60")
            )
            markup.add(
                types.InlineKeyboardButton("90 мин", callback_data="t_90"),
                types.InlineKeyboardButton("120 мин", callback_data="t_120"),
                types.InlineKeyboardButton("❌ Отмена таймера", callback_data="t_cancel")
            )
            self.bot.send_message(message.chat.id, "⏱️ **Укажите время для автоматического выключения ПК:**", reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🚀 Быстрый запуск")
        def run_shortcuts_menu(message):
            if not self.shortcuts:
                self.bot.send_message(message.chat.id, "ℹ️ **Список ярлыков пуст!**\n\nДобавьте программы, ярлыки или ссылки в приложении на ПК.", parse_mode="Markdown")
                return
            markup = types.InlineKeyboardMarkup(row_width=1)
            for name, path in self.shortcuts.items():
                s_id = self.get_path_id(path)
                markup.add(types.InlineKeyboardButton(f"🚀 {name}", callback_data=f"run_s:{s_id}"))
            self.bot.send_message(message.chat.id, "🚀 **Выберите программу, ярлык или ссылку для запуска:**", reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🖤 Экран-Штора (Вкл/Выкл)")
        def toggle_blackout(message):
            active = blackout.toggle()
            if active:
                self.bot.send_message(message.chat.id, "🖤 **Экран заблокирован черной шторой!**", parse_mode="Markdown")
            else:
                self.bot.send_message(message.chat.id, "☀️ **Экран разблокирован!**", parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "📸 Скриншот")
        def take_screenshot(message):
            status_msg = self.bot.send_message(message.chat.id, "⏳ Создаю скриншот...")
            try:
                path = os.path.join(tempfile.gettempdir(), 'screenshot.png')
                ImageGrab.grab().save(path, 'PNG')
                with open(path, 'rb') as photo:
                    self.bot.send_photo(message.chat.id, photo, caption="📸 Текущий экран ПК")
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                self.bot.send_message(message.chat.id, f"❌ Ошибка скриншота: {e}")
            finally:
                self.safe_delete_message(message.chat.id, status_msg.message_id)

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "📷 Веб-камера")
        def webcam_snap(message):
            status_msg = self.bot.send_message(message.chat.id, "📷 Делаю снимок с веб-камеры...")
            try:
                cap = cv2.VideoCapture(0)
                ret, frame = cap.read()
                cap.release()
                if ret:
                    path = os.path.join(tempfile.gettempdir(), 'webcam.jpg')
                    cv2.imwrite(path, frame)
                    with open(path, 'rb') as photo:
                        self.bot.send_photo(message.chat.id, photo, caption="📷 Фото с веб-камеры")
                    if os.path.exists(path):
                        os.remove(path)
                else:
                    self.bot.send_message(message.chat.id, "❌ Не удалось получить изображение с камеры.")
            except Exception as e:
                self.bot.send_message(message.chat.id, f"❌ Ошибка веб-камеры: {e}")
            finally:
                self.safe_delete_message(message.chat.id, status_msg.message_id)

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🎥 Запись видео")
        def record_screen_menu(message):
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("⏱️ 5 секунд", callback_data="rec_5"),
                types.InlineKeyboardButton("⏱️ 10 секунд", callback_data="rec_10"),
                types.InlineKeyboardButton("⏱️ 15 секунд", callback_data="rec_15"),
                types.InlineKeyboardButton("⏱️ 30 секунд", callback_data="rec_30")
            )
            self.bot.send_message(message.chat.id, "🎥 **Выберите длительность записи экрана:**", reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "🙈 Свернуть все окна")
        def fold_windows(message):
            minimize_all_windows()
            status_msg = self.bot.send_message(message.chat.id, "🙈 Все окна свернуты!")
            time.sleep(2)
            self.safe_delete_message(message.chat.id, status_msg.message_id)

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "📁 Файлы (FTP)")
        def open_file_manager(message):
            self.send_dir_contents(message.chat.id, "")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "📋 Буфер обмена")
        def clipboard_menu(message):
            try:
                text = pyperclip.paste()
                text = f"`{text}`" if text else "<i>Буфер обмена пуст.</i>"
            except Exception as e:
                text = f"Ошибка чтения: {e}"
            self.bot.send_message(message.chat.id, f"📋 **Буфер обмена ПК:**\n\n{text}", parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "ℹ️ Статус ПК")
        def pc_status(message):
            cpu = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            try:
                disk = psutil.disk_usage('C:\\')
                d_free = round(disk.free / (1024**3), 1)
                d_perc = disk.percent
            except Exception:
                d_free, d_perc = "N/A", "N/A"
            text = f"⚙️ **Статус:**\n💻 ЦП: `{cpu}%`\n🧠 ОЗУ: `{ram.percent}%`\n💾 Диск C: `{d_perc}%` (Свободно: {d_free} ГБ)"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Обновить", callback_data="refresh_status"))
            self.bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "📊 Диспетчер задач")
        def task_manager(message):
            procs = sorted([p.info for p in psutil.process_iter(['pid', 'name', 'memory_percent']) if p.info], key=lambda x: x['memory_percent'] or 0, reverse=True)[:10]
            text = "📊 **Топ-10 процессов по памяти:**\n\n"
            for p in procs:
                mem = f"{p['memory_percent']:.1f}%" if p['memory_percent'] else "N/A"
                text += f"• `{p['name']}` (PID: `{p['pid']}`) — {mem}\n"
            text += "\n💡 `/close <имя>`"
            self.bot.send_message(message.chat.id, text, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and m.text == "💬 Полезные команды")
        def show_help_commands(message):
            help_text = "🛠️ **Команды:**\n`/say Текст`\n`/msg Заголовок | Текст`\n`/run Путь`\n`/close Имя`\n`/get Путь`\n`/setcb Текст`"
            self.bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/close'))
        def close_app(message):
            name = self.clean_command(message.text).replace('/close', '', 1).strip().lower()
            count = sum(1 for p in psutil.process_iter(['name']) if name in p.info['name'].lower() and p.terminate())
            self.bot.send_message(message.chat.id, f"✅ Закрыто: {count}" if count else "❌ Не найдено.")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/say'))
        def say(message):
            text = self.clean_command(message.text).replace('/say', '', 1).strip()
            if text:
                self.speak_text_native(text)
                self.bot.send_message(message.chat.id, f"🗣️ `{text}`")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/run'))
        def run(message):
            target = self.clean_command(message.text).replace('/run', '', 1).strip()
            if target:
                try:
                    os.startfile(target)
                    self.bot.send_message(message.chat.id, f"🚀 Запущено: `{target}`", parse_mode="Markdown")
                except Exception as e:
                    self.bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/msg'))
        def msg(message):
            raw = self.clean_command(message.text).replace('/msg', '', 1).strip()
            title, content = raw.split('|', 1) if '|' in raw else ("Уведомление", raw)
            self.show_topmost_popup(title.strip(), content.strip())
            self.bot.send_message(message.chat.id, "📌 Выведено на экран!")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/get'))
        def get_file(message):
            path = os.path.abspath(self.clean_command(message.text).replace('/get', '', 1).strip())
            if self.is_safe_path(path) and os.path.isfile(path):
                with open(path, 'rb') as doc:
                    self.bot.send_document(message.chat.id, doc)
            else:
                self.bot.send_message(message.chat.id, "❌ Файл не найден или заблокирован.")

        @self.bot.message_handler(func=lambda m: self.check_access(m.from_user.id) and self.clean_command(m.text).startswith('/setcb'))
        def setcb(message):
            text = self.clean_command(message.text).replace('/setcb', '', 1).strip()
            if text:
                pyperclip.copy(text)
                self.bot.send_message(message.chat.id, "✅ Скопировано в буфер обмена!")

        @self.bot.callback_query_handler(func=lambda call: self.check_access(call.from_user.id))
        def callback(call):
            if call.data == "m_play": press_key(VK_MEDIA_PLAY_PAUSE)
            elif call.data == "m_next": press_key(VK_MEDIA_NEXT_TRACK)
            elif call.data == "m_prev": press_key(VK_MEDIA_PREV_TRACK)
            elif call.data == "m_vup": press_key(VK_VOLUME_UP)
            elif call.data == "m_vdown": press_key(VK_VOLUME_DOWN)
            elif call.data == "m_mute": press_key(VK_VOLUME_MUTE)
            elif call.data == "p_off": os.system("shutdown -s -t 5")
            elif call.data == "p_reboot": os.system("shutdown -r -t 5")
            elif call.data == "p_monoff": turn_off_monitor()
            elif call.data == "p_sleep": os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")
            elif call.data.startswith("t_"):
                if call.data == "t_cancel": os.system("shutdown -a")
                else: os.system(f"shutdown -s -t {int(call.data.split('_')[1]) * 60}")
            elif call.data.startswith("run_s:"):
                target = self.path_cache.get(call.data.split("run_s:", 1)[1], "")
                if target: os.startfile(target)
            elif call.data.startswith("f_d:"):
                self.send_dir_contents(call.message.chat.id, self.path_cache.get(call.data.split("f_d:", 1)[1], ""))
            elif call.data.startswith("f_g:"):
                path = self.path_cache.get(call.data.split("f_g:", 1)[1], "")
                if os.path.isfile(path):
                    with open(path, 'rb') as doc:
                        self.bot.send_document(call.message.chat.id, doc)
            elif call.data.startswith("rec_"):
                threading.Thread(target=self.record_screen_logic, args=(call.message.chat.id, int(call.data.split("_")[1])), daemon=True).start()
            self.bot.answer_callback_query(call.id)

    def start(self):
        self.bot.infinity_polling()

    def stop(self):
        self.bot.stop_polling()

# --- GUI Интерфейс ---

class AppGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("PC Remote Controller")
        self.root.geometry("520x680")
        self.root.resizable(False, False)

        icon_p = resource_path(ICON_FILE)
        if os.path.exists(icon_p):
            try:
                self.root.iconbitmap(icon_p)
            except Exception:
                pass

        self.bot_instance = None
        self.config = load_config()

        if not self.check_disclaimer():
            sys.exit(0)

        self._build_ui()
        self.init_system_tray()

        if "--minimized" in sys.argv:
            self.root.withdraw()

        if self.config.get("token") and self.config.get("admin_id"):
            self.start_bot()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

    def check_disclaimer(self):
        if not self.config.get("disclaimer_accepted", False):
            text = "⚠️ **ДИСКЛЕЙМЕР**\n\nПрограмма предназначена для личного удаленного управления. Вы несете полную ответственность за безопасность данных."
            if messagebox.askyesno("Дисклеймер", text):
                self.config["disclaimer_accepted"] = True
                save_config(self.config)
                return True
            return False
        return True

    def init_system_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Открыть окно", self.show_from_tray, default=True),
            pystray.MenuItem("▶️ Запустить бота", self.start_bot),
            pystray.MenuItem("⏹️ Остановить бота", self.stop_bot),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Выход", self.quit_app)
        )
        self.tray_icon = pystray.Icon("PCControlBot", get_tray_image(), "PC Controller", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def hide_to_tray(self):
        self.root.withdraw()

    def show_from_tray(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def quit_app(self, icon=None, item=None):
        if self.tray_icon: self.tray_icon.stop()
        if self.bot_instance: self.bot_instance.stop()
        self.root.after(0, self.root.destroy)

    def setup_paste(self, entry):
        m = tk.Menu(entry, tearoff=0)
        m.add_command(label="Вставить", command=lambda: entry.event_generate("<<Paste>>"))
        entry.bind("<Button-3>", lambda e: m.tk_popup(e.x_root, e.y_root))

        def do_paste(e):
            entry.event_generate("<<Paste>>")
            return "break"

        # Привязка для латиницы (V) и кириллицы (М)
        for seq in ("<Control-v>", "<Control-V>", "<Control-Cyrillic_em>", "<Control-Cyrillic_EM>"):
            entry.bind(seq, do_paste)

        # Перехват физической клавиши 'V' (keycode 86) при зажатом Ctrl независимо от раскладки
        def check_keycode_paste(event):
            if event.keycode == 86 and (event.state & 0x4):
                entry.event_generate("<<Paste>>")
                return "break"

        entry.bind("<KeyPress>", check_keycode_paste, add="+")

    def toggle_visibility(self, entry, btn):
        if entry.cget('show') == '*':
            entry.config(show='')
            btn.config(text='🙈')
        else:
            entry.config(show='*')
            btn.config(text='👁️')

    def browse_path(self):
        p = filedialog.askopenfilename()
        if p:
            self.sc_path_entry.delete(0, tk.END)
            self.sc_path_entry.insert(0, p)

    def add_shortcut(self):
        name, path = self.sc_name_entry.get().strip(), self.sc_path_entry.get().strip()
        if name and path:
            if "shortcuts" not in self.config: self.config["shortcuts"] = {}
            self.config["shortcuts"][name] = path
            save_config(self.config)
            self.update_list()
            self.sc_name_entry.delete(0, tk.END)
            self.sc_path_entry.delete(0, tk.END)

    def delete_shortcut(self):
        sel = self.sc_listbox.curselection()
        if sel:
            name = self.sc_listbox.get(sel[0]).split(" -> ")[0]
            if name in self.config.get("shortcuts", {}):
                del self.config["shortcuts"][name]
                save_config(self.config)
                self.update_list()

    def update_list(self):
        self.sc_listbox.delete(0, tk.END)
        for n, p in self.config.get("shortcuts", {}).items():
            self.sc_listbox.insert(tk.END, f"{n} -> {p}")

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding="15")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="🤖 Управление ПК через Telegram", font=("Helvetica", 13, "bold")).pack(pady=(0, 10))

        # Настройки
        sf = ttk.LabelFrame(frame, text="Настройки", padding="10")
        sf.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(sf, text="Bot Token:").grid(row=0, column=0, sticky=tk.W)
        self.token_entry = ttk.Entry(sf, show="*")
        self.token_entry.grid(row=0, column=1, sticky=tk.EW, padx=5)
        self.token_entry.insert(0, self.config.get("token", ""))
        self.setup_paste(self.token_entry)
        
        te = ttk.Button(sf, text="👁️", width=3, command=lambda: self.toggle_visibility(self.token_entry, te))
        te.grid(row=0, column=2)

        ttk.Label(sf, text="User ID:").grid(row=1, column=0, sticky=tk.W)
        self.admin_entry = ttk.Entry(sf, show="*")
        self.admin_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=5)
        self.admin_entry.insert(0, self.config.get("admin_id", ""))
        self.setup_paste(self.admin_entry)

        ae = ttk.Button(sf, text="👁️", width=3, command=lambda: self.toggle_visibility(self.admin_entry, ae))
        ae.grid(row=1, column=2)

        ttk.Label(sf, text="Папка FTP:").grid(row=2, column=0, sticky=tk.W)
        self.allowed_entry = ttk.Entry(sf)
        self.allowed_entry.grid(row=2, column=1, columnspan=2, sticky=tk.EW, padx=5)
        self.allowed_entry.insert(0, self.config.get("allowed_dirs", "C:\\"))
        self.setup_paste(self.allowed_entry)
        sf.columnconfigure(1, weight=1)

        # Ярлыки
        sc_frame = ttk.LabelFrame(frame, text="🚀 Быстрый запуск", padding="10")
        sc_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        sub = ttk.Frame(sc_frame)
        sub.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(sub, text="Имя:").grid(row=0, column=0)
        self.sc_name_entry = ttk.Entry(sub, width=12)
        self.sc_name_entry.grid(row=0, column=1, padx=5)
        self.setup_paste(self.sc_name_entry)

        ttk.Label(sub, text="Путь:").grid(row=0, column=2)
        self.sc_path_entry = ttk.Entry(sub)
        self.sc_path_entry.grid(row=0, column=3, sticky=tk.EW, padx=5)
        self.setup_paste(self.sc_path_entry)

        ttk.Button(sub, text="📁", width=3, command=self.browse_path).grid(row=0, column=4)
        sub.columnconfigure(3, weight=1)

        ttk.Button(sc_frame, text="➕ Добавить", command=self.add_shortcut).pack(fill=tk.X, pady=(0, 5))
        self.sc_listbox = tk.Listbox(sc_frame, height=4)
        self.sc_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
        ttk.Button(sc_frame, text="❌ Удалить", command=self.delete_shortcut).pack(anchor=tk.E)
        self.update_list()

        # Автозапуск и статус
        self.autostart_var = tk.BooleanVar(value=self.config.get("autostart", False))
        ttk.Checkbutton(frame, text="Запускать вместе с Windows (в трее)", variable=self.autostart_var).pack(anchor=tk.W, pady=(0, 5))

        self.status_label = ttk.Label(frame, text="Статус: Остановлен 🔴", font=("Helvetica", 10, "italic"))
        self.status_label.pack(pady=(0, 5))

        bf = ttk.Frame(frame)
        bf.pack(fill=tk.X)
        self.start_btn = ttk.Button(bf, text="▶️ Запустить бота", command=self.start_bot)
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        self.stop_btn = ttk.Button(bf, text="⏹️ Остановить", command=self.stop_bot, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(5, 0))

    def start_bot(self):
        self.config["token"] = self.token_entry.get().strip()
        self.config["admin_id"] = self.admin_entry.get().strip()
        self.config["allowed_dirs"] = self.allowed_entry.get().strip()
        self.config["autostart"] = self.autostart_var.get()

        if not self.config["token"] or not self.config["admin_id"]:
            messagebox.showwarning("Ошибка", "Заполните Token и Telegram User ID!")
            return

        save_config(self.config)
        try:
            self.bot_instance = PCBot(self.config)
            threading.Thread(target=self.bot_instance.start, daemon=True).start()
            self.status_label.config(text="Статус: Работает 🟢")
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def stop_bot(self):
        if self.bot_instance:
            self.bot_instance.stop()
            self.status_label.config(text="Статус: Остановлен 🔴")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

if __name__ == "__main__":
    root = tk.Tk()
    app = AppGUI(root)
    root.mainloop()