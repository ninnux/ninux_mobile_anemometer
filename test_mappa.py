import folium
import csv

# Inizializza mappa centrata sul primo punto
lat0, lon0 = 45.4640, 9.1900
m = folium.Map(location=[lat0, lon0], zoom_start=15)

# Leggi dati dal CSV
punti = []
with open("traccia.csv", newline='', encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        lat = float(row["lat"])
        lon = float(row["lon"])
        vento = row["vento_kn"]
        timestamp = row["timestamp"]

        # Aggiungi punto alla lista per la spezzata
        punti.append([lat, lon])

        # Aggiungi un marcatore con popup
        popup = f"<b>{timestamp}</b><br>Vento: {vento} kn"
        folium.Marker(location=[lat, lon], popup=popup).add_to(m)

# Disegna la spezzata (linea)
folium.PolyLine(punti, color="blue", weight=3, opacity=0.8).add_to(m)

# Salva la mappa
m.save("mappa_spezzata.html")
print("✅ Mappa salvata come 'mappa_spezzata.html'")

