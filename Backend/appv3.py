import os
import time
import cv2
import json
import re
from datetime import datetime
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi import APIRouter, HTTPException
from typing import Optional
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
NUM_PIT = 5
state = {
    "pit_log": ["Empty"] * 5,
    "pit_time": [None] * 5,
    "summary": [],
    "log": [],
    "simulation_running": True,
    "no_motor_count": [0] * NUM_PIT,  # Tambahan counter
}
last_heartbeat = {}
ping_status = ["inactive"] * 5

# === Utilitas Logging ===
def log(msg):
    now = datetime.now(timezone)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")  # Tambahkan tanggal
    line = f"[{ts}] {msg}"
    print(line)
    state["log"].append(line)

def save_daily_log():
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{today}.json")

    per_pit_summary = [{"pit": f"PIT {i+1}", "data": []} for i in range(5)]

    # Simpan data motor keluar dari summary
    for entry in state["summary"]:
        if isinstance(entry, dict):  # format baru
            pit_num = int(entry["pit"].replace("PIT", "").strip()) - 1
            per_pit_summary[pit_num]["data"].append({
                "plate": entry["plate"],
                "masuk": entry["masuk"],
                "keluar": entry["keluar"],
                "duration": entry["duration"]
            })
        elif isinstance(entry, str) and "PIT" in entry and "OUT" in entry:
            # fallback parsing log lama
            try:
                pit_num = int(entry.split("OUT:")[0].replace("PIT", "").strip()) - 1
                plate_info = entry.split("OUT:")[1].strip()
                plate, dur_info = plate_info.split(" (Durasi: ")
                durasi = dur_info.replace(")", "")
                per_pit_summary[pit_num]["data"].append({
                    "plate": plate.strip(),
                    "masuk": "--:--:--",
                    "keluar": "--:--:--",
                    "duration": durasi
                })
            except Exception as e:
                log(f"[WARN] Gagal parsing log lama: {entry} | {e}")

    # Tambahkan kendaraan yang masih ada di PIT
    for i in range(5):
        if state["pit_log"][i] != "Empty":
            plate = state["pit_log"][i]
            masuk_time = state["pit_time"][i]
            masuk_str = masuk_time.strftime("%H:%M:%S") if masuk_time else "--:--:--"
            per_pit_summary[i]["data"].append({
                "plate": plate,
                "masuk": masuk_str,
                "keluar": "-",
                "duration": "-"
            })

    daily_data = {
        "pit_log": state["pit_log"],
        "ping_status": ping_status,
        "summary_count": count_summary(state["summary"]),
        "per_pit_summary": per_pit_summary
    }

    with open(log_path, "w") as f:
        json.dump(daily_data, f, indent=2)

def count_summary(summary_list):
    count_per_pit = {}
    for item in summary_list:
        pit = item["pit"]
        if pit not in count_per_pit:
            count_per_pit[pit] = 0
        count_per_pit[pit] += 1
    return [{"pit": pit, "total": total} for pit, total in count_per_pit.items()]


def upload_log_to_spaces():
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{today}.json")
    remote_path = f"datalog/{today}.json"
    with open(log_path, "rb") as f:
        s3.upload_fileobj(f, DO_SPACES_BUCKET, remote_path, ExtraArgs={'ACL': 'private'})

def reset_state_for_new_day():
    log("[RESET] Menyimpan log harian dan reset state untuk hari baru")
    save_daily_log()
    upload_log_to_spaces()

    # Reset state
    state["summary"] = []
    state["log"] = []
    state["pit_log"] = ["Empty"] * 5
    state["pit_time"] = [None] * 5

def daily_reset_scheduler():
    log("[SCHEDULER] Thread reset harian dimulai")
    while True:
        now = datetime.now(timezone)
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_midnight - now).total_seconds()
        time.sleep(wait_seconds)

        reset_state_for_new_day()

