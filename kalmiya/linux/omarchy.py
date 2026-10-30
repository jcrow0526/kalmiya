"""Herramientas para manejar Omarchy (Hyprland + el shell de Omarchy) por voz.

Solo se registran en Linux con Hyprland, y las que usan comandos `omarchy-*` solo si
Omarchy está instalado. Son pocas herramientas con muchas acciones (y no muchas
herramientas pequeñas) porque LFM3-1.7B elige peor entre muchas opciones.

Lo que necesita la pantalla limpia (capturas, OCR, selector de color, grabar) va a
`ctx.later`: se ejecuta cuando Kalmiya termina de hablar y el orbe ya se ocultó, para
que no salga en la imagen.
"""
import difflib
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..tools import _norm, ask, ctx, tool
from . import desktop

log = logging.getLogger(__name__)

OMARCHY = shutil.which("omarchy") is not None


def _run(*cmd: str, timeout: float = 8) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _rc(*cmd: str, timeout: float = 15) -> bool:
    """Para órdenes que cambian algo: sin capturar la salida, porque algunas dejan un demonio en
    segundo plano (hyprsunset) que heredaría la tubería y nunca se cerraría."""
    try:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout, start_new_session=True).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _spawn(*cmd: str) -> None:
    """Comandos que tardan o se quedan esperando (menús, selección con el ratón)."""
    # Desde $HOME, como un atajo de Hyprland (omarchy-agent pasa de ahí a ~/Work).
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                     cwd=Path.home())


def _later(*cmd: str) -> None:
    ctx.later.append(lambda: _spawn(*cmd))


def _ok(r: subprocess.CompletedProcess) -> bool:
    return r.returncode == 0


# ---------------------------------------------------------------- ventanas y escritorios

_NUM = {"uno": 1, "un": 1, "primer": 1, "primero": 1, "dos": 2, "segundo": 2, "tres": 3, "tercer": 3, "tercero": 3,
        "cuatro": 4, "cuarto": 4, "cinco": 5, "quinto": 5, "seis": 6, "sexto": 6, "siete": 7, "septimo": 7,
        "ocho": 8, "octavo": 8, "nueve": 9, "noveno": 9, "diez": 10, "decimo": 10}


def _numero(n) -> int | None:
    if isinstance(n, (int, float)):
        return int(n)
    s = str(n or "").strip().lower()
    return int(s) if s.isdigit() else _NUM.get(s)


@tool("Escritorios (espacios de trabajo) y ventanas de Hyprland: ir al escritorio N, mover la ventana activa "
      "al escritorio N, escritorio siguiente/anterior, ventana flotante, cambiar la división, sacar la ventana "
      "flotando encima (emergente), cambiar la disposición, el scratchpad o cambiar el foco a izquierda/derecha. "
      "cerrar_todas solo si pide cerrar todas las ventanas.",
      {"accion": ("string", "Acción", ["ir_a", "mover_ventana_a", "siguiente", "anterior", "anterior_usado",
                                       "flotante", "dividir", "emergente", "disposicion", "scratchpad",
                                       "guardar_en_scratchpad", "foco_izquierda", "foco_derecha", "foco_arriba",
                                       "foco_abajo", "intercambiar_izquierda", "intercambiar_derecha",
                                       "cerrar_todas"]),
       "numero": ("integer", "Número de escritorio (con ir_a y mover_ventana_a)")},
      required=["accion"])
