import os
import time
import cv2
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from ultralytics import YOLO
from paddleocr import PaddleOCR
import threading
from uuid import uuid4
import boto3
from botocore.exceptions import BotoCoreError, ClientError

# === Konstanta dan Konfigurasi ===
DOWNLOAD_DIR = "uploads"
FOLDERS = [f"P{i+1}" for i in range(5)]
DO_SPACES_REGION = "sgp1"
DO_SPACES_ENDPOINT = "https://sgp1.digitaloceanspaces.com"
DO_SPACES_KEY = "DO801UTAA8KY7NAHHRC8"
DO_SPACES_SECRET = "Hos62RJQmYVkARvJmk96xPXMG04p58SK5q/WTlzpycE"
DO_SPACES_BUCKET = "pitmonitoring"
LOG_DIR = "datalog"

timezone = ZoneInfo("Asia/Jakarta")

# === Inisialisasi Folder ===
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
for folder in FOLDERS:
    os.makedirs(os.path.join(DOWNLOAD_DIR, folder), exist_ok=True)

# === Inisialisasi Model dan Storage ===
s3 = boto3.client(
    's3',
    region_name=DO_SPACES_REGION,
    endpoint_url=DO_SPACES_ENDPOINT,
    aws_access_key_id=DO_SPACES_KEY,
    aws_secret_access_key=DO_SPACES_SECRET
)

motor_model = YOLO("yolov8s.pt")
plate_model = YOLO("best(x100).pt")
ocr_model = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)

# === State Aplikasi ===
state = {
    "pit_log": ["Empty"] * 5,
    "pit_time": [None] * 5,
    "summary": [],
    "log": [],
    "simulation_running": True
}
last_heartbeat = {}
ping_status = ["inactive"] * 5

# === Utilitas Logging ===
def log(msg):
    now = datetime.now(timezone)
    ts = now.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    state["log"].append(line)

def save_daily_log():
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{today}.json")

    per_pit_summary = [{"pit": f"PIT {i+1}", "data": []} for i in range(5)]

    for log_entry in state["summary"]:
        if "PIT" in log_entry and "OUT" in log_entry:
            pit_num = int(log_entry.split("OUT:")[0].replace("PIT", "").strip()) - 1
            plate_info = log_entry.split("OUT:")[1].strip()
            plate, dur_info = plate_info.split(" (Durasi: ")
            durasi = dur_info.replace(")", "")
            now = datetime.now(timezone)
            masuk_time = state["pit_time"][pit_num].strftime("%H:%M:%S") if state["pit_time"][pit_num] else "--:--:--"
            per_pit_summary[pit_num]["data"].append({
                "plate": plate.strip(),
                "masuk": masuk_time,
                "keluar": now.strftime("%H:%M:%S"),
                "duration": durasi
            })

    daily_data = {
        "pit_log": state["pit_log"],
        "ping_status": ping_status,
        "summary_count": count_summary(state["summary"]),
        "per_pit_summary": per_pit_summary,
        "log": state["log"][-100:]
    }

    with open(log_path, "w") as f:
        json.dump(daily_data, f, indent=2)


def upload_log_to_spaces():
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{today}.json")
    remote_path = f"datalog/{today}.json"
    with open(log_path, "rb") as f:
        s3.upload_fileobj(f, DO_SPACES_BUCKET, remote_path, ExtraArgs={'ACL': 'private'})

def count_summary(summary_list):
    count = {}
    for entry in summary_list:
        if "PIT" in entry and "OUT" in entry:
            pit = entry.split()[0]
            count[pit] = count.get(pit, 0) + 1
    return [{"pit": k, "total": v} for k, v in count.items()]
    
#===DETEKSI=====
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
        for pit_index in range(5):
            pit_id = f"PIT{pit_index + 1}"
            last_time = last_heartbeat.get(pit_id)
            if last_time:
                delta = (now - last_time).total_seconds()
                if delta > 60:
                    ping_status[pit_index] = "inactive"
                    log(f"[OFFLINE] {pit_id} tidak aktif ")
                else:
                    ping_status[pit_index] = "active"
            else:
                ping_status[pit_index] = "inactive"
        time.sleep(10)
        