def sync_logs_from_spaces():
    log("[SYNC] Sinkronisasi awal log harian dari DigitalOcean Spaces")
    try:
        result = s3.list_objects_v2(Bucket=DO_SPACES_BUCKET, Prefix="datalog/")
        if "Contents" in result:
            for obj in result["Contents"]:
                key = obj["Key"]
                if key.endswith(".json"):
                    fname = os.path.basename(key)
                    local_path = os.path.join(LOG_DIR, fname)
                    if not os.path.exists(local_path):
                        with open(local_path, "wb") as f:
                            s3.download_fileobj(DO_SPACES_BUCKET, key, f)
                        log(f"[SYNC] Download log awal: {key}")
    except Exception as e:
        log(f"[ERROR] Gagal sync awal: {e}")

    
#===DETEKSI=====

def detect_motor_plate(path: str, save_crop=False, save_dir=None):
    log(f"[DETECT] Proses file: {path}")
    img = cv2.imread(path)
    if img is None:
        log("[ERROR] Gagal membaca gambar")
        return "error", None, None

    h_orig, w_orig, _ = img.shape
    img_resized = cv2.resize(img, (640, 640))
    scale_x = w_orig / 640
    scale_y = h_orig / 640

    motor_res = motor_model(img_resized, classes=[3], conf=0.3)[0]
    plate_detected = False
    plate_text = None
    plate_crop_path = None

    if motor_res.boxes:
        best_motor = max(motor_res.boxes, key=lambda b: float(b.conf[0]))
        x1m, y1m, x2m, y2m = map(int, best_motor.xyxy[0])

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
        plate_res = plate_model(roi_resized, conf=0.3)[0]

        if plate_res.boxes:
            best_plate = max(plate_res.boxes, key=lambda b: float(b.conf[0]))
            x1p, y1p, x2p, y2p = map(int, best_plate.xyxy[0])

            scale_px = (x2c - x1c) / 640
            scale_py = (y2c - y1c) / 640
            abs_x1p = int(x1p * scale_px) + x1c
            abs_y1p = int(y1p * scale_py) + y1c
            abs_x2p = int(x2p * scale_px) + x1c
            abs_y2p = int(y2p * scale_py) + y1c

            crop_plate = img[abs_y1p:abs_y2p, abs_x1p:abs_x2p]

            #  Filter ukuran dan rasio aspek
            plate_w = abs_x2p - abs_x1p
            plate_h = abs_y2p - abs_y1p
            aspect_ratio = plate_w / plate_h if plate_h > 0 else 0

            if (plate_w < w_orig * 0.5 and
                plate_h < h_orig * 0.25 and
                2 < aspect_ratio < 6 and
                crop_plate.size > 0):

                ocr_res = ocr_model.ocr(crop_plate, cls=True)
                if ocr_res and ocr_res[0]:
                    plate_text = ocr_res[0][0][1][0]
                    plate_detected = True
                    if save_crop and save_dir:
                        fname = f"plate_{os.path.basename(path)}"
                        plate_crop_path = os.path.join(save_dir, fname)
                        cv2.imwrite(plate_crop_path, crop_plate)

        if plate_detected:
            return "plate", plate_text, plate_crop_path
        else:
            if save_crop and save_dir:
                fname = f"motor_{os.path.basename(path)}"
                save_path = os.path.join(save_dir, fname)
                cv2.imwrite(save_path, roi_motor)
            return "motor", None, None

    else:
        # Tidak ada motor, coba cari plat langsung
        plate_res = plate_model(img_resized, conf=0.3)[0]
        if plate_res.boxes:
            best_plate = max(plate_res.boxes, key=lambda b: float(b.conf[0]))
            x1p, y1p, x2p, y2p = map(int, best_plate.xyxy[0])

            x1 = int(x1p * scale_x)
            y1 = int(y1p * scale_y)
            x2 = int(x2p * scale_x)
            y2 = int(y2p * scale_y)

            crop_plate = img[y1:y2, x1:x2]

            #  Filter juga di bagian ini
            plate_w = x2 - x1
            plate_h = y2 - y1
            aspect_ratio = plate_w / plate_h if plate_h > 0 else 0

            if (plate_w < w_orig * 0.5 and
                plate_h < h_orig * 0.25 and
                2 < aspect_ratio < 6 and
                crop_plate.size > 0):

                ocr_res = ocr_model.ocr(crop_plate, cls=True)
                if ocr_res and ocr_res[0]:
                    plate_text = ocr_res[0][0][1][0]
                    if save_crop and save_dir:
                        fname = f"plate_{os.path.basename(path)}"
                        plate_crop_path = os.path.join(save_dir, fname)
                        cv2.imwrite(plate_crop_path, crop_plate)
                    return "plate", plate_text, plate_crop_path

        return "no_motor", None, None
        
