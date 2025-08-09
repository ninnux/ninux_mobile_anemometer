import asyncio
import math
from bleak import BleakScanner, BleakClient

CHAR_NOTIFY = "0000ffe4-0000-1000-8000-00805f9a34fb"
CHAR_WRITE  = "0000ffe9-0000-1000-8000-00805f9a34fb"
TARGET_NAME = "WT901BLE67"

# Ultimi valori disponibili
latest_acc = {'x': 0.0, 'y': 0.0, 'z': 0.0}
latest_mag = {'x': 0.0, 'y': 0.0, 'z': 0.0}

# Calcolo heading compensato per l'inclinazione (tilt compensation)
def compensated_heading(ax, ay, az, mx, my, mz):
    norm_a = math.sqrt(ax ** 2 + ay ** 2 + az ** 2)
    if norm_a == 0:
        return None
    ax /= norm_a
    ay /= norm_a
    az /= norm_a

    pitch = math.asin(-ax)
    roll = math.asin(ay / math.cos(pitch))

    mx_comp = mx * math.cos(pitch) + mz * math.sin(pitch)
    my_comp = mx * math.sin(roll) * math.sin(pitch) + my * math.cos(roll) - mz * math.sin(roll) * math.cos(pitch)

    heading = math.atan2(my_comp, mx_comp)
    return (math.degrees(heading) + 360) % 360


def parse_packet(sender, data: bytes):
    if len(data) < 20 or data[0] != 0x55:
        return

    packet_type = data[1]

    def s16(lo, hi):
        return int.from_bytes([lo, hi], byteorder='little', signed=True)
        #return int.from_bytes([lo, hi], byteorder='big', signed=False)
        #return ((hi<<8)|lo)

    if packet_type == 0x61:
        az = s16(data[2], data[3]) / 32768 * 16
        ay = s16(data[4], data[5]) / 32768 * 16
        ax = s16(data[6], data[7]) / 32768 * 16 * -1
        latest_acc.update({'x': ax, 'y': ay, 'z': az})

    elif packet_type == 0x71:
        mx = s16(data[4], data[5]) / 150.0
        my = s16(data[6], data[7]) / 150.0
        mz = s16(data[8], data[9]) / 150.0

        latest_mag.update({'x': mx, 'y': my, 'z': mz})

#        print("\n🧲 Magnetometro [µT]:")
#        print(f"🧾 Pacchetto ricevuto: {data.hex(' ')}")
#        print(f"  x={mx:.2f}, y={my:.2f}, z={mz:.2f}")
#        print("\n Accellerometro:")
#        print(f"  x={latest_acc['x']:.2f}, y={latest_acc['y']:.2f}, z={latest_acc['z']:.2f}")

        heading = compensated_heading(
            latest_acc['x'], latest_acc['y'], latest_acc['z'],
            latest_mag['x'], latest_mag['y'], latest_mag['z']
        )

        if heading is not None:
            print(f"🧭 Heading compensato (accurato): {heading:.2f} °")

async def main():
    print("🔍 Scansione BLE...")
    devices = await BleakScanner.discover()
    target = next((d for d in devices if TARGET_NAME in (d.name or "")), None)
    if not target:
        print("❌ Dispositivo non trovato.")
        return

    async with BleakClient(target.address) as client:
        print(f"✅ Connesso a {target.name}")

        # 🔓 Sblocco modifiche
        await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x69, 0x88, 0xB5]))
        await asyncio.sleep(0.1)

        ## calibrazione accellerometro
        #await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x01, 0x01, 0x00]))
        #await asyncio.sleep(0.1)

        # ⚙️ Algoritmo 9 assi
        await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x24, 0x00, 0x00]))
        await asyncio.sleep(0.1)

        # 📤 Output: displacement + velocity + angle (AGPVSEL=1)
        # 📤 Output: acceleration + angular velocity + angle (AGPVSEL=0)
        await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x96, 0x00, 0x00]))
        await asyncio.sleep(0.1)

        # 💾 Salva configurazione
        await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x00, 0x00, 0x00]))
        await asyncio.sleep(0.1)

        await client.start_notify(CHAR_NOTIFY, parse_packet)

        print("📡 Lettura dati in corso (CTRL+C per uscire)...")
        try:
            while True:
                await client.write_gatt_char(CHAR_WRITE, bytearray([0xFF, 0xAA, 0x27, 0x3A, 0x00]))
                await asyncio.sleep(0.5)
        except KeyboardInterrupt:
            print("🛑 Interrotto.")
        finally:
            await client.stop_notify(CHAR_NOTIFY)

asyncio.run(main())