def generate_full_pit_summary():
    result = []
    for i in range(5):
        if state["pit_log"][i] != "Empty":
            masuk_time = state["pit_time"][i]
            dur = int((datetime.now(timezone) - masuk_time).total_seconds()) if masuk_time else 0
            h, rem = divmod(dur, 3600)
            m, s = divmod(rem, 60)
            result.append({
                "pit": f"PIT {i+1}",
                "data": [{
                    "plate": state["pit_log"][i],
                    "masuk": masuk_time.strftime("%H:%M:%S") if masuk_time else "--:--:--",
                    "keluar": "-",
                    "duration": f"{h:02}:{m:02}:{s:02}"
                }]
            })
        else:
            result.append({"pit": f"PIT {i+1}", "data": []})
    return result

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

@app.get("/log_dates")
def list_log_dates():
    dates = []
    for file in os.listdir(LOG_DIR):
        if file.endswith(".json"):
            dates.append(file.replace(".json", ""))
    dates.sort(reverse=True)
    return JSONResponse({"dates": dates})

@app.get("/state")
def get_state(request: Request):
    date_str = request.query_params.get("date")
    if date_str:
        log_path = os.path.join(LOG_DIR, f"{date_str}.json")
        if os.path.exists(log_path):
            with open(log_path) as f:
                data = json.load(f)
            return JSONResponse(data)
        return JSONResponse({"error": "Log not found"}, status_code=404)

    now = datetime.now(timezone)
    status = []
    for i, p in enumerate(state["pit_log"]):
        if p != "Empty" and state["pit_time"][i]:
            d = int((now - state["pit_time"][i]).total_seconds())
            m, s = divmod(d, 60)
            status.append(f"{p} ({m:02}:{s:02})")
        else:
            status.append("Empty")

    ping = [ping_status[i] if isinstance(ping_status, list) else ping_status.get(f"PIT {i+1}", "inactive") for i in range(5)]

    return JSONResponse({
        "pit_log": status,
        "ping_status": ping,
        "summary_count": count_summary(state["summary"]),
        "per_pit_summary": generate_full_pit_summary(),
        "log": state["log"][-100:]
    })

@app.post("/heartbeat")
async def heartbeat(pit_id: str = Form(...)):
    last_heartbeat[pit_id] = datetime.now(timezone)
    if isinstance(ping_status, list):
        pit_num = int(pit_id.split()[-1]) - 1
        ping_status[pit_num] = "active"
    else:
        ping_status[pit_id] = "active"
    log(f"[STATUS] {pit_id} reconnecting {last_heartbeat[pit_id].strftime('%H:%M:%S')}")
    save_daily_log()
    upload_log_to_spaces()
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

        now = datetime.now(timezone)
        prev_state = state["pit_log"][pit]

        if status == "plate":
            if prev_state == "Empty":
                state["pit_log"][pit] = plate_text
                state["pit_time"][pit] = now
                log(f"PIT{pit+1} ⬅ Plat: {plate_text}")
            elif prev_state == "Motor":
                state["pit_log"][pit] = plate_text
                log(f"PIT{pit+1} Plat Dikenali: {plate_text}")
        elif status == "motor":
            if prev_state == "Empty":
                state["pit_log"][pit] = "Motor"
                state["pit_time"][pit] = now
                log(f"PIT{pit+1} ⬅ Motor (tanpa plat)")
        elif status == "no_motor":
            if prev_state != "Empty":
                masuk = state["pit_time"][pit]
                dur = (now - masuk).total_seconds() if masuk else 0
                h, rem = divmod(dur, 3600)
                m, s = divmod(rem, 60)
                state["summary"].append(
                    f"PIT{pit+1} OUT: {prev_state} (Durasi: {int(h):02}:{int(m):02}:{int(s):02})"
                )
                state["pit_log"][pit] = "Empty"
                state["pit_time"][pit] = None
                log(f"PIT{pit+1} ➡ Motor Keluar")

                # Simpan log jika terjadi keluar
                save_daily_log()
                upload_log_to_spaces()

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