def process_folder(pit_idx: int):
    today = datetime.now(timezone).strftime("%Y-%m-%d")
    folder_path = os.path.join(DOWNLOAD_DIR, today, FOLDERS[pit_idx])
    if not os.path.exists(folder_path):
        os.makedirs(folder_path, exist_ok=True)
        return

    files = sorted([
        f for f in os.listdir(folder_path)
        if f.lower().endswith(('.jpg', '.jpeg', '.png'))
        # Skip file yang sudah punya prefix agar tidak dobel
        and not f.startswith("motor_")
        and not f.startswith("no_motor_")
    ])

    for fn in files:
        full_path = os.path.join(folder_path, fn)
        done_marker = full_path + ".done"
        if os.path.exists(done_marker):
            continue  # Lewati file yang sudah diproses sebelumnya

        status, plate_text, _ = detect_motor_plate(full_path)
        now = datetime.now(timezone)
        prev_state = state["pit_log"][pit_idx]

        timestamp_str = now.strftime("%H%M%S")  # untuk nama unik

        if status == "plate":
            state["no_motor_count"][pit_idx] = 0
            if prev_state == "Empty":
                state["pit_log"][pit_idx] = plate_text
                state["pit_time"][pit_idx] = now
                log(f"PIT{pit_idx+1} ⬅ Plat: {plate_text}")
            elif prev_state == "Motor":
                state["pit_log"][pit_idx] = plate_text
                log(f"PIT{pit_idx+1} Plat Dikenali: {plate_text}")

        elif status == "motor":
            state["no_motor_count"][pit_idx] = 0
            if prev_state == "Empty":
                state["pit_log"][pit_idx] = "Motor"
                state["pit_time"][pit_idx] = now
                log(f"PIT{pit_idx+1} ⬅ Motor (tanpa plat)")
            elif prev_state not in ["Empty", "Motor"]:
                log(f"PIT{pit_idx+1} Plat masih dipertahankan: {prev_state}")

        elif status == "no_motor":
            state["no_motor_count"][pit_idx] += 1
            if prev_state == "Empty":
                log(f"PIT{pit_idx+1} tetap kosong (no_motor)")
            else:
                log(f"PIT{pit_idx+1} Tidak terdeteksi motor, hitung={state['no_motor_count'][pit_idx]}")

            if state["no_motor_count"][pit_idx] >= 3:
                masuk = state["pit_time"][pit_idx]
                dur = (now - masuk).total_seconds() if masuk else 0
                h, rem = divmod(dur, 3600)
                m, s = divmod(rem, 60)
                if dur >= 120:
                    state["summary"].append({
                        "pit": f"PIT {pit_idx+1}",
                        "plate": prev_state,
                        "masuk": masuk.strftime("%H:%M:%S") if masuk else "--:--:--",
                        "keluar": now.strftime("%H:%M:%S"),
                        "duration": f"{int(h):02}:{int(m):02}:{int(s):02}"
                    })
                    log(f"PIT{pit_idx+1} ➡ Motor Keluar (valid, durasi: {int(dur)} detik)")
                else:
                    log(f"PIT{pit_idx+1} keluar terlalu cepat (<2 menit), durasi: {int(dur)} detik")

                state["pit_log"][pit_idx] = "Empty"
                state["pit_time"][pit_idx] = None
                state["no_motor_count"][pit_idx] = 0

                save_daily_log()
                upload_log_to_spaces()

            # Upload juga gambar no_motor dengan nama unik
            remote_path = f"{today}/{FOLDERS[pit_idx]}/no_motor_{timestamp_str}.jpg"
            try:
                with open(full_path, "rb") as f:
                    s3.upload_fileobj(
                        f,
                        DO_SPACES_BUCKET,
                        remote_path,
                        ExtraArgs={'ACL': 'private', 'ContentType': 'image/jpeg'}
                    )
                log(f"[UPLOAD] File no_motor dari PIT{pit_idx+1} tersimpan: {remote_path}")
            except Exception as e:
                log(f"[ERROR] Upload file no_motor gagal: {e}")

        # Buat penanda file sudah diproses
        try:
            with open(done_marker, "w") as f:
                f.write("done")
        except Exception as e:
            log(f"[ERROR] Gagal membuat file marker: {done_marker}, error: {e}")

        # Hapus file lokal setelah diproses
        try:
            os.remove(full_path)
        except FileNotFoundError:
            pass
             
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
    result = [{"pit": f"PIT {i+1}", "data": []} for i in range(5)]

    # Tambahkan motor yang sudah keluar dari summary
    for entry in state["summary"]:
        pit_num = int(entry["pit"].replace("PIT", "").strip()) - 1
        result[pit_num]["data"].append({
            "plate": entry["plate"],
            "masuk": entry["masuk"],
            "keluar": entry["keluar"],
            "duration": entry["duration"]
        })

    # Tambahkan motor yang masih ada di PIT
    for i in range(5):
        if state["pit_log"][i] != "Empty":
            plate = state["pit_log"][i]
            masuk_time = state["pit_time"][i]
            masuk_str = masuk_time.strftime("%H:%M:%S") if masuk_time else "--:--:--"
            result[i]["data"].append({
                "plate": plate,
                "masuk": masuk_str,
                "keluar": "-",
                "duration": "-"
            })

    return result

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
 
