import os
import time
import cv2
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Form 
from ultralytics import YOLO
from paddleocr import PaddleOCR
import threading
from uuid import uuid4
import boto3
from botocore.exceptions import BotoCoreError, ClientError

DOWNLOAD_DIR = "uploads"
FOLDERS = [f"P{i+1}" for i in range(5)]
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
for folder in FOLDERS:
    os.makedirs(os.path.join(DOWNLOAD_DIR, folder), exist_ok=True)

DO_SPACES_REGION = "sgp1"
DO_SPACES_ENDPOINT = "https://sgp1.digitaloceanspaces.com"
DO_SPACES_KEY = "DO801UTAA8KY7NAHHRC8"
DO_SPACES_SECRET = "Hos62RJQmYVkARvJmk96xPXMG04p58SK5q/WTlzpycE"
DO_SPACES_BUCKET = "pitmonitoring"

s3 = boto3.client(
    's3',
    region_name=DO_SPACES_REGION,
    endpoint_url=DO_SPACES_ENDPOINT,
    aws_access_key_id=DO_SPACES_KEY,
    aws_secret_access_key=DO_SPACES_SECRET
)

motor_model = YOLO("yolov8s.pt")
plate_model = YOLO("best(x100).pt")
ocr_model   = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

timezone = ZoneInfo("Asia/Jakarta")
state = {
    "pit_log":            ["Empty"] * 5,
    "pit_time":           [None] * 5,
    "summary":            [],
    "log":                [],
    "simulation_running": True
}

last_heartbeat = {}

def log(msg):
    now = datetime.now(timezone)
    ts = now.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    state["log"].append(line)

def detect_motor_plate(path: str, save_crop=False, save_dir=None):
    log(f"[DETECT] Proses file: {path}")
    img = cv2.imread(path)
    if img is None:
        log("[ERROR] Gagal membaca gambar")
        return "error", None, None

    h_orig, w_orig, _ = img.shape
    img_resized = cv2.resize(img, (640, 640))
    motor_res = motor_model(img_resized, classes=[3], conf=0.2)[0]

    if motor_res.boxes:
        best_motor = max(motor_res.boxes, key=lambda b: float(b.conf[0]))
        x1m, y1m, x2m, y2m = map(int, best_motor.xyxy[0])
        scale_x = w_orig / 640
        scale_y = h_orig / 640
        x1o = int(x1m * scale_x)
        y1o = int(y1m * scale_y)
        x2o = int(x2m * scale_x)
        y2o = int(y2m * scale_y)
        dx = int(0.1 * (x2o - x1o))
        dy = int(0.1 * (y2o - y1o))
        x1c = max(x1o - dx, 0)
        y1c = max(y1o - dy, 0)
        x2c = min(x2o + dx, w_orig - 1)
        y2c = min(y2o + dy, h_orig - 1)
        roi_motor = img[y1c:y2c, x1c:x2c]
        if roi_motor.size == 0:
            return "motor", None, None
        roi_resized = cv2.resize(roi_motor, (640, 640))
        plate_res = plate_model(roi_resized, conf=0.2)[0]
        if not plate_res.boxes:
            # hanya motor tanpa plat
            if save_crop and save_dir:
                fname = f"motor_{os.path.basename(path)}"
                save_path = os.path.join(save_dir, fname)
                cv2.imwrite(save_path, roi_motor)
                return "motor", None, save_path
            return "motor", None, None

        best_plate = max(plate_res.boxes, key=lambda b: float(b.conf[0]))
        x1p, y1p, x2p, y2p = map(int, best_plate.xyxy[0])
        rh, rw, _ = roi_motor.shape
        scale_px = rw / 640
        scale_py = rh / 640
        abs_x1p = int(x1p * scale_px) + x1c
        abs_y1p = int(y1p * scale_py) + y1c
        abs_x2p = int(x2p * scale_px) + x1c
        abs_y2p = int(y2p * scale_py) + y1c
        crop_plate = img[abs_y1p:abs_y2p, abs_x1p:abs_x2p]
        if crop_plate.size == 0:
            return "motor", None, None

        ocr_res = ocr_model.ocr(crop_plate, cls=True)
        if ocr_res and ocr_res[0]:
            plate_text = ocr_res[0][0][1][0]
            if save_crop and save_dir:
                fname = f"plate_{os.path.basename(path)}"
                save_path = os.path.join(save_dir, fname)
                cv2.imwrite(save_path, crop_plate)
                return "plate", plate_text, save_path
            return "plate", plate_text, None
        else:
            return "motor", None, None

    else:
        # tidak ada motor, coba deteksi plat saja
        plate_res = plate_model(img_resized, conf=0.2)[0]
        if not plate_res.boxes:
            return "no_motor", None, None
        best_plate = max(plate_res.boxes, key=lambda b: float(b.conf[0]))
        x1p, y1p, x2p, y2p = map(int, best_plate.xyxy[0])
        scale_x = w_orig / 640
        scale_y = h_orig / 640
        x1 = int(x1p * scale_x)
        y1 = int(y1p * scale_y)
        x2 = int(x2p * scale_x)
        y2 = int(y2p * scale_y)
        crop_plate = img[y1:y2, x1:x2]
        if crop_plate.size == 0:
            return "no_motor", None, None
        ocr_res = ocr_model.ocr(crop_plate, cls=True)
        if ocr_res and ocr_res[0]:
            plate_text = ocr_res[0][0][1][0]
            if save_crop and save_dir:
                fname = f"plate_{os.path.basename(path)}"
                save_path = os.path.join(save_dir, fname)
                cv2.imwrite(save_path, crop_plate)
                return "plate", plate_text, save_path
            return "plate", plate_text, None
        else:
            return "no_motor", None, None

