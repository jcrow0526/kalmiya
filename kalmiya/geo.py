"""Ciudad aproximada del usuario (para el clima) a partir de su IP pública.

No necesita permisos de ubicación de Windows; la precisión es de ciudad, a veces la de su
proveedor de internet. Se usa solo si el usuario no escribió una ciudad.
"""
import logging

import httpx

log = logging.getLogger(__name__)


def detect_city() -> str:
    for url, key in (("https://ipapi.co/json/", "city"), ("http://ip-api.com/json/?lang=es&fields=city", "city")):
        try:
            r = httpx.get(url, timeout=6, headers={"User-Agent": "Kalmiya/0.2"})
            r.raise_for_status()
            city = (r.json().get(key) or "").strip()
            if city:
                return city
        except Exception as e:
            log.debug("Sin ciudad desde %s: %s", url, e)
    return ""
