"""Periódicos por RSS: leer titulares, descubrir el feed de una página y recomendaciones."""
import concurrent.futures as cf
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Kalmiya/0.2 (lector RSS)"}

# Recomendados (feeds comprobados). (nombre, url, categoría, idioma)
CATALOG = [
    ("El Universal", "https://www.eluniversal.com.mx/arc/outboundfeeds/rss/?outputType=xml", "México", "es"),
    ("El Financiero", "https://www.elfinanciero.com.mx/arc/outboundfeeds/rss/?outputType=xml", "Negocios", "es"),
    ("Expansión", "https://expansion.mx/rss", "Negocios", "es"),
    ("Infobae", "https://www.infobae.com/arc/outboundfeeds/rss/", "Internacional", "es"),
    ("El País", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada", "Internacional", "es"),
    ("El País México", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/mexico/portada", "México", "es"),
    ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml", "Internacional", "es"),
    ("France 24 Español", "https://www.france24.com/es/rss", "Internacional", "es"),
    ("DW Español", "https://rss.dw.com/xml/rss-sp-all", "Internacional", "es"),
    ("Xataka México", "https://www.xataka.com.mx/index.xml", "Tecnología", "es"),
    ("Xataka", "https://www.xataka.com/index.xml", "Tecnología", "es"),
    ("Hipertextual", "https://hipertextual.com/feed", "Tecnología", "es"),
    ("ESPN Deportes", "https://www.espn.com.mx/espn/rss/news", "Deportes", "es"),
    ("Marca", "https://e00-marca.uecdn.es/rss/portada.xml", "Deportes", "es"),
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml", "World", "en"),
    ("The New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "World", "en"),
    ("NPR News", "https://feeds.npr.org/1001/rss.xml", "World", "en"),
    ("The Verge", "https://www.theverge.com/rss/index.xml", "Tech", "en"),
    ("TechCrunch", "https://techcrunch.com/feed/", "Tech", "en"),
]


@dataclass
class Item:
    fuente: str
    titulo: str
    resumen: str
    link: str


def _clean(s: str | None) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _parse(data: bytes, fuente: str) -> list[Item]:
    items = []
    try:
        root = ET.fromstring(data)
        atom = "{http://www.w3.org/2005/Atom}"
        for it in root.findall(".//item") or root.findall(f".//{atom}entry"):
            title = it.findtext("title") or it.findtext(f"{atom}title")
            desc = it.findtext("description") or it.findtext(f"{atom}summary") or it.findtext(f"{atom}content")
            link = it.findtext("link") or ""
            if not link:
                el = it.find(f"{atom}link")
                link = el.get("href", "") if el is not None else ""
            items.append(Item(fuente, _clean(title), _clean(desc), link.strip()))
    except ET.ParseError:
        # Muchos periódicos publican XML mal formado: se leen los <item> con expresiones regulares.
        text = data.decode("utf-8", "replace")
        for block in re.findall(r"<item\b.*?</item>", text, re.S | re.I):
            def tag(name):
                m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", block, re.S | re.I)
                return re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip()) if m else ""
            items.append(Item(fuente, _clean(tag("title")), _clean(tag("description")), _clean(tag("link"))))
    return [i for i in items if i.titulo]


_cache: dict[str, tuple[float, list[Item]]] = {}


def fetch(nombre: str, url: str, max_age: float = 600) -> list[Item]:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    r = httpx.get(url, headers=UA, timeout=12, follow_redirects=True)
    r.raise_for_status()
    items = _parse(r.content, nombre)
    _cache[url] = (time.time(), items)
    return items


def headlines(feeds: list[dict], per_feed: int = 4) -> list[Item]:
    """Titulares de varias fuentes en paralelo (las que fallan se omiten)."""
    out = []
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(fetch, f["nombre"], f["url"]): f for f in feeds}
        for fut in cf.as_completed(futs):
            try:
                out.extend(fut.result()[:per_feed])
            except Exception as e:
                log.warning("No pude leer %s: %s", futs[fut]["nombre"], e)
    order = {f["nombre"]: i for i, f in enumerate(feeds)}
    return sorted(out, key=lambda i: order.get(i.fuente, 99))


def discover(url: str) -> tuple[str, str]:
    """Acepta un feed o la portada de un periódico; devuelve (nombre, url del feed)."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.strip()
    r = httpx.get(url, headers=UA, timeout=12, follow_redirects=True)
    r.raise_for_status()
    items = _parse(r.content, "")
    if items:  # ya es un feed
        m = re.search(rb"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", r.content, re.S)
        name = _clean(m.group(1).decode("utf-8", "replace")) if m else httpx.URL(url).host
        return name[:40], str(r.url)
    page = r.text
    for m in re.finditer(r"<link\b[^>]*>", page, re.I):
        tag = m.group(0)
        if re.search(r"application/(rss|atom)\+xml", tag, re.I):
            href = re.search(r'href=["\']([^"\']+)', tag, re.I)
            if href:
                title = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
                name = _clean(title.group(1)).split("|")[0].split(" - ")[0] if title else httpx.URL(url).host
                return name[:40], urljoin(str(r.url), html.unescape(href.group(1)))
    # La página no anuncia su feed: probar las rutas habituales.
    title = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    name = _clean(title.group(1)).split("|")[0].split(" - ")[0] if title else httpx.URL(url).host
    for path in ("/feed/", "/rss", "/rss.xml", "/feed.xml", "/index.xml", "/rss/portada.xml"):
        try:
            cand = urljoin(str(r.url), path)
            rr = httpx.get(cand, headers=UA, timeout=8, follow_redirects=True)
            if rr.status_code == 200 and _parse(rr.content, ""):
                return name[:40], str(rr.url)
        except httpx.HTTPError:
            continue
    raise ValueError("No encontré un feed RSS en esa página")
