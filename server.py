#!/usr/bin/env python3
"""Lokalni server za generator realnog ženskog glasa (bez API ključeva)."""
import asyncio
import hashlib
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8765"))
# Render (i slični hosting) traži 0.0.0.0 — lokalno ostajemo na 127.0.0.1.
ON_RENDER = bool(os.environ.get("RENDER"))
HOST = os.environ.get("HOST") or ("0.0.0.0" if ON_RENDER else "127.0.0.1")

LANGUAGES = [
    {"id": "bs", "name": "Bosanski", "locale": "bs-BA", "tl": "sr",
     "native_f": "bs-BA-VesnaNeural", "native_m": "bs-BA-GoranNeural"},
    {"id": "hr", "name": "Hrvatski", "locale": "hr-HR", "tl": "hr",
     "native_f": "hr-HR-GabrijelaNeural", "native_m": "hr-HR-SreckoNeural"},
    {"id": "sr", "name": "Srpski", "locale": "sr-RS", "tl": "sr",
     "native_f": "sr-RS-SophieNeural", "native_m": "sr-RS-NicholasNeural"},
]

VOICES = [
    {"id": "native-f", "label": "Domaći ženski (Vesna · Gabrijela · Sophie)",
     "short": "Domaći (ž)", "gender": "f", "group": "domaci"},
    {"id": "en-US-AvaMultilingualNeural", "label": "Ava — razgovorni, najprirodniji",
     "short": "Ava (ž)", "gender": "f", "group": "univerzalni"},
    {"id": "en-US-EmmaMultilingualNeural", "label": "Emma — vedar i jasan",
     "short": "Emma (ž)", "gender": "f", "group": "univerzalni"},
    {"id": "native-m", "label": "Domaći muški (Goran · Srećko · Nikola)",
     "short": "Domaći (m)", "gender": "m", "group": "domaci"},
    {"id": "en-US-AndrewMultilingualNeural", "label": "Andrew — muški",
     "short": "Andrew (m)", "gender": "m", "group": "univerzalni"},
]

# Rezervni (Google) glas: ženski gdje postoji
GOOGLE_TL_FEMALE = {"bs": "sr", "hr": "hr", "sr": "sr"}
GOOGLE_TL_MALE = {"bs": "bs", "hr": "bs", "sr": "bs"}

HIGH_QUALITY_FORMAT = "audio-24khz-96kbitrate-mono-mp3"
DEFAULT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"

# Koliko smije da traje jedan pokušaj sinteze — da nikad ne zablokira pokretanje.
EDGE_TIMEOUT = 25
WARM_TIMEOUT = 90
WARMING = set()
WARM_LOCK = threading.Lock()

try:
    import edge_tts
    import edge_tts.communicate as edge_comm
    ENGINE = "edge"
except Exception:
    edge_tts = None
    edge_comm = None
    ENGINE = "google"

_ctx = threading.local()
CACHE = {}
CACHE_MAX = 80
CACHE_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# priprema kvalitetnijeg zvuka i pravilnog jezika (SSML)
# --------------------------------------------------------------------------
def _install_edge_patches():
    """96 kbps audio + ispravan xml:lang (bitno za izgovor višejezičnih glasova)."""
    try:
        import aiohttp
        original_send = aiohttp.ClientWebSocketResponse.send_str

        async def send_with_better_format(self, data, *args, **kwargs):
            if isinstance(data, str) and "speech.config" in data and DEFAULT_FORMAT in data:
                data = data.replace(DEFAULT_FORMAT, HIGH_QUALITY_FORMAT)
            return await original_send(self, data, *args, **kwargs)

        aiohttp.ClientWebSocketResponse.send_str = send_with_better_format
    except Exception:
        pass

    original_mkssml = edge_comm.mkssml

    def mkssml_with_lang(tc, escaped_text):
        lang = getattr(_ctx, "lang", None)
        if not lang:
            return original_mkssml(tc, escaped_text)
        if isinstance(escaped_text, bytes):
            escaped_text = escaped_text.decode("utf-8")
        return (
            "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            f"xml:lang='{lang}'><voice name='{tc.voice}'>"
            f"<prosody pitch='{tc.pitch}' rate='{tc.rate}' volume='{tc.volume}'>"
            f"{escaped_text}"
            "</prosody></voice></speak>"
        )

    edge_comm.mkssml = mkssml_with_lang