def escritorio(accion: str, numero: int | None = None) -> str:
    n = _numero(numero)
    if accion in ("ir_a", "mover_ventana_a"):
        if not n or not 1 <= n <= 10:
            return "¿A qué escritorio? Del 1 al 10"
        if accion == "ir_a":
            desktop.dispatch(f'hl.dsp.focus({{ workspace = "{n}" }})', ["workspace", str(n)])
            return f"Escritorio {n}"
        if not desktop.active_window():
            return "Error: no hay ninguna ventana activa que mover"
        desktop.dispatch(f'hl.dsp.window.move({{ workspace = "{n}" }})', ["movetoworkspace", str(n)])
        return f"Moví la ventana al escritorio {n}"
    simple = {
        "siguiente": ('hl.dsp.focus({ workspace = "e+1" })', ["workspace", "e+1"], "Siguiente escritorio"),
        "anterior": ('hl.dsp.focus({ workspace = "e-1" })', ["workspace", "e-1"], "Escritorio anterior"),
        "anterior_usado": ('hl.dsp.focus({ workspace = "previous" })', ["workspace", "previous"], "Listo"),
        "flotante": ('hl.dsp.window.float({ action = "toggle" })', ["togglefloating"], "Listo"),
        "dividir": ('hl.dsp.layout("togglesplit")', ["layoutmsg", "togglesplit"], "Cambié la división"),
        "scratchpad": ('hl.dsp.workspace.toggle_special("scratchpad")', ["togglespecialworkspace", "scratchpad"],
                       "Listo"),
        "guardar_en_scratchpad": ('hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })',
                                  ["movetoworkspacesilent", "special:scratchpad"], "La guardé en el scratchpad"),
    }
    for d, k in (("izquierda", "l"), ("derecha", "r"), ("arriba", "u"), ("abajo", "d")):
        simple[f"foco_{d}"] = (f'hl.dsp.focus({{ direction = "{k}" }})', ["movefocus", k], "Listo")
        simple[f"intercambiar_{d}"] = (f'hl.dsp.window.swap({{ direction = "{k}" }})', ["swapwindow", k], "Listo")
    if accion in simple:
        lua, legacy, msg = simple[accion]
        return msg if desktop.dispatch(lua, legacy) else f"Error: Hyprland no aceptó {accion}"
    if accion == "emergente":
        _spawn("omarchy-hyprland-window-pop")
        return "Listo"
    if accion == "disposicion":
        _spawn("omarchy-hyprland-workspace-layout-toggle")
        return "Cambié la disposición del escritorio"
    if accion == "cerrar_todas":
        return ask("¿Cierro todas las ventanas?",
                   lambda: (_later("omarchy-hyprland-window-close-all"), "Cierro todas las ventanas")[1], siempre=True)
    return f"Error: acción desconocida {accion}"


# ---------------------------------------------------------------- apariencia

def _temas() -> list[str]:
    r = _run("omarchy-theme-list")
    return [t.strip() for t in r.stdout.splitlines() if t.strip()]


def _tema_actual() -> str:
    return _run("omarchy-theme-current").stdout.strip()


def _buscar_tema(nombre: str) -> str | None:
    temas = _temas()
    clave = " ".join(_norm(nombre)).removeprefix("tema ").strip()
    por_clave = {" ".join(_norm(t)): t for t in temas}
    if clave in por_clave:
        return por_clave[clave]
    parcial = [t for k, t in por_clave.items() if clave and (clave in k or k in clave)]
    if parcial:
        return min(parcial, key=len)
    cerca = difflib.get_close_matches(clave, por_clave, n=1, cutoff=0.6)
    return por_clave[cerca[0]] if cerca else None


_CLAROS = ("latte", "light", "white")


@tool("Apariencia de Omarchy: cambiar el tema ('pon el tema Nord', 'pon un tema claro/oscuro'), poner otro tema "
      "al azar, decir qué tema hay, cambiar el fondo de pantalla, activar/quitar la transparencia de la ventana, "
      "los márgenes entre ventanas, u ocultar/mostrar la barra de arriba (barra).",
      {"accion": ("string", "Acción", ["cambiar_tema", "tema_al_azar", "tema_claro", "tema_oscuro", "tema_actual",
                                       "listar_temas", "siguiente_fondo", "transparencia", "margenes", "barra"]),
       "nombre": ("string", "Nombre del tema (solo con cambiar_tema), p.ej. 'Tokyo Night'")},
      required=["accion"])
