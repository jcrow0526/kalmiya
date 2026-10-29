"""Calendario de Kalmiya: eventos guardados en %LOCALAPPDATA%\\Kalmiya\\calendario.json y,
si el usuario lo conecta, sincronizados con Google Calendar por su API.

Google: el usuario crea en Google Cloud un cliente OAuth tipo "App de escritorio",
descarga el JSON y lo elige en el calendario de Kalmiya; la autorización se hace en el
navegador y el token queda en google_token.json.
"""
import datetime as dt
import json
import logging
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field

from .config import CALENDAR_FILE, GOOGLE_CLIENT_FILE, GOOGLE_TOKEN_FILE

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass
class Event:
    titulo: str
    inicio: str  # ISO local "2026-10-30T16:00"
    fin: str
    lugar: str = ""
    descripcion: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    google_id: str = ""
    origen: str = "eco"  # eco | google
    avisado: bool = False
    tipo: str = "evento"  # evento | recordatorio (suena a su hora exacta, no 10 min antes)

    @property
    def es_recordatorio(self) -> bool:
        return self.tipo == "recordatorio"

    @property
    def start(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.inicio)

    @property
    def end(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.fin)

    @property
    def todo_el_dia(self) -> bool:
        return len(self.inicio) == 10


def spoken_date(d: dt.date) -> str:
    hoy = dt.date.today()
    if d == hoy:
        return "hoy"
    if d == hoy + dt.timedelta(days=1):
        return "mañana"
    return f"el {DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def spoken_time(t: dt.datetime) -> str:
    h = t.hour % 12 or 12
    m = f":{t.minute:02d}" if t.minute else ""
    parte = ("de la mañana" if t.hour < 12 else "del mediodía" if t.hour == 12
             else "de la tarde" if t.hour < 19 else "de la noche")
    return f"{'la' if h == 1 else 'las'} {h}{m} {parte}"


