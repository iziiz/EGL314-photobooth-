import cv2
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from rembg import remove
import os
import sys
import threading
import queue
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
import qrcode
import numpy as np

class DriveManager:
    """Singleton class to manage Google Drive connection"""
    _instance = None
    _drive = None
    _authenticated = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._authenticated:
            self._initialize_drive()
    
    def _initialize_drive(self):
        """Initialize Google Drive connection with better error handling"""
        try:
            gauth = GoogleAuth()
            
            # Load credentials if they exist
            try:
                if os.path.exists("mycreds.txt"):
                    gauth.LoadCredentialsFile("mycreds.txt")
                    print("thing has been loaded")
                else:
                    print("[!] mycreds.txt not found")
            except Exception as e:
                print(f"[!] Failed to load credentials: {e}")

            
            # Handle authentication
            if gauth.credentials is None:
                print("[i] No credentials found, starting authentication...")
                gauth.LocalWebserverAuth()
            elif gauth.access_token_expired:
                print("[i] Token expired, refreshing...")
                gauth.Refresh()
            else:
                gauth.Authorize()
            
            # Save credentials for next time
            gauth.SaveCredentialsFile("mycreds.txt")
            
            self._drive = GoogleDrive(gauth)
            self._authenticated = True
            print("[✓] Google Drive connection established")
            
        except Exception as e:
            print(f"[!] Failed to initialize Google Drive: {e}")
            self._drive = None
            self._authenticated = False
    
    def get_drive(self):
        """Get the drive instance, reinitialize if needed"""
        if not self._authenticated or self._drive is None:
            self._initialize_drive()
        return self._drive
    
    def is_connected(self):
        """Check if drive is connected"""
        return self._authenticated and self._drive is not None