def apariencia(accion: str, nombre: str = "") -> str:
    if accion == "cambiar_tema" and not nombre:
        accion = "tema_al_azar"  # "pon otro tema"
    if accion == "cambiar_tema":
        tema = _buscar_tema(nombre)
        if tema is None:
            return f"No tengo el tema {nombre}. " + _lista_temas()
        return _poner_tema(tema)
    if accion in ("tema_al_azar", "tema_claro", "tema_oscuro"):
        import random
        actual = _tema_actual()
        temas = [t for t in _temas() if t != actual]
        if accion != "tema_al_azar":
            claro = accion == "tema_claro"
            temas = [t for t in temas if any(c in t.lower() for c in _CLAROS) == claro]
        if not temas:
            return "No tengo otro tema así"
        return _poner_tema(random.choice(temas))
    if accion == "tema_actual":
        return f"Tienes el tema {_tema_actual()}"
    if accion == "listar_temas":
        return _lista_temas()
    if accion == "siguiente_fondo":
        _spawn("omarchy-theme-bg-next")
        return "Cambié el fondo"
    if accion == "transparencia":
        _spawn("omarchy-hyprland-window-transparency-toggle")
        return "Cambié la transparencia de la ventana"
    if accion == "margenes":
        _spawn("omarchy-hyprland-window-gaps-toggle")
        return "Cambié los márgenes"
    if accion == "barra":
        _spawn("omarchy-toggle-bar")
        return "Listo"
    return f"Error: acción desconocida {accion}"


def _poner_tema(tema: str) -> str:
    _spawn("omarchy-theme-set", tema)
    return f"Pongo el tema {tema}"


def _lista_temas() -> str:
    return "Tengo " + ", ".join(_temas())


# ---------------------------------------------------------------- ajustes rápidos

def _estado_actual(ajuste: str) -> bool | None:
    try:
        if ajuste == "luz_nocturna":
            return json.loads(_run("omarchy-toggle-nightlight", "--status").stdout)["enabled"]
        if ajuste == "no_molestar":
            return _run("omarchy-shell", "notifications", "isDnd").stdout.strip() == "on"
        if ajuste == "mantener_despierto":
            return json.loads(_run("omarchy-toggle-idle", "status").stdout)["enabled"]
        if ajuste == "bluetooth":
            return _ok(_run("omarchy-bluetooth-power", "is-on"))
        if ajuste == "wifi":
            return _run("nmcli", "radio", "wifi").stdout.strip() == "enabled"
    except Exception as e:
        log.warning("No pude leer el estado de %s: %s", ajuste, e)
    return None


_NOMBRES = {"luz_nocturna": "la luz nocturna", "no_molestar": "el modo no molestar",
            "mantener_despierto": "el modo mantener despierta la pantalla", "bluetooth": "el Bluetooth",
            "wifi": "el wifi"}


def _aplicar(ajuste: str, encender: bool) -> bool:
    if ajuste == "luz_nocturna":
        return _rc("omarchy-toggle-nightlight")
    if ajuste == "no_molestar":
        return _rc("omarchy-shell", "notifications", "setDnd", "true" if encender else "false")
    if ajuste == "mantener_despierto":
        return _rc("omarchy-toggle-idle", "stay-awake" if encender else "allow-idle")
    if ajuste == "bluetooth":
        return _rc("omarchy-bluetooth-power", "on" if encender else "off")
    if ajuste == "wifi":
        return _rc("nmcli", "radio", "wifi", "on" if encender else "off")
    return False


@tool("Ajustes rápidos: encender o apagar la luz nocturna, el modo no molestar (silenciar notificaciones), "
      "mantener la pantalla despierta, el Bluetooth o el wifi; o cambiar el modo de energía (ahorro, equilibrado, "
      "rendimiento).",
      {"ajuste": ("string", "Ajuste", ["luz_nocturna", "no_molestar", "mantener_despierto", "bluetooth", "wifi",
                                       "modo_ahorro", "modo_equilibrado", "modo_rendimiento"]),
       "estado": ("string", "encender, apagar o alternar (si no lo dice)", ["encender", "apagar", "alternar"])},
      required=["ajuste"])