def process_folder(pit_idx: int):
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    folder_path = os.path.join(DOWNLOAD_DIR, today, FOLDERS[pit_idx])
    if not os.path.exists(folder_path):
        os.makedirs(folder_path, exist_ok=True)
        return  # Tidak ada file hari ini

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    for fn in files:
        full_path = os.path.join(folder_path, fn)
        status, plate_text, _ = detect_motor_plate(full_path)
        now = datetime.now(timezone)
        prev_state = state["pit_log"][pit_idx]
        if status == "plate":
            if prev_state == "Empty":
                state["pit_log"][pit_idx] = plate_text
                state["pit_time"][pit_idx] = now
                log(f"PIT{pit_idx+1} ⬅ Plat: {plate_text}")
            elif prev_state == "Motor":
                state["pit_log"][pit_idx] = plate_text
                log(f"PIT{pit_idx+1} Plat Dikenali: {plate_text}")
        elif status == "motor":
            if prev_state == "Empty":
                state["pit_log"][pit_idx] = "Motor"
                state["pit_time"][pit_idx] = now
                log(f"PIT{pit_idx+1} ⬅ Motor (tanpa plat)")
        elif status == "no_motor":
            if prev_state != "Empty":
                masuk = state["pit_time"][pit_idx]
                dur = (now - masuk).total_seconds() if masuk else 0
                h, rem = divmod(dur, 3600)
                m, s = divmod(rem, 60)
                state["summary"].append(
                    f"PIT{pit_idx+1} OUT: {prev_state} (Durasi: {int(h):02}:{int(m):02}:{int(s):02})"
                )
                state["pit_log"][pit_idx] = "Empty"
                state["pit_time"][pit_idx] = None
                log(f"PIT{pit_idx+1} ➡ Motor Keluar")
        os.remove(full_path)

def pit_worker(pit_idx: int):
    log(f"[THREAD] Worker PIT{pit_idx+1} dimulai")
    while True:
        if state["simulation_running"]:
            process_folder(pit_idx)
        time.sleep(2.0)
        
def heartbeat_monitor():
    while True:
        now = datetime.now(timezone)
        for pit_id, last_time in list(last_heartbeat.items()):
            delta = (now - last_time).total_seconds()
            if delta > 60:
                log(f"[OFFLINE] {pit_id} tidak aktif ")
        time.sleep(10)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def on_startup():
    log("[STARTUP] Memulai semua worker thread")
    for idx in range(len(FOLDERS)):
        t = threading.Thread(target=pit_worker, args=(idx,), daemon=True)
        t.start()
        
    threading.Thread(target=heartbeat_monitor, daemon=True).start()
  
@app.get("/")
def root():
    return HTMLResponse("<h3>ALPR Server Ready</h3>")

@app.head("/")
def head_root():
    return ""

@app.get("/state")
def get_state():
    now = datetime.now(timezone)
    status = []
    for i, p in enumerate(state["pit_log"]):
        if p != "Empty" and state["pit_time"][i]:
            d = int((now - state["pit_time"][i]).total_seconds())
            m, s = divmod(d, 60)
            status.append(f"{p} ({m:02}:{s:02})")
        else:
            status.append("Empty")
    return JSONResponse({
        "pit_log": status,
        "summary": state["summary"],
        "log": state["log"]
    })

@app.post("/heartbeat")
async def heartbeat(pit_id: str = Form(...)):
    last_heartbeat[pit_id] = datetime.now(timezone)
    log(f"[CAMERA] {pit_id} reconnecting {last_heartbeat[pit_id].strftime('%H:%M:%S')}")
    return {"status": "ok"}

@app.post("/upload")
async def upload_image(pit: int = 0, file: UploadFile = File(...)):
    try:
        ext = os.path.splitext(file.filename)[-1]
        filename = f"{uuid4().hex}{ext}"
        today = datetime.now(timezone).strftime("%Y-%m-%d")
        folder = os.path.join(today, FOLDERS[pit])
        local_path = os.path.join(DOWNLOAD_DIR, folder, filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Simpan file sementara ke lokal
        with open(local_path, "wb") as f:
            f.write(await file.read())

        # Jalankan deteksi
        status, plate_text, result_path = detect_motor_plate(
            local_path, save_crop=True, save_dir=os.path.dirname(local_path)
        )

        # Hapus file jika tidak ada motor/plat
        if status not in ["motor", "plate"]:
          #  log(f"[SKIP] Gambar dari PIT{pit+1} diabaikan karena tidak terdeteksi: {status}")
            os.remove(local_path)
            return JSONResponse({"status": "ignored", "reason": status})

        # Upload file hasil deteksi (bisa crop, bisa original)
        upload_path = result_path or local_path
        remote_path = f"{today}/{FOLDERS[pit]}/{os.path.basename(upload_path)}"

        with open(upload_path, "rb") as f:
            s3.upload_fileobj(
                f,
                DO_SPACES_BUCKET,
                remote_path,
                ExtraArgs={'ACL': 'private', 'ContentType': file.content_type}
            )

        log(f"[UPLOAD] File dari PIT{pit+1} tersimpan: {remote_path}")
        return JSONResponse({"status": "uploaded", "path": remote_path})

    except (BotoCoreError, ClientError) as e:
        log(f"[ERROR] Upload gagal: {e}")
        return JSONResponse({"error": "Upload failed", "details": str(e)}, status_code=500)
    except Exception as e:
        log(f"[ERROR] Upload internal error: {e}")
        return JSONResponse({"error": "Internal error", "details": str(e)}, status_code=500)
