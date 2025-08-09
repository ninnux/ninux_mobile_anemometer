#!/usr/bin/env python3
import asyncio
import serial
import pynmea2
import math
import csv
import os
import time
from datetime import datetime

from calypso_anemometer.core import CalypsoDeviceApi
from calypso_anemometer.model import CalypsoReading
from calypso_anemometer.util import wait_forever
import calypso_anemometer.exception

from bleak import BleakScanner, BleakClient

# ----------------- CONFIG -----------------
GPS_PORT = "/dev/serial0"     # regola se necessario
GPS_BAUDRATE = 9600

CALYPSO_NAME = "ULTRASONIC"   # o l'identificativo del tuo anemometro
CALYPSO_MAC =  "CD:BF:93:88:E2:68"    # se vuoi fissare il MAC, mettilo qui; altrimenti usa la scansione

WT901_NAME = "WT901BLE67"          # nome del modulo WT901
CHAR_NOTIFY = "0000ffe4-0000-1000-8000-00805f9a34fb"
CHAR_WRITE  = "0000ffe9-0000-1000-8000-00805f9a34fb"

CSV_FILE = "vento_compensato.csv"

# soglia minima distanza per calcolare bearing GPS (m)
GPS_MIN_DIST_M = 5.0

# ----------------- SHARED STATE -----------------
boat_speed_knots = 0.0
latitude = None
longitude = None
heading_gps = None

# WT901 state
latest_acc = {'x': 0.0, 'y': 0.0, 'z': 0.0}   # in g (approssimato)
latest_mag = {'x': 0.0, 'y': 0.0, 'z': 0.0}   # in uT
heading_mag = None