def ajuste_rapido(ajuste: str, estado: str = "alternar") -> str:
    if ajuste.startswith("modo_"):
        perfil = {"modo_ahorro": "power-saver", "modo_equilibrado": "balanced",
                  "modo_rendimiento": "performance"}[ajuste]
        if not _rc("omarchy-powerprofiles-set", "autodetect", perfil):
            return f"Tu equipo no tiene el modo {ajuste.removeprefix('modo_')}"
        return f"Modo {ajuste.removeprefix('modo_')} activado"
    if ajuste not in _NOMBRES:
        return f"Error: ajuste desconocido {ajuste}"
    actual = _estado_actual(ajuste)
    encender = (not actual) if estado == "alternar" else estado == "encender"
    nombre = _NOMBRES[ajuste]
    if actual is not None and actual == encender:
        hecho = ("activada" if encender else "apagada") if ajuste == "luz_nocturna" else \
            ("activado" if encender else "apagado")
        return f"{nombre[0].upper()}{nombre[1:]} ya estaba {hecho}"
    if ajuste == "wifi" and not encender:
        return ask("Si apago el wifi puedes quedarte sin internet. ¿Lo apago?",
                   lambda: _resultado(ajuste, False))
    return _resultado(ajuste, encender)


def _resultado(ajuste: str, encender: bool) -> str:
    nombre = _NOMBRES[ajuste]
    if not _aplicar(ajuste, encender):
        return f"Error: no pude cambiar {nombre}"
    return f"{'Activé' if encender else 'Apagué'} {nombre}"


@tool("Brillo de la pantalla: subir, bajar o fijar un porcentaje.",
      {"accion": ("string", "Acción", ["subir", "bajar", "fijar"]),
       "nivel": ("integer", "Solo con fijar: porcentaje de 1 a 100")},
      required=["accion"])
def brillo(accion: str, nivel: int | None = None) -> str:
    if nivel is not None:
        accion = "fijar"
    paso = {"subir": "+10%", "bajar": "10%-"}.get(accion)
    if accion == "fijar":
        if nivel is None:
            return "¿A qué porcentaje lo pongo?"
        paso = f"{max(1, min(100, int(nivel)))}%"
    if paso is None:
        return f"Error: acción desconocida {accion}"
    if not _rc("omarchy-brightness-display", paso):
        return "Error: tu pantalla no deja cambiar el brillo desde el sistema"
    return f"Brillo al {paso.rstrip('%-').lstrip('+')} por ciento" if accion == "fijar" else \
        ("Subí el brillo" if accion == "subir" else "Bajé el brillo")


# ---------------------------------------------------------------- capturas

@tool("Captura de pantalla: toda la pantalla, una zona o una ventana que el usuario elige con el ratón, copiar "
      "el texto de una zona (OCR), copiar un color de la pantalla o leer un código QR.",
      {"modo": ("string", "pantalla, zona, ventana, texto (copiar texto de la pantalla), color o qr",
                ["pantalla", "zona", "ventana", "texto", "color", "qr"])},
      required=[])
def captura_pantalla(modo: str = "pantalla") -> str:
    if modo == "pantalla":
        _later("omarchy-capture-screenshot", "fullscreen")
        return "Tomo la captura; quedará en Imágenes y en el portapapeles"
    if modo in ("zona", "ventana"):
        _later("omarchy-capture-screenshot", "region" if modo == "zona" else "windows")
        return "Selecciona la zona con el ratón" if modo == "zona" else "Haz clic en la ventana que quieras capturar"
    if modo == "texto":
        _later("omarchy-capture-text")
        return "Selecciona el texto; te lo dejo copiado"
    if modo == "color":
        _later("hyprpicker", "-a")
        return "Haz clic en el color; te lo dejo copiado"
    if modo == "qr":
        _later("omarchy-capture-qr")
        return "Selecciona el código QR"
    return f"Error: modo desconocido {modo}"


def _grabando() -> bool:
    return subprocess.run(["pgrep", "-f", "gpu-screen-recorder"], capture_output=True).returncode == 0


@tool("Grabar la pantalla en video: empezar (con o sin el sonido del escritorio) o detener la grabación.",
      {"accion": ("string", "Acción", ["empezar", "detener"]),
       "con_audio": ("boolean", "Grabar también el sonido del escritorio")},
      required=["accion"])
def grabar_pantalla(accion: str, con_audio: bool = False) -> str:
    if accion == "detener":
        if not _grabando():
            return "No estoy grabando la pantalla"
        _spawn("omarchy-capture-screenrecording", "--stop-recording")
        return "Detuve la grabación; el video queda en Videos"
    if _grabando():
        return "Ya estoy grabando la pantalla"
    args = ["omarchy-capture-screenrecording", "--fullscreen"] + (["--with-desktop-audio"] if con_audio else [])
    _later(*args)
    return "Empiezo a grabar la pantalla. Para parar, dime detén la grabación"