@app.on_event("startup")
def on_startup():
    log("[STARTUP] Memulai semua worker thread")
    sync_logs_from_spaces()
    for idx in range(len(FOLDERS)):
        t = threading.Thread(target=pit_worker, args=(idx,), daemon=True)
        t.start()
        
    threading.Thread(target=heartbeat_monitor, daemon=True).start()
    threading.Thread(target=daily_reset_scheduler, daemon=True).start() 
  
@app.get("/")
def root():
    return HTMLResponse("<h3>ALPR Server Ready</h3>")

@app.head("/")
def head_root():
    return "" 

@app.get("/log_dates")
def list_log_dates():
    dates = set()

    # Hari ini (agar bisa di-filter)
    today_str = datetime.now(timezone).strftime("%Y-%m-%d")

    # Ambil daftar dari Spaces
    try:
        result = s3.list_objects_v2(Bucket=DO_SPACES_BUCKET, Prefix="datalog/")
        if "Contents" in result:
            for obj in result["Contents"]:
                key = obj["Key"]
                if key.endswith(".json"):
                    fname = os.path.basename(key)
                    date_str = fname.replace(".json", "")
                    if date_str != today_str:  # ➤ FILTER HARI INI
                        dates.add(date_str)

                        # Sync ke lokal jika belum ada
                        local_path = os.path.join(LOG_DIR, fname)
                        if not os.path.exists(local_path):
                            with open(local_path, "wb") as f:
                                s3.download_fileobj(DO_SPACES_BUCKET, key, f)
                            log(f"[SYNC] Download log: {key}")
    except Exception as e:
        log(f"[ERROR] Gagal ambil daftar log dari Spaces: {e}")

    # Gabungkan dengan file lokal tambahan (jika ada)
    for file in os.listdir(LOG_DIR):
        if file.endswith(".json"):
            date_str = file.replace(".json", "")
            if date_str != today_str:  # ➤ FILTER HARI INI
                dates.add(date_str)

    return JSONResponse({"dates": sorted(dates, reverse=True)})