if ENGINE == "edge":
    _install_edge_patches()


# --------------------------------------------------------------------------
# tekst
# --------------------------------------------------------------------------
def normalize_text(text):
    return " ".join(text.split())


def split_sentences(text):
    out, buf = [], ""
    for ch in text:
        buf += ch
        if ch in ".!?;:\n":
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out or [text]


def split_text(text, limit=700):
    text = normalize_text(text)
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for sentence in split_sentences(text):
        if len(buf) + len(sentence) + 1 <= limit:
            buf = (buf + " " + sentence).strip()
        else:
            if buf:
                parts.append(buf)
            buf = sentence
    if buf:
        parts.append(buf)
    return [p for p in parts if p]


def cache_get(key):
    with CACHE_LOCK:
        return CACHE.get(key)


def cache_put(key, data):
    if not data:
        return
    with CACHE_LOCK:
        if key not in CACHE and len(CACHE) >= CACHE_MAX:
            CACHE.pop(next(iter(CACHE)))
        CACHE[key] = data


def friendly_error(err):
    s = str(err) or err.__class__.__name__
    low = s.lower()
    if "no audio was received" in low:
        return "Servis nije vratio zvuk za ovaj tekst — promijeni tekst ili pokušaj ponovo."
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)) or "timeout" in low:
        return "Izgovor je trajao predugo — pokušaj ponovo."
    if "name resolution" in low or "cannot connect" in low or "connection" in low or "network" in low:
        return "Nema veze sa internetom — provjeri vezu i pokušaj ponovo."
    if "401" in low or "403" in low or "unauthorized" in low:
        return "Servis trenutno odbija zahtjev — pokušaj za par sekundi."
    return "Greška pri izgovoru: " + s[:140]


def synth_edge_bytes(text, azure_voice, locale, rate, pitch):
    """Cijela sinteza u bafer (za zagrijavanje prije nego korisnik pritisne Enter)."""
    out = []

    async def run():
        for part in split_text(text, 700):
            _ctx.lang = locale
            com = edge_tts.Communicate(part, azure_voice, rate=rate, pitch=pitch)
            async for chunk in com.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    out.append(chunk["data"])

    asyncio.run(asyncio.wait_for(run(), WARM_TIMEOUT))
    return b"".join(out)