# ---------------------------------------------------------------- menús y paneles

_PANELES = {
    "menu": ("omarchy-menu", "summon"), "aplicaciones": ("omarchy-menu", "summon", "apps"),
    "sistema": ("omarchy-menu", "summon", "system"), "temas": ("omarchy-menu", "summon", "theme"),
    "fondos": ("omarchy-menu", "summon", "background"), "capturas": ("omarchy-menu", "summon", "capture"),
    "ajustes": ("omarchy-menu", "summon", "toggle"), "hardware": ("omarchy-menu", "summon", "hardware"),
    "compartir": ("omarchy-menu", "summon", "share"), "atajos": ("omarchy-menu-keybindings",),
    "wifi": ("omarchy-shell", "shell", "summon", "omarchy.network", "{}"),
    "bluetooth": ("omarchy-shell", "shell", "summon", "omarchy.bluetooth", "{}"),
    "audio": ("omarchy-shell", "shell", "summon", "omarchy.audio", "{}"),
    "pantallas": ("omarchy-shell", "shell", "summon", "omarchy.monitor", "{}"),
    "energia": ("omarchy-shell", "shell", "summon", "omarchy.power", "{}"),
    "portapapeles": ("omarchy-shell", "shell", "summon", "omarchy.clipboard", "{}"),
    "emojis": ("omarchy-shell", "shell", "summon", "omarchy.emojis", "{}"),
    "notificaciones": ("omarchy-shell", "notifications", "showHistory"),
    "actividad": ("omarchy-launch-or-focus-tui", "btop"),
}


@tool("Abre un menú o panel de Omarchy: el menú principal, el lanzador de aplicaciones, el menú de apagado "
      "(sistema), temas, fondos, capturas, ajustes, hardware, compartir, la lista de atajos de teclado, los "
      "paneles de wifi, Bluetooth, audio, pantallas o energía, el historial del portapapeles, los emojis, el "
      "historial de notificaciones o el monitor de actividad (CPU y memoria).",
      {"panel": ("string", "Panel", list(_PANELES))})
def abrir_panel(panel: str) -> str:
    cmd = _PANELES.get(" ".join(_norm(panel)))  # el modelo a veces manda "menú"
    if cmd is None:
        return f"Error: no conozco el panel {panel}"
    _spawn(*cmd)
    return "Ahí lo tienes"


@tool("Notificaciones: descartar la última, descartar todas o abrir la última.",
      {"accion": ("string", "Acción", ["descartar_ultima", "descartar_todas", "abrir_ultima"])})
def notificaciones(accion: str) -> str:
    metodo = {"descartar_ultima": "dismissOne", "descartar_todas": "dismissAll", "abrir_ultima": "invokeLast"}
    if accion not in metodo:
        return f"Error: acción desconocida {accion}"
    _rc("omarchy-shell", "notifications", metodo[accion])
    return {"descartar_ultima": "Listo", "descartar_todas": "Borré las notificaciones", "abrir_ultima": "Listo"}[accion]


# ---------------------------------------------------------------- sistema

_ENERGIA = {
    "apagar": ("¿Apago la computadora?", "Apagando", ["omarchy-system-shutdown"]),
    "reiniciar": ("¿Reinicio la computadora?", "Reiniciando", ["omarchy-system-reboot"]),
    "suspender": ("¿Suspendo la computadora?", "Suspendiendo", ["systemctl", "suspend"]),
    "cerrar_sesion": ("¿Cierro la sesión?", "Cerrando la sesión", ["omarchy-system-logout"]),
}


@tool("Apagar, reiniciar, suspender la computadora o cerrar la sesión (siempre pregunta antes). "
      "Para bloquear usa bloquear_pc.",
      {"accion": ("string", "Acción", list(_ENERGIA))})
def energia(accion: str) -> str:
    if accion not in _ENERGIA:
        return f"Error: acción desconocida {accion}"
    pregunta, dicho, cmd = _ENERGIA[accion]
    return ask(pregunta, lambda: (_later(*cmd), dicho)[1], siempre=True)