@app.get("/state")
def get_state(request: Request):
    date_str = request.query_params.get("date")
    if date_str:
        log_path = os.path.join(LOG_DIR, f"{date_str}.json")

        # Jika file log tidak ada secara lokal, coba unduh dari DigitalOcean Spaces
        if not os.path.exists(log_path):
            remote_path = f"datalog/{date_str}.json"
            try:
                with open(log_path, "wb") as f:
                    s3.download_fileobj(DO_SPACES_BUCKET, remote_path, f)
                log(f"[DOWNLOAD] Log tanggal {date_str} diunduh dari Spaces")
            except Exception as e:
                log(f"[ERROR] Gagal unduh log dari Spaces: {e}")
                return JSONResponse({"error": "Log not found (lokal & cloud)"}, status_code=404)

        # Setelah berhasil, baca file log-nya
        try:
            with open(log_path) as f:
                data = json.load(f)
            return JSONResponse(data)
        except Exception as e:
            log(f"[ERROR] Gagal baca file JSON: {e}")
            return JSONResponse({"error": "Gagal membaca log"}, status_code=500)

    # Realtime mode
    now = datetime.now(timezone)
    status = []
    for i, p in enumerate(state["pit_log"]):
        if p != "Empty" and state["pit_time"][i]:
            d = int((now - state["pit_time"][i]).total_seconds())
            h, rem = divmod(d, 3600)
            m, s = divmod(rem, 60)
            status.append(f"{p} ({h:02}:{m:02}:{s:02})")
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
    match = re.search(r'\d+', pit_id)
    if not match:
        return JSONResponse({"error": "Invalid PIT ID"}, status_code=400)
    pit_num = int(match.group()) - 1
    if isinstance(ping_status, list):
        ping_status[pit_num] = "active"
    else:
        ping_status[pit_id] = "active"
    log(f"[STATUS] {pit_id} reconnecting")
    save_daily_log()
    upload_log_to_spaces()
    return {"status": "ok"}
    