# ----------------- UTIL -----------------
def haversine_m(lat1, lon1, lat2, lon2):
    """Return distance in meters between two lat/lon points."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def bearing_between(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to 2 in degrees 0-360 (north-based)."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - \
        math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    brng = math.degrees(math.atan2(y, x))
    return (brng + 360) % 360

def ensured_csv_header(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "lat", "lon", "gps_speed_kn", "heading_gps",
                             "heading_mag", "shift_deg", "AWS_kn", "AWA_deg", "AWA_corr_deg", "TWS_kn", "TWA_deg"])

# ----------------- GPS Task -----------------
async def gps_reader():
    global boat_speed_knots, latitude, longitude, heading_gps
    prev_lat = None
    prev_lon = None
    prev_time = None
    try:
        ser = serial.Serial(GPS_PORT, GPS_BAUDRATE, timeout=1)
    except Exception as e:
        print(f"❌ Errore apertura seriale GPS: {e}")
        return

    print("📡 GPS reader avviato...")
    while True:
        try:
            line = ser.readline().decode('ascii', errors='ignore').strip()
        except Exception:
            await asyncio.sleep(0.2)
            continue
        if not line:
            await asyncio.sleep(0.05)
            continue

        if line.startswith("$GNRMC") or line.startswith("$GPRMC"):
            try:
                msg = pynmea2.parse(line)
            except pynmea2.ParseError:
                continue
            if getattr(msg, "status", None) != 'A':
                # no fix
                await asyncio.sleep(0.1)
                continue
            # aggiorna posizione e velocita (nodi)
            try:
                lat = msg.latitude
                lon = msg.longitude
                spd = float(msg.spd_over_grnd)  # nodi
            except Exception:
                await asyncio.sleep(0.1)
                continue

            # aggiorna globali
            boat_speed_knots = spd
            latitude = lat
            longitude = lon

            # calcola bearing tra precedenti se validi
            now = datetime.utcnow().timestamp()
            if prev_lat is not None and prev_lon is not None:
                dist = haversine_m(prev_lat, prev_lon, lat, lon)
                # calcola heading GPS se la distanza supera la soglia o la velocità è significativa
                if dist >= GPS_MIN_DIST_M or boat_speed_knots > 0.5:
                    heading_gps = bearing_between(prev_lat, prev_lon, lat, lon)
                    # aggiorna prev
                    prev_lat, prev_lon, prev_time = lat, lon, now
            else:
                prev_lat, prev_lon, prev_time = lat, lon, now

        await asyncio.sleep(0.05)

# ----------------- WT901 Task -----------------
def s16_from_bytes(lo, hi):
    raw = (hi << 8) | lo
    if raw & 0x8000:
        raw -= 0x10000
    return raw

def compensated_heading_from_acc_mag(ax, ay, az, mx, my, mz):
    # ax,ay,az in g; mx,my,mz in uT
    norm_a = math.sqrt(ax*ax + ay*ay + az*az)
    if norm_a == 0:
        return None
    axn = ax / norm_a
    ayn = ay / norm_a
    azn = az / norm_a
    # tilt angles
    pitch = math.asin(max(-1.0, min(1.0, -axn)))
    # avoid domain error for cos(pitch) ~ 0
    cos_pitch = math.cos(pitch)
    if abs(cos_pitch) < 1e-6:
        return None
    roll = math.asin(max(-1.0, min(1.0, ayn / cos_pitch)))
    # compensate
    mx_comp = mx * math.cos(pitch) + mz * math.sin(pitch)
    my_comp = mx * math.sin(roll) * math.sin(pitch) + my * math.cos(roll) - mz * math.sin(roll) * math.cos(pitch)
    heading = math.atan2(my_comp, mx_comp)
    return (math.degrees(heading) + 360) % 360

async def wt901_task():
    global latest_acc, latest_mag, heading_mag
    backoff = 2
    while True:
        # Cerca WT901
        print("🔍 Scansione BLE per WT901...")
        devices = await BleakScanner.discover(timeout=5.0)
        target = next((d for d in devices if d.name and WT901_NAME in d.name), None)
        if not target:
            print("❌ WT901 non trovato, riprovo dopo qualche secondo...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
            continue

        print(f"✅ Trovato WT901: {target.name} [{target.address}] - Connessione...")
        try:
            async with BleakClient(target.address) as client:
                # setup
                await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x69, 0x88, 0xB5]))
                await asyncio.sleep(0.1)
                await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x24, 0x00, 0x00]))
                await asyncio.sleep(0.1)
                # request acc+gyro+angle output (may or may not change device)
                await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x96, 0x00, 0x00]))
                await asyncio.sleep(0.1)
                await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x00, 0x00, 0x00]))
                await asyncio.sleep(0.1)

                # notification handler
                def handle(sender, data: bytes):
                    nonlocal client
                    # minimal validation
                    if len(data) < 11 or data[0] != 0x55:
                        return
                    ptype = data[1]
                    if ptype == 0x61:
                        # using mapping used previously (adapt if necessary)
                        # NOTE: user experiments showed ordering differences; keep same mapping
                        az = s16_from_bytes(data[2], data[3]) / 32768.0 * 16.0
                        ay = s16_from_bytes(data[4], data[5]) / 32768.0 * 16.0
                        ax = s16_from_bytes(data[6], data[7]) / 32768.0 * 16.0 * -1.0
                        latest_acc.update({'x': ax, 'y': ay, 'z': az})
                    elif ptype == 0x71:
                        # magnetometer: bytes layout observed: start at index 4..9 for Hx,Hy,Hz
                        # depends on single register read format 55 71 regL regH <8 registers...>
                        # many logs used [4:6],[6:8],[8:10]
                        try:
                            mx_raw = s16_from_bytes(data[4], data[5])
                            my_raw = s16_from_bytes(data[6], data[7])
                            mz_raw = s16_from_bytes(data[8], data[9])
                            mx = mx_raw / 150.0
                            my = my_raw / 150.0
                            mz = mz_raw / 150.0
                            latest_mag.update({'x': mx, 'y': my, 'z': mz})
                            # compute heading if acc present
                            h = compensated_heading_from_acc_mag(latest_acc['x'], latest_acc['y'], latest_acc['z'], mx, my, mz)
                            if h is not None:
                                nonlocal_heading = h
                                # update global
                                # assign to outer variable
                                globals_dict = globals()
                                globals_dict['heading_mag'] = h
                        except Exception:
                            pass

                await client.start_notify(CHAR_NOTIFY, handle)

                print("📡 WT901 notifications attive. Richiedo magnetometro periodicamente...")
                backoff = 2
                try:
                    while True:
                        # request magnetometer single read (FF AA 27 3A 00)
                        try:
                            await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x27, 0x3A, 0x00]))
                        except Exception:
                            # sometimes write fails; ignore and continue
                            pass
                        await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    await client.stop_notify(CHAR_NOTIFY)
                    raise
        except Exception as e:
            print(f"❌ Errore WT901 connection: {e}. Riprovando tra {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 30)

# ----------------- Calypso / Anemometer Task -----------------
def calcola_vento_reale(AWS, AWA_deg, BS):
    # AWS, BS in nodi, AWA in deg (0-360 relative to bow)
    # Convert AWA to radians, with x forward
    AWA_rad = math.radians(AWA_deg)
    AW_x = AWS * math.cos(AWA_rad)
    AW_y = AWS * math.sin(AWA_rad)
    TW_x = AW_x - BS
    TW_y = AW_y
    TWS = math.sqrt(TW_x**2 + TW_y**2)
    TWA_rad = math.atan2(TW_y, TW_x)
    TWA_deg = math.degrees(TWA_rad)
    if TWA_deg < 0:
        TWA_deg += 360
    return round(TWS, 2), round(TWA_deg, 2)

async def find_calypso_address(name=CALYPSO_NAME, timeout=6.0):
    print("🔍 Scansione BLE per Calypso...")
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and name in d.name:
            print(f"✅ Trovato Calypso {d.name} [{d.address}]")
            return d.address
    print("❌ Calypso non trovato nella scansione.")
    return None

async def calypso_subscribe(address):
    ensured_csv_header(CSV_FILE)

    def process_reading(reading: CalypsoReading):
        global boat_speed_knots, latitude, longitude, heading_gps, heading_mag
        now = datetime.utcnow()
        aws_kn = round(reading.wind_speed * 1.943844, 2)  # m/s -> kn
        awa = reading.wind_direction  # deg
        gps_spd = boat_speed_knots
        # compute shift only if both headings available
        shift = None
        awa_corr = awa
        if heading_mag is not None and heading_gps is not None:
            # shift as difference (mag - gps) as earlier discussed; use user's chosen convention
            shift = (heading_mag - heading_gps + 360.0) % 360.0
            awa_corr = (awa + shift) % 360.0
        # compute TWS/TWA using corrected AWA and GPS speed as boat speed
        TWS, TWA = calcola_vento_reale(aws_kn, awa_corr, gps_spd)
        # log
        ts = int(now.timestamp())
        lat = latitude if latitude is not None else ""
        lon = longitude if longitude is not None else ""
        h_gps = round(heading_gps,2) if heading_gps is not None else ""
        h_mag = round(heading_mag,2) if heading_mag is not None else ""
        shift_val = round(shift,2) if shift is not None else ""
        print(f"{ts},{lat},{lon},{gps_spd},{h_gps},{h_mag},{shift_val},{aws_kn},{awa},{round(awa_corr,2)},{TWS},{TWA}")
        with open(CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([ts, lat, lon, gps_spd, h_gps, h_mag, shift_val, aws_kn, awa, round(awa_corr,2), TWS, TWA])

    # open connection
    print(f"🔗 Connettendo a Calypso {address} ...")
    async with CalypsoDeviceApi(ble_address=address) as calypso:
        await calypso.subscribe_reading(process_reading)
        await wait_forever()

# ----------------- MAIN -----------------
async def main():
    # start gps and wt901 tasks
    gps_t = asyncio.create_task(gps_reader())
    wt_t = asyncio.create_task(wt901_task())
    # try to find calypso and subscribe (with retries)
    backoff = 2
    while True:
        try:
            address = CALYPSO_MAC or await find_calypso_address()
            if address is None:
                print(f"⏳ Non trovato, aspetto {backoff}s e riprovo...")
                await asyncio.sleep(backoff)
                backoff = min(backoff*2, 60)
                continue
            await calypso_subscribe(address)
            backoff = 2
        except (calypso_anemometer.exception.BluetoothConversationError,
                calypso_anemometer.exception.BluetoothTimeoutError) as e:
            print(f"❌ Errore Calypso: {e}. Riprovo in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 60)
        except Exception as e:
            print(f"❌ Errore in calypso_subscribe: {e}. Riprovo in {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff*2, 60)

if __name__ == "__main__":
    ensured_csv_header(CSV_FILE)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Interrotto dall'utente.")