def google_audio(text, tl):
    chunks = split_text(text, limit=190)
    out = []
    for c in chunks:
        url = ("https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob"
               "&q=" + urllib.parse.quote(c) + "&tl=" + tl)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
        if not data.startswith(b"\xff") and not data.startswith(b"ID3"):
            raise RuntimeError("rezervni glas nije dostupan")
        out.append(data)
    return b"".join(out)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GeneratorGlasa/2.0"
    _headers_sent = False

    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._headers_sent = False
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self.send_json({"status": "ok", "engine": ENGINE,
                                   "cached": len(CACHE), "host": HOST})
        if parsed.path == "/api/info":
            return self.send_json({
                "engine": ENGINE,
                "quality": HIGH_QUALITY_FORMAT if ENGINE == "edge" else None,
                "languages": [{"id": l["id"], "name": l["name"]} for l in LANGUAGES],
                "voices": VOICES,
                "port": PORT,
            })
        if parsed.path == "/api/tts":
            return self.handle_tts(parsed)
        self.serve_static(parsed.path)

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = urllib.parse.unquote(path.lstrip("/"))
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT) or not os.path.isfile(full):
            return self.send_json({"error": "nema fajla"}, 404)
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".mp3": "audio/mpeg",
        }
        self.send_file(full, types.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))

    def resolve_voice(self, voice_id, lang_id):
        lang = next((l for l in LANGUAGES if l["id"] == lang_id), LANGUAGES[0])
        if voice_id == "native-f":
            return lang["native_f"], lang["locale"], "f", lang
        if voice_id == "native-m":
            return lang["native_m"], lang["locale"], "m", lang
        meta = next((v for v in VOICES if v["id"] == voice_id), VOICES[0])
        return meta["id"], lang["locale"], meta["gender"], lang

    def handle_tts(self, parsed):
        q = urllib.parse.parse_qs(parsed.query)
        text = normalize_text((q.get("text", [""])[0] or ""))
        if not text:
            return self.send_json({"error": "prazan tekst"}, 400)

        voice_id = q.get("voice", [""])[0] or "native-f"
        lang_id = q.get("lang", ["bs"])[0] or "bs"
        rate = q.get("rate", ["+0%"])[0] or "+0%"
        pitch = q.get("pitch", ["+0Hz"])[0] or "+0Hz"
        if not rate.startswith(("+", "-")) or not rate.endswith("%"):
            rate = "+0%"
        if not pitch.startswith(("+", "-")) or not pitch.endswith("Hz"):
            pitch = "+0Hz"

        azure_voice, locale, gender, lang = self.resolve_voice(voice_id, lang_id)
        key = hashlib.md5(f"{text}|{azure_voice}|{locale}|{rate}|{pitch}".encode()).hexdigest()

        cached = cache_get(key)
        if cached is not None:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(cached)))
            self.send_header("X-Engine", ENGINE)
            self.send_header("X-Voice", azure_voice)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cached)
            return

        if q.get("warm", ["0"])[0] == "1":
            return self.handle_warm(key, text, azure_voice, locale, rate, pitch)

        try:
            if ENGINE == "edge":
                self.stream_edge(text, azure_voice, locale, rate, pitch, key)
            else:
                tl = (GOOGLE_TL_FEMALE if gender == "f" else GOOGLE_TL_MALE).get(lang_id, "hr")
                self.stream_google(text, tl, key)
        except Exception as e:
            print(f"[greska] {e}", flush=True)
            if self._headers_sent:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                except Exception:
                    pass
                return
            # Ako je Edge nedostupan (npr. blokiran iz datacentra na Render-u),
            # pokušaj rezervni glas prije nego vratiš grešku.
            if ENGINE == "edge" and "no audio was received" not in str(e).lower():
                try:
                    tl = (GOOGLE_TL_FEMALE if gender == "f" else GOOGLE_TL_MALE).get(lang_id, "hr")
                    self.stream_google(text, tl, key)
                    print("[info] korišten rezervni Google glas", flush=True)
                    return
                except Exception as e2:
                    print(f"[greska] rezervni glas: {e2}", flush=True)
                    e = e2
            self.send_json({"error": friendly_error(e)}, 502)

    def handle_warm(self, key, text, azure_voice, locale, rate, pitch):
        """Tiho pripremi glas unaprijed — da Enter odmah pusti zvuk."""
        if ENGINE != "edge":
            return self.send_json({"ok": True, "warmed": False})
        with WARM_LOCK:
            if key in WARMING:
                return self.send_json({"ok": True, "warmed": "u toku"})
            WARMING.add(key)
        try:
            data = synth_edge_bytes(text, azure_voice, locale, rate, pitch)
            if data:
                cache_put(key, data)
            return self.send_json({"ok": True, "bytes": len(data)})
        except Exception as e:
            return self.send_json({"error": friendly_error(e)}, 502)
        finally:
            with WARM_LOCK:
                WARMING.discard(key)

    def start_audio(self, engine):
        self._headers_sent = True
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("X-Engine", engine)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def write_chunk(self, data):
        if data:
            self.wfile.write(b"%X\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

    def end_chunks(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def stream_edge(self, text, azure_voice, locale, rate, pitch, key):
        """Sinteza po dijelovima: timeout po SVAKOM pokušaju i sigurno ponavljanje,
        pa nikad ne zablokira pokretanje i nikad ne vraća prazan odgovor."""
        parts = split_text(text, 700)
        collected = []
        started = False
        offset = 0
        last_error = None
        _ctx.lang = locale

        async def consume(part):
            """Strimuje jedan dio i odmah ga šalje; `offset` pamti već poslano."""
            nonlocal started, offset
            com = edge_tts.Communicate(part, azure_voice, rate=rate, pitch=pitch)
            async for chunk in com.stream():
                if chunk.get("type") != "audio":
                    continue
                data = chunk.get("data") or b""
                if not data:
                    continue
                if offset:
                    cut = min(len(data), offset)
                    data = data[cut:]
                    offset -= cut
                    if not data:
                        continue
                if not started:
                    self.start_audio("edge")
                    started = True
                self.write_chunk(data)
                collected.append(data)

        complete = False
        for idx, part in enumerate(parts):
            offset = 0
            for _attempt in range(3):
                try:
                    asyncio.run(asyncio.wait_for(consume(part), EDGE_TIMEOUT))
                    last_error = None
                    break
                except (BrokenPipeError, ConnectionResetError):
                    raise
                except OSError as e:
                    if e.errno in (32, 54, 9):   # prekinuta veza — nema ponavljanja
                        raise
                    last_error = e
                    print(f"[greska] dio {idx + 1}/{len(parts)} (pokušaj): {e}", flush=True)
                    continue
                except Exception as e:
                    last_error = e
                    print(f"[greska] dio {idx + 1}/{len(parts)} (pokušaj): {e}", flush=True)
                    continue
            else:
                # sva tri pokušaja za ovaj dio su propala
                if not started:
                    raise last_error or RuntimeError("Servis nije vratio zvuk.")
                print(f"[greska] odustajem nakon dijela {idx + 1}/{len(parts)}", flush=True)
                break
            if idx == len(parts) - 1:
                complete = True

        if not started:
            raise last_error or RuntimeError("Servis nije vratio zvuk.")
        try:
            self.end_chunks()
        except Exception:
            pass
        if complete:
            cache_put(key, b"".join(collected))

    def stream_google(self, text, tl, key):
        data = google_audio(text, tl)
        self.start_audio("google")
        self.write_chunk(data)
        self.end_chunks()
        cache_put(key, data)


def warm_up_network():
    """Prvo spajanje na servis odmah na startu — da prvi Enter ne čeka rukovanje."""
    if ENGINE != "edge":
        return
    try:
        data = synth_edge_bytes("Zdravo.", "en-US-AvaMultilingualNeural", "en-US", "+0%", "+0Hz")
        if data:
            print("[spremno] Veza sa servisom za govor uspostavljena.", flush=True)
    except Exception as e:
        print(f"[upozorenje] Servis za govor trenutno nije dostupan: {friendly_error(e)}", flush=True)


class QuietServer(ThreadingHTTPServer):
    """Prekid reprodukcije (Stop dugme) nije greška — ne štampamo traceback."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            print("[greska] korisnik je prekinuo reprodukciju", flush=True)
            return
        super().handle_error(request, client_address)


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    try:
        srv = QuietServer((HOST, PORT), Handler)
    except OSError:
        print(f"Port {PORT} je zauzet, pokreni sa: PORT={PORT + 1} python3 server.py")
        sys.exit(1)

    local_url = f"http://127.0.0.1:{PORT}"
    print("=" * 60)
    print("  Generator realnog ženskog glasa")
    print(f"  Otvori u pregovorniku:  {local_url}")
    print(f"  Slušam:                 {HOST}:{PORT}")
    print(f"  Motor : {'Microsoft neural + 96 kbps' if ENGINE == 'edge' else 'rezervni Google glas'}")
    if ON_RENDER:
        print(f"  Hosting: Render  {os.environ.get('RENDER_EXTERNAL_URL', '')}")
    print("  Zaustavi: Ctrl+C")
    print("=" * 60)
    if not ON_RENDER and os.environ.get("NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(local_url)).start()
    threading.Thread(target=warm_up_network, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nGasim server...")
        srv.shutdown()


if __name__ == "__main__":
    main()