class Calendar:
    def __init__(self):
        self._lock = threading.Lock()
        self.events: list[Event] = []
        self.on_change = lambda: None
        self._load()

    # ------------------------------------------------------------ local
    def _load(self) -> None:
        try:
            data = json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
            self.events = [Event(**e) for e in data]
        except FileNotFoundError:
            self.events = []
        except Exception:
            log.exception("calendario.json dañado; se empieza vacío")
            shutil.copy(CALENDAR_FILE, CALENDAR_FILE.with_suffix(".bak"))
            self.events = []

    def _save(self) -> None:
        CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
        CALENDAR_FILE.write_text(json.dumps([asdict(e) for e in self.events], ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        self.on_change()

    def add(self, titulo: str, inicio: dt.datetime, minutos: int = 60, lugar: str = "",
            descripcion: str = "", tipo: str = "evento") -> Event:
        spec = "seconds" if tipo == "recordatorio" else "minutes"  # "en 2 minutos" debe sonar a su hora
        ev = Event(titulo, inicio.isoformat(timespec=spec),
                   (inicio + dt.timedelta(minutes=minutos)).isoformat(timespec=spec), lugar, descripcion,
                   tipo=tipo)
        with self._lock:
            self.events.append(ev)
            self._save()
        if self.google_connected:
            threading.Thread(target=self._push, args=(ev,), daemon=True).start()
        return ev

    def delete(self, ev_id: str) -> None:
        with self._lock:
            ev = next((e for e in self.events if e.id == ev_id), None)
            if ev is None:
                return
            self.events.remove(ev)
            self._save()
        if ev.google_id and self.google_connected:
            threading.Thread(target=self._google_delete, args=(ev.google_id,), daemon=True).start()

    def between(self, start: dt.datetime, end: dt.datetime) -> list[Event]:
        with self._lock:
            evs = [e for e in self.events if e.start < end and e.end > start]
        return sorted(evs, key=lambda e: e.inicio)

    def on_day(self, day: dt.date) -> list[Event]:
        start = dt.datetime.combine(day, dt.time())
        return self.between(start, start + dt.timedelta(days=1))

    def describe_day(self, day: dt.date) -> str:
        evs = [e for e in self.on_day(day) if not e.es_recordatorio]
        cuando = spoken_date(day)
        if not evs:
            return f"No tienes nada {cuando}"
        partes = [e.titulo if e.todo_el_dia else f"{e.titulo} a {spoken_time(e.start)}" for e in evs]
        lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
        return f"{cuando.capitalize()} tienes {len(evs)} {'evento' if len(evs) == 1 else 'eventos'}: {lista}"

    # ------------------------------------------------------------ recordatorios
    def add_reminder(self, texto: str, cuando: dt.datetime) -> Event:
        return self.add(texto, cuando.replace(microsecond=0), 5, tipo="recordatorio")

    def reminders(self, include_done: bool = False) -> list[Event]:
        now = dt.datetime.now() - dt.timedelta(minutes=1)
        with self._lock:
            evs = [e for e in self.events if e.es_recordatorio and (include_done or e.start >= now)]
        return sorted(evs, key=lambda e: e.inicio)

    def describe_reminders(self) -> str:
        evs = self.reminders()
        if not evs:
            return "No tienes recordatorios pendientes"
        partes = [f"{e.titulo}, {spoken_date(e.start.date())} a {spoken_time(e.start)}" for e in evs[:6]]
        n = len(evs)
        cab = f"Tienes {n} {'recordatorio' if n == 1 else 'recordatorios'}"
        return cab + ": " + "; ".join(partes) + ("; y más en tu lista" if n > 6 else "")

    def start_reminders(self, notify, minutes_before: int = 10) -> None:
        def loop():
            import time
            while True:
                now = dt.datetime.now()
                soon = now + dt.timedelta(minutes=minutes_before)
                with self._lock:
                    eventos = [e for e in self.events if not e.avisado and not e.todo_el_dia
                               and not e.es_recordatorio and now <= e.start <= soon]
                    # Recordatorios: a su hora (con hasta 10 min de gracia si la PC estaba dormida).
                    recs = [e for e in self.events if not e.avisado and e.es_recordatorio
                            and now - dt.timedelta(minutes=10) <= e.start <= now + dt.timedelta(seconds=15)]
                    for e in eventos + recs:
                        e.avisado = True
                    if eventos or recs:
                        self._save()
                for e in eventos:
                    mins = max(1, round((e.start - now).total_seconds() / 60))
                    notify(f"En {mins} minutos: {e.titulo}" + (f", en {e.lugar}" if e.lugar else ""))
                for e in recs:
                    notify(f"Recordatorio: {e.titulo}")
                time.sleep(10)
        threading.Thread(target=loop, daemon=True).start()

    # ------------------------------------------------------------ Google Calendar
    @property
    def google_connected(self) -> bool:
        return GOOGLE_TOKEN_FILE.exists()

    def _credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def _session(self):
        from google.auth.transport.requests import AuthorizedSession
        return AuthorizedSession(self._credentials())

    def connect_google(self, client_json: str | None = None) -> None:
        """Abre el navegador para autorizar a Kalmiya. Bloquea hasta que el usuario acepta."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        if client_json:
            shutil.copy(client_json, GOOGLE_CLIENT_FILE)
        flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CLIENT_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True,
                                      success_message="Listo: Kalmiya ya puede usar tu Google Calendar. "
                                                      "Puedes cerrar esta pestaña.")
        GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        for ev in [e for e in self.events if e.origen == "eco" and not e.google_id]:
            self._push(ev)  # subir lo que ya estaba en Kalmiya
        self.sync_google()

    def disconnect_google(self) -> None:
        GOOGLE_TOKEN_FILE.unlink(missing_ok=True)
        with self._lock:
            self.events = [e for e in self.events if e.origen != "google"]
            self._save()

    def _push(self, ev: Event) -> None:
        tz = dt.datetime.now().astimezone().tzinfo
        body = {"summary": ev.titulo, "location": ev.lugar, "description": ev.descripcion,
                "start": {"dateTime": ev.start.replace(tzinfo=tz).isoformat()},
                "end": {"dateTime": ev.end.replace(tzinfo=tz).isoformat()}}
        try:
            r = self._session().post(API, json=body, timeout=15)
            r.raise_for_status()
            with self._lock:
                ev.google_id = r.json()["id"]
                self._save()
        except Exception:
            log.exception("No pude subir el evento a Google")

    def _google_delete(self, google_id: str) -> None:
        try:
            self._session().delete(f"{API}/{google_id}", timeout=15)
        except Exception:
            log.exception("No pude borrar el evento en Google")

    def sync_google(self, days: int = 60) -> int:
        """Trae los eventos de Google (de hace una semana a `days` días)."""
        if not self.google_connected:
            return 0
        tz = dt.datetime.now().astimezone().tzinfo
        now = dt.datetime.now(tz)
        params = {"timeMin": (now - dt.timedelta(days=7)).isoformat(), "timeMax": (now + dt.timedelta(days=days)).isoformat(),
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": 500}
        r = self._session().get(API, params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        with self._lock:
            mine = {e.google_id for e in self.events if e.origen == "eco" and e.google_id}
            avisados = {e.google_id for e in self.events if e.avisado}
            self.events = [e for e in self.events if e.origen != "google"]
            for it in items:
                if it["id"] in mine or it.get("status") == "cancelled":
                    continue
                s, f = it.get("start", {}), it.get("end", {})
                if "dateTime" in s:
                    ini = dt.datetime.fromisoformat(s["dateTime"]).astimezone(tz).replace(tzinfo=None)
                    fin = dt.datetime.fromisoformat(f["dateTime"]).astimezone(tz).replace(tzinfo=None)
                    ini_s, fin_s = ini.isoformat(timespec="minutes"), fin.isoformat(timespec="minutes")
                else:  # todo el día
                    ini_s, fin_s = s["date"], f["date"]
                self.events.append(Event(it.get("summary", "(sin título)"), ini_s, fin_s, it.get("location", ""),
                                         it.get("description", ""), google_id=it["id"], origen="google",
                                         avisado=it["id"] in avisados))
            self._save()
        return len(items)

    def start_google_sync(self, every_minutes: int = 10) -> None:
        def loop():
            import time
            while True:
                try:
                    self.sync_google()
                except Exception as e:
                    log.warning("Sincronización con Google falló: %s", e)
                time.sleep(every_minutes * 60)
        threading.Thread(target=loop, daemon=True).start()
