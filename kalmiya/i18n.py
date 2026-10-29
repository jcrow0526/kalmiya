"""Textos de la interfaz en español e inglés. `t("clave")` usa el idioma de la configuración."""

LANG = {"es": "es"}  # se actualiza con set_lang()

VOICES = {
    "es": {"es_AR-daniela-high.onnx": "Daniela — español latino, mujer",
           "es_MX-claude-high.onnx": "Claude — español de México"},
    "en": {"en_US-lessac-high.onnx": "Lessac — US English, female"},
}
DEFAULT_VOICE = {"es": "es_AR-daniela-high.onnx", "en": "en_US-lessac-high.onnx"}

T = {
    "listening": ("Escuchando…", "Listening…"),
    "dictating": ("Dictando…", "Dictating…"),
    "transcribing": ("Transcribiendo…", "Transcribing…"),
    "thinking": ("Pensando…", "Thinking…"),
    "not_heard": ("No te escuché", "I didn't hear you"),
    "preparing": ("Kalmiya se está preparando…", "Kalmiya is getting ready…"),
    "loading": ("Cargando LFM3 y Whisper", "Loading LFM3 and Whisper"),
    "ready": ("Kalmiya lista", "Kalmiya is ready"),
    "ready_hint": ("Mantén {k} y habla", "Hold {k} and speak"),
    "ready_notify": ("Lista. Mantén {k} para hablar.", "Ready. Hold {k} to talk."),
    "still_preparing": ("Kalmiya aún se está preparando…", "Kalmiya is still getting ready…"),
    "failed_start": ("Kalmiya no pudo iniciar", "Kalmiya couldn't start"),
    "error": ("Algo salió mal", "Something went wrong"),
    "say_again": ("Vale, dímelo otra vez.", "Okay, say it again."),
    "did_you_say": ("No te entendí bien. ¿Dijiste: {x}?", "I didn't quite catch that. Did you say: {x}?"),
    "listen_answer": ("Te escucho…", "I'm listening…"),
    "answer_hint": ("Responde (o quédate callado para cancelar)", "Answer (or stay quiet to cancel)"),
    "dropped": ("Vale, lo dejamos", "Okay, never mind"),
    "draft_kept": ("No lo envié; quedó escrito en el chat", "Not sent; it's still typed in the chat"),
    "new_conv": ("Nueva conversación", "New conversation"),
    "new_conv_body": ("Olvidé lo anterior", "I forgot the previous context"),
    "reminder": ("Recordatorio", "Reminder"),
    "image_what": ("¿Qué quieres saber?", "What do you want to know?"),
    "image_hint": ("Pregunta sobre la imagen (o calla y te la describo)",
                   "Ask about the image (or stay quiet and I'll describe it)"),
    "image_default": ("¿Qué es esto? Descríbelo brevemente.", "What is this? Describe it briefly."),
    "image_looking": ("Mirando la imagen…", "Looking at the image…"),
    "image_none": ("No pude distinguir nada claro en la imagen.", "I couldn't make out anything clear in the image."),
    "image_error": ("No pude ver la imagen", "I couldn't see the image"),
    "vision_missing": ("Falta el modelo de visión", "The vision model is missing"),
    "vision_missing_body": ("Vuelve a ejecutar el instalador de Kalmiya", "Run the Kalmiya installer again"),
    "settings_saved": ("Configuración guardada", "Settings saved"),
    "settings_saved_body": ("Reinicia Kalmiya si cambiaste el servidor LLM", "Restart Kalmiya if you changed the LLM server"),
    "picker": ("Haz clic en la imagen (o arrastra un recuadro) · Esc para cancelar",
               "Click the image (or drag a box) · Esc to cancel"),
    # bandeja
    "tray_talk": ("{k} para hablar", "{k} to talk"),
    "tray_calendar": ("Calendario", "Calendar"),
    "tray_reminders": ("Recordatorios", "Reminders"),
    "tool_ver_recordatorios": ("Revisando tus recordatorios", "Checking your reminders"),
    "tool_abrir_recordatorios": ("Abriendo recordatorios", "Opening reminders"),
    "tool_resumen_noticias": ("Leyendo tus periódicos", "Reading your newspapers"),
    "tool_abrir_noticia": ("Abriendo la noticia", "Opening the article"),
    "tool_pestana": ("Buscando la pestaña", "Finding the tab"),
    "tray_settings": ("Configuración…", "Settings…"),
    "tray_new": ("Nueva conversación", "New conversation"),
    "tray_welcome": ("Ver presentación", "Show introduction"),
    "tray_downloads": ("Abrir descargas", "Open downloads"),
    "tray_logs": ("Ver registros", "View logs"),
    "tray_quit": ("Salir", "Quit"),
    # herramientas (mientras trabajan)
    "tool_reproducir": ("Buscando la canción", "Finding the song"),
    "tool_youtube_recomendado": ("Abriendo el recomendado", "Opening the recommendation"),
    "tool_controlar_ventana": ("Ajustando la ventana", "Adjusting the window"),
    "tool_whatsapp_abrir_chat": ("Abriendo el chat", "Opening the chat"),
    "tool_whatsapp_mensaje": ("Escribiendo el mensaje", "Writing the message"),
    "tool_clima": ("Consultando el clima", "Checking the weather"),
    "tool_buscar_info": ("Buscando información", "Looking it up"),
    "tool_cerrar_aplicacion": ("Cerrando", "Closing"),
    "tool_responder": ("Pensando", "Thinking"),
    "tool_abrir_web": ("Abriendo navegador", "Opening the browser"),
    "tool_buscar_web": ("Buscando", "Searching"),
    "tool_abrir_aplicacion": ("Abriendo aplicación", "Opening the app"),
    "tool_abrir_carpeta": ("Abriendo carpeta", "Opening the folder"),
    "tool_crear_evento": ("Agendando", "Scheduling"),
    "tool_ver_agenda": ("Revisando tu agenda", "Checking your calendar"),
    "tool_descargar_video": ("Descargando", "Downloading"),
    "tool_controlar_medios": ("Controlando medios", "Controlling media"),
    "tool_captura_pantalla": ("Capturando", "Taking a screenshot"),
    "tool_recordatorio": ("Programando recordatorio", "Setting a reminder"),
    "tool_bloquear_pc": ("Bloqueando", "Locking"),
}


def set_lang(lang: str) -> None:
    LANG["es"] = "en" if (lang or "es").startswith("en") else "es"


def lang() -> str:
    return LANG["es"]


def t(key: str, **kw) -> str:
    es, en = T.get(key, (key, key))
    s = en if LANG["es"] == "en" else es
    return s.format(**kw) if kw else s