def _hay_actualizacion() -> str:
    """'Hay una actualización de Omarchy (4.0.4 a 4.0.6)' o '' si está al día."""
    r = _run("omarchy-update-available", timeout=20)
    m = re.search(r"([\d.]+)-\d+ -> ([\d.]+)", r.stdout)
    if r.returncode != 0 or not m:
        return ""
    return f"Hay una actualización de Omarchy, de la {m[1]} a la {m[2]}"


@tool("Actualiza el sistema (Omarchy y los paquetes) en una terminal. Pregunta antes.")
def actualizar_sistema() -> str:
    def go():
        _spawn("omarchy-launch-floating-terminal-with-presentation", "omarchy-update")
        return "Abrí la actualización en una terminal"
    if not _hay_actualizacion():
        return ask("Parece que no hay actualizaciones de Omarchy. ¿Actualizo los paquetes igualmente?", go,
                   siempre=True)
    return ask("¿Actualizo el sistema? Puede tardar varios minutos.", go, siempre=True)


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}".replace(".", ",").removesuffix(",0") + " gigas"


@tool("Dice el estado del equipo: memoria, procesador, disco, batería, red (wifi e IP), tiempo encendido o "
      "si hay actualizaciones.",
      {"dato": ("string", "Qué dato", ["memoria", "procesador", "disco", "bateria", "red", "tiempo_encendido",
                                       "actualizaciones"])})
def estado_equipo(dato: str) -> str:
    import psutil
    if dato == "memoria":
        m = psutil.virtual_memory()
        return f"Usas {_gb(m.used)} de {_gb(m.total)} de memoria, el {m.percent:.0f} por ciento"
    if dato == "procesador":
        procs = list(psutil.process_iter(["name"]))
        for p in procs:
            try:
                p.cpu_percent()  # la primera lectura solo arranca la medida
            except psutil.Error:
                pass
        uso = psutil.cpu_percent(interval=0.7)
        top = []
        for p in procs:
            try:
                top.append((p.cpu_percent(), p.info["name"]))
            except psutil.Error:
                pass
        top.sort(reverse=True)
        quien = f"; lo que más usa es {top[0][1]}" if top and top[0][0] > 10 else ""
        return f"El procesador está al {uso:.0f} por ciento{quien}"
    if dato == "disco":
        d = psutil.disk_usage(str(Path.home()))
        return f"Te quedan {_gb(d.free)} libres de {_gb(d.total)}"
    if dato == "bateria":
        b = psutil.sensors_battery()
        if b is None:
            return "Este equipo no tiene batería"
        return f"Batería al {b.percent:.0f} por ciento" + (", cargando" if b.power_plugged else "")
    if dato == "red":
        r = _run("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device")
        conn = [ln.split(":", 3) for ln in r.stdout.splitlines()]
        conn = [c for c in conn if len(c) == 4 and c[2] == "connected" and c[1] in ("wifi", "ethernet")]
        if not conn:
            return "No estás conectado a ninguna red"
        dev, tipo, _, nombre = conn[0]
        ip = _run("nmcli", "-g", "IP4.ADDRESS", "device", "show", dev).stdout.split("/")[0].strip()
        if tipo == "wifi":
            donde = f"al wifi {nombre}"
        else:
            donde = "por cable"
        return f"Estás conectado {donde}" + (f", con la IP {ip}" if ip else "")
    if dato == "tiempo_encendido":
        s = int(time.time() - psutil.boot_time())
        h, m = s // 3600, s % 3600 // 60
        return f"Llevas encendido {h} horas y {m} minutos" if h else f"Llevas encendido {m} minutos"
    if dato == "actualizaciones":
        return _hay_actualizacion() or "Omarchy está al día"
    return f"Error: dato desconocido {dato}"


# ---------------------------------------------------------------- apps de Omarchy

def _webapp(host: str, url: str) -> list[str]:
    return ["omarchy-launch-or-focus-webapp", re.escape(host), url]