class AsyncUploader:
    """Handle asynchronous uploads to Google Drive"""
    def __init__(self):
        self.upload_queue = queue.Queue()
        self.upload_thread = None
        self.drive_manager = DriveManager()
        self.start_upload_worker()
    
    def start_upload_worker(self):
        """Start the background upload worker thread"""
        if self.upload_thread is None or not self.upload_thread.is_alive():
            self.upload_thread = threading.Thread(target=self._upload_worker, daemon=True)
            self.upload_thread.start()
    
    def _upload_worker(self):
        """Background worker to process upload queue"""
        while True:
            try:
                file_path = self.upload_queue.get(timeout=1)
                if file_path is None:  # Poison pill to stop thread
                    break
                self._upload_file(file_path)
                self.upload_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[!] Upload worker error: {e}")
    
    def upload_and_get_link(self, file_path):
        """Upload a file and return its public share URL"""
        try:
            drive = self.drive_manager.get_drive()
            if not drive:
                print("[!] No drive connection")
                return None

            if not os.path.exists(file_path):
                print(f"[!] File not found: {file_path}")
                return None

            file_metadata = {
                'title': os.path.basename(file_path),
                'parents': [{'id': self._get_or_create_folder()}]
            }

            file = drive.CreateFile(file_metadata)
            file.SetContentFile(file_path)
            file.Upload()

            # Make public
            file.InsertPermission({
                'type': 'anyone',
                'value': 'anyone',
                'role': 'reader'
            })

            share_url = file['alternateLink']
            print(f"[✓] Uploaded and shared: {share_url}")
            return share_url

        except Exception as e:
            print(f"[!] Failed to upload and get link: {e}")
            return None


    def _upload_file(self, file_path):
        """Upload a single file to Google Drive"""
        try:
            drive = self.drive_manager.get_drive()
            if not drive:
                print(f"[!] No drive connection available for {file_path}")
                return False
            
            # Check if file exists
            if not os.path.exists(file_path):
                print(f"[!] File not found: {file_path}")
                return False
            
            # Create file metadata
            file_metadata = {
                'title': os.path.basename(file_path),
                'parents': [{'id': self._get_or_create_folder()}]  # Optional: organize in folder
            }
            
            # Create and upload file
            file = drive.CreateFile(file_metadata)
            file.SetContentFile(file_path)
            file.Upload()
            
            print(f"[✓] Uploaded to Google Drive: {file['title']}")
            return True
            
        except Exception as e:
            print(f"[!] Upload failed for {file_path}: {e}")
            return False
    
    def _get_or_create_folder(self):
        """Get or create a PhotoBooth folder in Google Drive"""
        try:
            drive = self.drive_manager.get_drive()
            if not drive:
                return None
            
            # Search for existing folder
            folder_list = drive.ListFile({
                'q': "title='PhotoBooth' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            }).GetList()
            
            if folder_list:
                return folder_list[0]['id']
            
            # Create folder if it doesn't exist
            folder = drive.CreateFile({
                'title': 'PhotoBooth',
                'mimeType': 'application/vnd.google-apps.folder'
            })
            folder.Upload()
            return folder['id']
            
        except Exception as e:
            print(f"[!] Error managing folder: {e}")
            return None
    
    def queue_upload(self, file_path):
        """Add file to upload queue"""
        if not self.drive_manager.is_connected():
            print(f"[!] Drive not connected, skipping upload of {file_path}")
            return
        
        self.upload_queue.put(file_path)
        print(f"[i] Queued for upload: {os.path.basename(file_path)}")
    
    def stop(self):
        """Stop the upload worker thread"""
        self.upload_queue.put(None)  # Poison pill
        if self.upload_thread and self.upload_thread.is_alive():
            self.upload_thread.join(timeout=5)

def resource_path(relative_path):
    """Gets path to resource"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class PhotoBoothPython:
    def __init__(self, root, title="PhotoBooth"):
        self.root = root
        self.root.title(title)
        self.root.attributes("-fullscreen", True)

        # Initialize async uploader
        self.uploader = AsyncUploader()

        self.cap = self.init_webcam()
        self.cam_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.cam_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self.reference_image = None
        self.img_counter = 0
        self.save_session_id = 1
        self.last_captured = None
        self.last_no_bg = None

        self.preview_mode = False
        self.video_lbl = self.setup_video_label()
        self.countdown_lbl = self.setup_countdown_label()
        self.preview_controls = self.setup_preview_controls()

        self.background_images = ["background1.png", "background2.png", "background3.png"]

        self.setup_control_buttons()
        self.setup_shortcuts()

        self.update_frame()

    def init_webcam(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            raise RuntimeError("Could not open webcam")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        return cap

    def setup_video_label(self):
        label = ttk.Label(self.root)
        label.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=self.cam_width, height=self.cam_height)
        return label

    def setup_countdown_label(self):
        return tk.Label(self.root, text="", font=("Arial", 100, "bold"), fg="white", bg="black")
    
    def capture_background_reference(self):
        ok, frame = self.cap.read()
        if ok:
            self.reference_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).convert("RGBA")
            print("[✓] Captured background reference")


    def setup_preview_controls(self):
        frame = ttk.Frame(self.root)
        frame.place(relx=0.5, rely=0.95, anchor=tk.S)

        self.btn_take_again = ttk.Button(frame, text="Retake Photo", command=self.hide_preview)
        self.btn_cutout = ttk.Button(frame, text="Transparent Cutout", command=lambda: self.show_cutout(self.last_captured, frame))
        self.btn_remove_bg = ttk.Button(frame, text="Remove Background", command=lambda: self.remove_background(self.last_captured, frame))
        self.btn_save_final = ttk.Button(frame, text="Save Final Image", command=self.save_final_image)

        self.btn_take_again.grid(row=0, column=0, padx=5)
        self.btn_cutout.grid(row=0, column=1, padx=5)
        self.btn_remove_bg.grid(row=0, column=2, padx=5)
        self.btn_save_final.grid(row=0, column=3, padx=5)

        frame.place_forget()
        return frame

    def setup_control_buttons(self):
        ctl = ttk.Frame(self.root)
        ctl.pack(pady=10)
        ttk.Button(ctl, text="Capture", command=self.start_countdown).grid(row=0, column=0, padx=5)
        ttk.Button(ctl, text="Quit", command=self.on_close).grid(row=0, column=1, padx=5)

    def setup_shortcuts(self):
        self.root.bind("<space>", lambda _e: self.start_countdown())
        self.root.bind("<Escape>", lambda _e: self.on_close())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def update_frame(self):
        if not self.preview_mode:
            ok, frame = self.cap.read()
            if ok:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                imgtk = ImageTk.PhotoImage(img)
                self.video_lbl.imgtk = imgtk
                self.video_lbl.configure(image=imgtk)
        self.root.after(15, self.update_frame)

    def start_countdown(self):
        if self.countdown_lbl.winfo_ismapped():
            return
        self.count = 5
        self.countdown_lbl.place(relx=1.0, rely=0.0, anchor='ne', x=-20, y=20)
        self.tick_countdown()

    def tick_countdown(self):
        if self.count > 0:
            self.countdown_lbl.config(text=str(self.count))
            self.count -= 1
            self.root.after(1000, self.tick_countdown)
        else:
            self.countdown_lbl.config(text="📸")
            self.root.after(500, self.finish_countdown)

    def finish_countdown(self):
        self.countdown_lbl.place_forget()
        self.take_photo()

    def take_photo(self):
        ok, frame = self.cap.read()
        if ok:
            fname = f"photo_{self.img_counter}.png"
            cv2.imwrite(fname, frame)
            print(f"[✓] Saved {fname}")
            self.img_counter += 1
            self.save_session_id = self.img_counter
            self.last_captured = fname
            self.preview_mode = True

            # Queue for background upload
            self.uploader.queue_upload(fname)

            raw_img = Image.open(fname)
            self.display_image(raw_img)
            self.last_no_bg = None
            self.last_composited = None
            self.last_raw = raw_img
            self.last_raw_path = fname
            self.show_preview_controls()

    def display_image(self, img):
        img = img.resize((self.cam_width, self.cam_height), Image.LANCZOS)
        imgtk = ImageTk.PhotoImage(img)
        self.video_lbl.imgtk = imgtk
        self.video_lbl.configure(image=imgtk)

    def show_preview_controls(self):
        self.preview_controls.place(relx=0.5, rely=0.95, anchor=tk.S)

    def hide_preview(self):
        self.preview_mode = False
        self.preview_controls.place_forget()

        if hasattr(self, "qr_frame"):
            self.qr_frame.destroy()
            del self.qr_frame

    def close_background_selector(self):
        if hasattr(self, "bg_thumb_frame"):
            self.bg_thumb_frame.destroy()
            del self.bg_thumb_frame

    def show_cutout(self, input_path, _):
        try:
            img = Image.open(input_path).convert("RGBA")
            no_bg = remove(img)
            self.last_no_bg = no_bg
            self.preview_mode = True
            self.display_image(no_bg)

            # Save and upload the cutout to the drive
            output_folder = "final_images"
            os.makedirs(output_folder, exist_ok=True)
            filename = f"photo_{self.save_session_id}_cutout.png"
            final_path = os.path.join(output_folder, filename)
            no_bg.save(final_path)

            # Upload and display QR code
            share_url = self.uploader.upload_and_get_link(final_path)
            self.display_qr_code(share_url)

        except Exception as e:
            print(f"[!] Error generating cutout: {e}")


    def remove_background(self, input_path, _):
        try:
            person = Image.open(input_path).convert("RGBA")
            no_bg = remove(person)  # Use rembg directly, no background reference
            self.last_no_bg = no_bg
            self.preview_mode = True
            self.show_background_thumbnails(no_bg, input_path)

        except Exception as e:
            print(f"[!] Error removing background: {e}")

    def show_background_thumbnails(self, no_bg_img, input_path):
        if hasattr(self, "bg_thumb_frame"):
            self.bg_thumb_frame.destroy()

        self.bg_thumb_frame = ttk.Frame(self.root)
        self.bg_thumb_frame.place(relx=0.5, rely=0.7, anchor=tk.CENTER)

        top_row = ttk.Frame(self.bg_thumb_frame)
        top_row.pack(fill="x", pady=(0, 5))

        ttk.Label(top_row, text="Choose Background:").pack(side="left", padx=10)

        close_btn = ttk.Button(top_row, text="✕ Close", command=self.close_background_selector)
        close_btn.pack(side="right", padx=10)

        thumbs_container = ttk.Frame(self.bg_thumb_frame)
        thumbs_container.pack()

        self.thumb_imgs = []

        def on_bg_click(bg_path):
            background = Image.open(resource_path(bg_path)).convert("RGBA").resize(no_bg_img.size)
            final_img = Image.alpha_composite(background, no_bg_img)
            self.display_image(final_img)
            self.last_composited = final_img
            self.last_composited_path = input_path.replace(".png", "_edited.png")

        for idx, bg_path in enumerate(self.background_images):
            bg = Image.open(resource_path(bg_path)).convert("RGBA").resize(no_bg_img.size)
            thumb_img = Image.alpha_composite(bg, no_bg_img)
            thumb_img.thumbnail((200, 150), Image.LANCZOS)
            imgtk = ImageTk.PhotoImage(thumb_img)
            self.thumb_imgs.append(imgtk)

            btn = tk.Button(thumbs_container, image=imgtk, command=lambda p=bg_path: on_bg_click(p))
            btn.grid(row=0, column=idx, padx=5, pady=5)

    def reselect_background(self):
        if self.last_captured and self.last_no_bg:
            self.preview_mode = True
            self.show_background_thumbnails(self.last_no_bg, self.last_captured)
            self.show_preview_controls()
        else:
            print("[!] No cutout found to reselect background. Take a photo first.")

    def save_final_image(self):
        output_folder = "final_images"
        os.makedirs(output_folder, exist_ok=True)

        image_to_save = None
        filename_suffix = ""

        if hasattr(self, "last_composited") and self.last_composited:
            image_to_save = self.last_composited
            filename_suffix = "_composited"
        elif hasattr(self, "last_no_bg") and self.last_no_bg:
            image_to_save = self.last_no_bg
            filename_suffix = "_cutout"
        elif hasattr(self, "last_raw") and self.last_raw:
            image_to_save = self.last_raw
            filename_suffix = "_raw"
        else:
            print("[!] No image to save.")
            return

        filename = f"photo_{self.save_session_id}{filename_suffix}.png"
        final_path = os.path.join(output_folder, filename)

        try:
            image_to_save.save(final_path)
            print(f"[✓] Saved final image: {final_path}")
            # Upload and get share link
            share_url = self.uploader.upload_and_get_link(final_path)
            # Show QR code with download link
            self.display_qr_code(share_url)

        except Exception as e:
            print(f"[!] Error saving image: {e}")


    def display_qr_code(self, url):
        if hasattr(self, "qr_frame"):
            self.qr_frame.destroy()

        self.qr_frame = ttk.Frame(self.root)

        self.qr_frame.place(relx=0.02, rely=0.02, anchor='nw')

        qr_img = qrcode.make(url)
        qr_img = qr_img.resize((200, 200), Image.LANCZOS)
        tk_qr = ImageTk.PhotoImage(qr_img)

        label = ttk.Label(self.qr_frame, text="Scan to Download", font=("Arial", 12))
        label.pack()

        qr_label = ttk.Label(self.qr_frame, image=tk_qr)
        qr_label.image = tk_qr
        qr_label.pack(pady=5)

    def on_close(self):
        # Stop the uploader
        self.uploader.stop()
        
        if self.cap.isOpened():
            self.cap.release()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    PhotoBoothPython(root)
    root.mainloop()