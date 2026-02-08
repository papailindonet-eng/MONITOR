import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import re
from typing import List, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from flask import Flask, jsonify, request, send_from_directory

SOURCE_URL = "https://agendeam.com.br/ujf/motorista.php"
POLL_INTERVAL_SECONDS = 10
TRANSPORTADORA_ALERT = "TRANSPORTADORA SEIS"
ALERT_STATUS = "CHAMADO DA PORTARIA"


@dataclass
class VehicleEntry:
    agenda: str
    caminhao: str
    transportadora: str
    pdt: str
    status: str
    sinal: str
    local: str
    previsao: str

    @property
    def vehicle_id(self) -> str:
        return f"{self.agenda}|{self.caminhao}|{self.transportadora}".strip().upper()


class MotoristaTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_td = False
        self.current_row: List[str] = []
        self.rows: List[List[str]] = []
        self._buffer: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self.current_row = []
        if tag.lower() == "td":
            self.in_td = True
            self._buffer = []

    def handle_endtag(self, tag):
        if tag.lower() == "td":
            self.in_td = False
            cell_text = "".join(self._buffer)
            self.current_row.append(cell_text)
            self._buffer = []
        if tag.lower() == "tr":
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_td:
            self._buffer.append(data)


def clean_text(value: str) -> str:
    cleaned = unescape(value or "")
    cleaned = cleaned.replace("\xa0", " ").replace("&nbsp;", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def parse_vehicle_rows(html: str) -> List[VehicleEntry]:
    parser = MotoristaTableParser()
    parser.feed(html)
    vehicles: List[VehicleEntry] = []
    seen: set[str] = set()
    agenda_pattern = re.compile(r"^\d{2}/\d{2}/\d{4}")
    for row in parser.rows:
        cleaned = [clean_text(cell) for cell in row]
        if len(cleaned) < 8:
            continue
        if cleaned[0].upper() == "AGENDA":
            continue
        agenda, caminhao, transportadora, pdt, status, sinal, local, previsao = cleaned[:8]
        if not agenda or not caminhao:
            continue
        if not agenda_pattern.match(agenda):
            continue
        dedupe_key = "|".join(
            [agenda.strip(), caminhao.strip(), transportadora.strip(), status.strip()]
        ).upper()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        vehicles.append(
            VehicleEntry(
                agenda=agenda,
                caminhao=caminhao,
                transportadora=transportadora,
                pdt=pdt,
                status=status,
                sinal=sinal,
                local=local,
                previsao=previsao,
            )
        )
    return vehicles


class MonitorState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.vehicles: List[VehicleEntry] = []
        self.last_success: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_fetch_duration: Optional[float] = None
        self.alert_event_id = 0
        self.last_ack_event_id = 0
        self.alert_vehicles: List[VehicleEntry] = []
        self.previous_status: Dict[str, str] = {}
        self.has_baseline = False

    def update(self, vehicles: List[VehicleEntry], duration: float) -> None:
        with self.lock:
            previous_status = self.previous_status
            new_status_map: Dict[str, str] = {}
            alert_matches: List[VehicleEntry] = []
            for vehicle in vehicles:
                normalized_status = vehicle.status.strip().upper()
                new_status_map[vehicle.vehicle_id] = normalized_status
                if (
                    vehicle.transportadora.strip().upper() == TRANSPORTADORA_ALERT
                    and normalized_status == ALERT_STATUS
                ):
                    if self.has_baseline:
                        previous = previous_status.get(vehicle.vehicle_id)
                        if previous != ALERT_STATUS:
                            alert_matches.append(vehicle)
            if alert_matches:
                self.alert_event_id += 1
                self.alert_vehicles = alert_matches
            self.previous_status = new_status_map
            self.has_baseline = True
            self.vehicles = vehicles
            self.last_success = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            self.last_fetch_duration = duration

    def register_error(self, error: str) -> None:
        with self.lock:
            self.last_error = error

    def ack_alert(self, event_id: int) -> None:
        with self.lock:
            if event_id >= self.last_ack_event_id:
                self.last_ack_event_id = event_id

    def snapshot(self) -> Dict[str, object]:
        with self.lock:
            vehicles = list(self.vehicles)
            last_success = self.last_success
            last_error = self.last_error
            last_fetch_duration = self.last_fetch_duration
            alert_event_id = self.alert_event_id
            last_ack_event_id = self.last_ack_event_id
            alert_vehicles = list(self.alert_vehicles)
        transportadora_seis = [v for v in vehicles if v.transportadora.strip().upper() == TRANSPORTADORA_ALERT]
        total_fila = sum(1 for v in vehicles if v.status.strip().upper() == "FILA")
        active_alert = alert_event_id > last_ack_event_id
        snapshot = {
            "source": SOURCE_URL,
            "last_success": last_success,
            "last_error": last_error,
            "last_fetch_duration": last_fetch_duration,
            "total_fila": total_fila,
            "vehicles": [asdict(v) for v in vehicles],
            "transportadora_seis": [asdict(v) for v in transportadora_seis],
            "alert": {
                "event_id": alert_event_id,
                "active": active_alert,
                "vehicles": [asdict(v) for v in alert_vehicles],
                "last_ack_event_id": last_ack_event_id,
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return snapshot


state = MonitorState()


def fetch_source_html() -> str:
    headers = {
        "User-Agent": "MonitorBot/1.0",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    request = Request(SOURCE_URL, headers=headers)
    with urlopen(request, timeout=15) as response:
        raw = response.read()
        try:
            return raw.decode("iso-8859-1")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="ignore")


def poll_loop() -> None:
    while True:
        started = time.time()
        try:
            html = fetch_source_html()
            vehicles = parse_vehicle_rows(html)
            duration = time.time() - started
            state.update(vehicles, duration)
            logging.info("Atualizacao concluida: %s veiculos", len(vehicles))
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            duration = time.time() - started
            state.register_error(str(exc))
            logging.error("Falha ao atualizar (%ss): %s", round(duration, 2), exc)
        except Exception as exc:  # noqa: BLE001
            duration = time.time() - started
            state.register_error(str(exc))
            logging.exception("Erro inesperado (%ss)", round(duration, 2))
        time.sleep(POLL_INTERVAL_SECONDS)


app = Flask(__name__, static_folder="../frontend", static_url_path="")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/ack", methods=["POST"])
def api_ack():
    payload = request.get_json(silent=True) or {}
    event_id = int(payload.get("event_id", 0))
    state.ack_alert(event_id)
    return jsonify({"ok": True, "event_id": event_id})


if __name__ == "__main__":
    os.makedirs("backend/data", exist_ok=True)
    log_path = "backend/data/monitor.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )
    thread = threading.Thread(target=poll_loop, daemon=True)
    thread.start()
    app.run(host="0.0.0.0", port=8000, debug=False)
