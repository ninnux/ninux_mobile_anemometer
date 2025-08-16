import folium
import csv
import math
from folium.features import DivIcon

CSV_FILE = "vento_compensato_last.csv"
OUT_HTML = "mappa_traccia.html"

# Colonne attese nel CSV
REQUIRED_FIELDS = [
    "timestamp","lat","lon","gps_speed_kn","heading_gps","heading_mag","shift_deg",
    "AWS_kn","AWA_deg","AWA_corr_deg","TWS_kn","TWA_deg"
]

def parse_required_float(row, field):
    """Legge e valida un float richiesto; fallisce se mancante/vuoto/NaN."""
    if field not in row:
        raise ValueError(f"Colonna mancante nel CSV: {field}")
    v = (row[field] or "").strip()
    if v == "" or v.lower() == "nan":
        raise ValueError(f"Valore mancante o NaN per campo richiesto: {field}")
    try:
        x = float(v)
    except ValueError:
        raise ValueError(f"Valore non numerico per {field}: {v}")
    if math.isnan(x):
        raise ValueError(f"Valore NaN per campo {field}")
    return x

def norm_heading(deg):
    return deg % 360.0

def make_arrow_icon(deg, color, scale=18, dx=0, dy=0):
    """Crea una freccia ruotata di 'deg' gradi, colorata."""
    deg = norm_heading(deg)
    return DivIcon(
        icon_size=(20, 20),
        icon_anchor=(10, 10),
        html=f"""
        <div style="
            transform: translate({dx}px, {dy}px) rotate({deg}deg);
            transform-origin: center center;
            font-size:{scale}px;
            color:{color};
            line-height: 20px;
            ">
            ▲
        </div>
        """
    )

def main():
    m = folium.Map(location=[45.4640, 9.1900], zoom_start=14)
    punti = []
    first_point_set = False

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        missing = [c for c in REQUIRED_FIELDS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Colonne mancanti nel CSV: {missing}")

        for row in reader:
            try:
                lat = parse_required_float(row, "lat")
                lon = parse_required_float(row, "lon")

                # Salta righe con coordinate nulle
                if lat == 0 or lon == 0:
                    continue

                heading_mag = parse_required_float(row, "heading_mag")
                heading_gps = parse_required_float(row, "heading_gps")

            except ValueError as e:
                print(f"⚠️ Riga saltata: {e}")
                continue

            ts = row.get("timestamp", "")
            gps_speed_kn = row.get("gps_speed_kn", "")
            shift_deg = row.get("shift_deg", "")
            AWS_kn = row.get("AWS_kn", "")
            AWA_deg = row.get("AWA_deg", "")
            AWA_corr_deg = row.get("AWA_corr_deg", "")
            TWS_kn = row.get("TWS_kn", "")
            TWA_deg = row.get("TWA_deg", "")
            tws_nord = (float(TWA_deg) + float(heading_gps)) % 360

            if not first_point_set:
                m.location = [lat, lon]
                first_point_set = True

            punti.append([lat, lon])

            popup_html = f"""
            <b>Timestamp:</b> {ts}<br>
            <b>GPS speed:</b> {gps_speed_kn} kn<br>
            <b>Heading GPS:</b> {heading_gps}°<br>
            <b>Heading Mag:</b> {heading_mag}°<br>
            <b>Shift:</b> {shift_deg}°<br>
            <b>AWS:</b> {AWS_kn} kn<br>
            <b>AWA:</b> {AWA_deg}°<br>
            <b>AWA Corr:</b> {AWA_corr_deg}°<br>
            <b>TWS:</b> {TWS_kn} kn<br>
            <b>TWA:</b> {TWA_deg}°
            <b>TWS_nord:</b> {tws_nord}°
            """

            # Marker heading MAG (blu)
            folium.Marker(
                location=[lat, lon],
                popup=popup_html,
                icon=make_arrow_icon(heading_mag, color="blue",  scale=18, dx=-6, dy=0)
            ).add_to(m)

            # Marker heading GPS (verde)
            folium.Marker(
                location=[lat, lon],
                popup=popup_html,
                icon=make_arrow_icon(heading_gps, color="green", scale=16, dx=6, dy=0)
            ).add_to(m)

    if punti:
        folium.PolyLine(punti, color="red", weight=3, opacity=0.8).add_to(m)

    m.save(OUT_HTML)
    print(f"✅ Mappa salvata come '{OUT_HTML}'")

if __name__ == "__main__":
    main()