@app.post("/upload")
async def upload_image(pit: int = 0, file: UploadFile = File(...)):
    try:
        now = datetime.now(timezone)
        today = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S-%f")

        folder = os.path.join(today, FOLDERS[pit])
        ext = os.path.splitext(file.filename)[-1].lower()
        ext = ext if ext in [".jpg", ".jpeg", ".png"] else ".jpg"  # fallback
        filename = f"motor_{time_str}{ext}"
        local_path = os.path.join(DOWNLOAD_DIR, folder, filename)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        # Simpan file lokal
        with open(local_path, "wb") as f:
            f.write(await file.read())

        # Deteksi motor dan plat
        status, plate_text, result_path = detect_motor_plate(
            local_path, save_crop=True, save_dir=os.path.dirname(local_path)
        )

        prev_state = state["pit_log"][pit]

        if status == "plate":
            state["no_motor_count"][pit] = 0
            if prev_state == "Empty":
                state["pit_log"][pit] = plate_text
                state["pit_time"][pit] = now
                log(f"PIT{pit+1} ⬅ Plat: {plate_text}")
            elif prev_state == "Motor":
                state["pit_log"][pit] = plate_text
                log(f"PIT{pit+1} Plat Dikenali: {plate_text}")

        elif status == "motor":
            state["no_motor_count"][pit] = 0
            if prev_state == "Empty":
                state["pit_log"][pit] = "Motor"
                state["pit_time"][pit] = now
                log(f"PIT{pit+1} ⬅ Motor (tanpa plat)")
            elif prev_state not in ["Empty", "Motor"]:
                log(f"PIT{pit+1} Plat masih dipertahankan: {prev_state}")

        elif status == "no_motor":
            state["no_motor_count"][pit] += 1
            if prev_state == "Empty":
                log(f"PIT{pit+1} tetap kosong (no_motor)")
            else:
                log(f"PIT{pit+1} Tidak terdeteksi motor, hitung={state['no_motor_count'][pit]}")

            if state["no_motor_count"][pit] >= 3:
                masuk = state["pit_time"][pit]
                dur = (now - masuk).total_seconds() if masuk else 0
                h, rem = divmod(dur, 3600)
                m, s = divmod(rem, 60)

                if dur >= 120:
                    state["summary"].append({
                        "pit": f"PIT {pit+1}",
                        "plate": prev_state,
                        "masuk": masuk.strftime("%H:%M:%S") if masuk else "--:--:--",
                        "keluar": now.strftime("%H:%M:%S"),
                        "duration": f"{int(h):02}:{int(m):02}:{int(s):02}"
                    })
                    log(f"PIT{pit+1} ➡ Motor Keluar (valid, durasi: {int(dur)} detik)")
                else:
                    log(f"PIT{pit+1} keluar terlalu cepat (<2 menit), durasi: {int(dur)} detik")

                state["pit_log"][pit] = "Empty"
                state["pit_time"][pit] = None
                state["no_motor_count"][pit] = 0

                save_daily_log()
                upload_log_to_spaces()

            # Upload file no_motor
            remote_path = f"{today}/{FOLDERS[pit]}/no_motor_{filename}"
            try:
                with open(local_path, "rb") as f:
                    s3.upload_fileobj(
                        f, DO_SPACES_BUCKET, remote_path,
                        ExtraArgs={'ACL': 'private', 'ContentType': file.content_type}
                    )
                log(f"[UPLOAD] File no_motor dari PIT{pit+1} tersimpan: {remote_path}")
            except Exception as e:
                log(f"[ERROR] Upload file no_motor gagal: {e}")
                return JSONResponse({"error": "Upload failed", "details": str(e)}, status_code=500)

            os.remove(local_path)
            return JSONResponse({"status": "uploaded_no_motor", "path": remote_path, "reason": "no_motor"})

        # Upload file hasil deteksi
        upload_path = result_path or local_path
        remote_path = f"{today}/{FOLDERS[pit]}/{os.path.basename(upload_path)}"
        with open(upload_path, "rb") as f:
            s3.upload_fileobj(
                f, DO_SPACES_BUCKET, remote_path,
                ExtraArgs={'ACL': 'private', 'ContentType': file.content_type}
            )

        if result_path and os.path.exists(result_path):
            os.remove(result_path)
        if upload_path != local_path and os.path.exists(local_path):
            os.remove(local_path)

        log(f"[UPLOAD] File dari PIT{pit+1} tersimpan: {remote_path}")
        return JSONResponse({"status": "uploaded", "path": remote_path})

    except (BotoCoreError, ClientError) as e:
        log(f"[ERROR] Upload gagal: {e}")
        return JSONResponse({"error": "Upload failed", "details": str(e)}, status_code=500)
    except Exception as e:
        log(f"[ERROR] Upload internal error: {e}")
        return JSONResponse({"error": "Internal error", "details": str(e)}, status_code=500)
        
@app.get("/latest_image")
def get_latest_image(pit: str, date: Optional[str] = None):
    import boto3
    from urllib.parse import quote
    from datetime import datetime, timedelta

    BUCKET_NAME = "pitmonitoring"
    REGION = "sgp1"
    session = boto3.session.Session()
    s3_client = session.client(
        's3',
        region_name=REGION,
        endpoint_url="https://sgp1.digitaloceanspaces.com",
        aws_access_key_id=DO_SPACES_KEY,
        aws_secret_access_key=DO_SPACES_SECRET
    )

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    prefix = f"{date}/{pit}/"

    try:
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
        if "Contents" not in response or len(response["Contents"]) == 0:
            raise HTTPException(status_code=404, detail="No images found.")

        latest_obj = max(response["Contents"], key=lambda obj: obj["LastModified"])
        signed_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': latest_obj['Key']},
            ExpiresIn=60  # URL berlaku 1 menit
        )
        return {"url": signed_url, "key": latest_obj['Key']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