# Nombre dicho -> (orden, cómo decirlo). Van antes que los .desktop: la terminal es la que el usuario
# eligió en Omarchy, y las web apps se abren (o se traen al frente) como en sus atajos.
_APPS = {
    "terminal": (["omarchy-launch-terminal"], "la terminal"),
    "chatgpt": (_webapp("chatgpt.com", "https://chatgpt.com"), "ChatGPT"),
    "grok": (_webapp("grok.com", "https://grok.com"), "Grok"),
    "x": (_webapp("x.com", "https://x.com/"), "X"),
    "google fotos": (_webapp("photos.google.com", "https://photos.google.com/"), "Google Fotos"),
    "google maps": (_webapp("maps.google.com", "https://maps.google.com/"), "Google Maps"),
    "google messages": (_webapp("messages.google.com", "https://messages.google.com/web/conversations"),
                        "Google Messages"),
    "ajustes": (["omarchy-menu", "summon"], "el menú de Omarchy"),
    "administrador de tareas": (["omarchy-launch-or-focus-tui", "btop"], "el monitor de actividad"),
    # El agente de programación de Omarchy (Super+Shift+Ctrl+A), p. ej. Claude Code.
    "agente": (["omarchy-agent"], "tu agente"),
}
_APP_ALIAS = {"consola": "terminal", "la terminal": "terminal", "una terminal": "terminal", "terminal nueva": "terminal",
              "chat gpt": "chatgpt", "twitter": "x",
              "fotos": "google fotos", "fotos de google": "google fotos", "mapas": "google maps", "maps": "google maps",
              "mensajes": "google messages", "configuracion": "ajustes", "configuración": "ajustes",
              "monitor del sistema": "administrador de tareas", "monitor de actividad": "administrador de tareas",
              "btop": "administrador de tareas",
              # Whisper oye "abre mi gente" por "abre mi agente".
              "gente": "agente", "agente de inteligencia artificial": "agente", "agente de ia": "agente",
              "agente de programacion": "agente", "agente de programación": "agente", "claude": "agente",
              "claude code": "agente", "inteligencia artificial": "agente", "ia": "agente"}


def abrir_app(nombre: str) -> str | None:
    """Abre una app con el lanzador de Omarchy; None si no es una de estas."""
    key = re.sub(r"^(mi|el|la|una|un)\s+", "", nombre.lower().strip())
    key = _APP_ALIAS.get(key, key)
    if key == "spotify":
        if not shutil.which("spotify"):
            return ask("Spotify no está instalado. ¿Lo instalo?",
                       lambda: (_spawn("omarchy-launch-spotify"), "Abrí el instalador de Spotify")[1])
        _spawn("omarchy-launch-spotify")
        return "Abrí Spotify"
    if key not in _APPS:
        return None
    cmd, dicho = _APPS[key]
    _spawn(*cmd)
    return f"Abrí {dicho}"


# ---------------------------------------------------------------- órdenes rápidas (sin el modelo)

def _es_app(texto: str) -> bool:
    if texto in _APPS or texto in _APP_ALIAS or texto == "spotify":
        return True
    return any(" ".join(_norm(k)) == texto for k in desktop.desktop_apps())


def _tema_rapido(texto: str) -> dict | None:
    if texto in ("claro", "oscuro"):
        return {"accion": f"tema_{texto}"}
    por_clave = {" ".join(_norm(t)): t for t in _temas()}
    return {"accion": "cambiar_tema", "nombre": por_clave[texto]} if texto in por_clave else None


def _n(m) -> dict | None:
    n = _numero(m["n"])
    return {"accion": "ir_a", "numero": n} if n else None


