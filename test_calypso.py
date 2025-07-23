
#import asyncio
#from calypso_anemometer.core import CalypsoDeviceApi
#
#async def main():
#    async with CalypsoDeviceApi(ble_address="8B5E78C8-2AB2-392C-EC12-BB159DF87E70") as calypso:
#        reading = await calypso.get_reading()
#        reading.dump()
#        #print(reading)
#
#if __name__ == "__main__":
#    asyncio.run(main())


import asyncio

from calypso_anemometer.core import CalypsoDeviceApi
from calypso_anemometer.model import CalypsoReading
from calypso_anemometer.util import wait_forever
import calypso_anemometer.exception
from datetime import datetime

import math
import csv

def calcola_vento_reale(AWS, AWA_deg, BS):
    """
    Calcola TWS (True Wind Speed) e TWA (True Wind Angle) a partire da:
    - AWS: Apparent Wind Speed (nodi)
    - AWA: Apparent Wind Angle (gradi da prua, positivo a dritta)
    - BS: Boat Speed (nodi)
    """

    # Converti angolo in radianti
    AWA_rad = math.radians(AWA_deg)

    # Componenti del vento apparente (sistema con x = prua della barca)
    AW_x = AWS * math.cos(AWA_rad)  # avanti
    AW_y = AWS * math.sin(AWA_rad)  # laterale

    # Sottrai velocità della barca
    TW_x = AW_x - BS
    TW_y = AW_y

    # Calcola intensità del vento reale
    TWS = math.sqrt(TW_x**2 + TW_y**2)

    # Calcola angolo del vento reale (radians → degrees)
    TWA_rad = math.atan2(TW_y, TW_x)
    TWA_deg = math.degrees(TWA_rad)

    # Normalizza l'angolo (0–360°) relativo alla prua
    if TWA_deg < 0:
        TWA_deg += 360

    return round(TWS, 2), round(TWA_deg, 2)


async def calypso_subscribe_demo():
    def process_reading(reading: CalypsoReading):
        #reading.dump()
        now = datetime.now()
        kn=round(reading.wind_speed*1.943844,2)
        awa=reading.wind_direction
        bs=2 #GPS speed
        TWS,TWA=calcola_vento_reale(kn,awa,bs)
        print(str(int(now.timestamp()))+","+str(kn)+","+str(awa)+","+str(TWS)+","+str(TWA))
        #print(str(int(now.timestamp()))+","+str(reading.wind_speed)+","+str(reading.wind_direction))
        file=open("vento.csv", "a", newline="")
        writer = csv.writer(file)
        writer.writerow([int(now.timestamp()),kn,awa,TWS,TWA,bs])

    async with CalypsoDeviceApi(ble_address="8B5E78C8-2AB2-392C-EC12-BB159DF87E70") as calypso:
        await calypso.subscribe_reading(process_reading)
        await wait_forever()


if __name__ == "__main__":  # pragma: nocover
   while True:
     try:
       asyncio.run(calypso_subscribe_demo())
     except calypso_anemometer.exception.BluetoothConversationError:
       print("fallito!... riprovo")
       continue 