QUICK = [
    (re.compile(r"^(ve|vete|ir|cambia|pasa|llevame|salta|muevete)( al| a)? (escritorio|espacio( de trabajo)?) "
                r"(numero )?(?P<n>\w+)$"), "escritorio", _n),
    (re.compile(r"^(escritorio|espacio) (?P<n>\w+)$"), "escritorio", _n),
    (re.compile(r"^(mueve|manda|lleva|pasa) (esta|la) ventana al? (escritorio|espacio( de trabajo)?) "
                r"(numero )?(?P<n>\w+)$"), "escritorio",
     lambda m: {"accion": "mover_ventana_a", "numero": _numero(m["n"])} if _numero(m["n"]) else None),
    (re.compile(r"^(activa|enciende|pon|prende)( la)? luz nocturna$"), "ajuste_rapido",
     {"ajuste": "luz_nocturna", "estado": "encender"}),
    (re.compile(r"^(desactiva|apaga|quita)( la)? luz nocturna$"), "ajuste_rapido",
     {"ajuste": "luz_nocturna", "estado": "apagar"}),
    (re.compile(r"^(cambia|pon|siguiente|otro)( el)? (fondo|fondo de pantalla|wallpaper)( siguiente)?$"),
     "apariencia", {"accion": "siguiente_fondo"}),
    (re.compile(r"^(quita|oculta|esconde|muestra|pon|ensena|vuelve a poner|activa|desactiva)( la)? barra"
                r"( de arriba| superior| de omarchy| de tareas)?$"), "apariencia", {"accion": "barra"}),
    (re.compile(r"^(copia|copiame|saca|extrae|lee|captura)( el| un)? texto( de| en)?( la| una)? ?(pantalla|zona|imagen)?$"),
     "captura_pantalla", {"modo": "texto"}),
    (re.compile(r"^(copia|copiame|saca|elige|captura)( el| un)? color( de la pantalla)?$"),
     "captura_pantalla", {"modo": "color"}),
    (re.compile(r"^(pon|cambia|ponme)( me)?( un| el)? (otro|nuevo) tema$|^cambia (el|de) tema$"), "apariencia",
     {"accion": "tema_al_azar"}),
    (re.compile(r"^(que|cual|como se llama el) tema (tengo|es|uso|esta puesto|hay|es este)"), "apariencia",
     {"accion": "tema_actual"}),
    (re.compile(r"^(que|cuales) temas (tengo|hay)"), "apariencia", {"accion": "listar_temas"}),
    (re.compile(r"^cierra todas (las )?(ventanas|aplicaciones|apps)$"), "escritorio", {"accion": "cerrar_todas"}),
    (re.compile(r"^(abre|abreme|muestrame|ensename)( el)? (administrador|monitor|gestor) de (tareas|actividad|sistema"
                r"|recursos)$"), "abrir_panel", {"panel": "actividad"}),
    # "Abre la terminal" acababa en abrir_carpeta.
    (re.compile(r"^(abre|abreme|lanza|inicia|dame)( la| una| el| un)? (terminal|consola|navegador)( nueva| nuevo)?$"),
     "abrir_aplicacion", lambda m: {"nombre": m[3]}),
    # "Abre ChatGPT/YouTube/WhatsApp" acababa en un chat de WhatsApp o en reproducir: si el nombre es
    # exactamente una app instalada, se abre sin pasar por el modelo.
    (re.compile(r"^(abre|abreme|lanza|inicia|ejecuta)( la| el| mi| una| un)? (?P<a>.+?)( por favor)?$"),
     "abrir_aplicacion", lambda m: {"nombre": m["a"]} if _es_app(m["a"]) else None),
    # "Pon el tema X" también es poner una canción: solo si X es exactamente un tema instalado.
    (re.compile(r"^(pon|cambia|cambiame)( el)? tema( de omarchy| del escritorio)?( a| al)? (?P<t>.+)$"), "apariencia",
     lambda m: _tema_rapido(m["t"])),
]

RULES = """
- Omarchy: "ve al escritorio 3", "manda esta ventana al 2", "flota esta ventana" usan escritorio.
- "Cambia el tema del escritorio a Nord", "cambia el fondo", "quita la barra" usan apariencia ("pon el tema X" casi siempre es una canción: reproducir); "luz nocturna", "no molestar", "Bluetooth", "wifi", "modo ahorro" usan ajuste_rapido.
- "Abre mi agente" (el agente de IA de Omarchy) usa abrir_aplicacion con "agente", nunca abrir_panel.
- "Abre el menú", "abre el wifi", "enséñame los atajos", "abre el portapapeles" usan abrir_panel.
- "Haz una captura", "copia el texto de la pantalla", "copia un color" usan captura_pantalla; "graba la pantalla" usa grabar_pantalla.
- "¿Cuánta memoria/disco/batería me queda?" usan estado_equipo. Apagar, reiniciar o suspender usan energia."""
