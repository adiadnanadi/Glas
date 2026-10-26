const $ = (s) => document.querySelector(s);

const textEl = $("#text");
const langEl = $("#lang");
const langRow = $("#langRow");
const voiceEl = $("#voice");
const engineEl = $("#engine");
const engineInfo = $("#engineInfo");
const speakBtn = $("#speakBtn");
const stopBtn = $("#stopBtn");
const clearBtn = $("#clearBtn");
const copyBtn = $("#copyBtn");
const downloadBtn = $("#downloadBtn");
const statusEl = $("#status");
const statusText = $("#statusText");
const charCount = $("#charCount");
const voiceInfo = $("#voiceInfo");
const historyEl = $("#history");

const rateEl = $("#rate"), pitchEl = $("#pitch"), volumeEl = $("#volume");
const rateVal = $("#rateVal"), pitchVal = $("#pitchVal"), volumeVal = $("#volumeVal");

let info = { engine: "webspeech", voices: [] };
let audio = null;
let lastUrl = "";
let history = [];
let speaking = false;
let attempt = 0;      // svaki novi pokušaj poništava događaje prethodnog
let watchdog = null;  // ako zvuk ne krene, prekidamo i pokušavamo ponovo
let slowTimer = null;
let warmTimer = null;

const STATUS = {
  idle: ["idle", "Spreman"],
  work: ["speaking", "Motam glas…"],
  play: ["speaking", "Govorim…"],
  done: ["done", "Završeno"],
  stop: ["idle", "Zaustavljeno"],
};

function setStatus(key, custom) {
  const [cls, txt] = STATUS[key] || STATUS.idle;
  statusEl.className = "status " + cls;
  statusText.textContent = custom || txt;
}

function updateCount() {
  charCount.textContent = `${textEl.value.length} znakova`;
}

function updateSliders() {
  const r = parseInt(rateEl.value, 10);
  const p = parseInt(pitchEl.value, 10);
  rateVal.textContent = (r > 0 ? "+" : "") + r + "%";
  pitchVal.textContent = (p > 0 ? "+" : "") + p + " Hz";
  volumeVal.textContent = Math.round(volumeEl.value * 100) + "%";
}

function rateParam() {
  const r = parseInt(rateEl.value, 10);
  return (r >= 0 ? "+" : "") + r + "%";
}
function pitchParam() {
  const p = parseInt(pitchEl.value, 10);
  return (p >= 0 ? "+" : "") + p + "Hz";
}

function currentVoice() {
  return info.voices.find((v) => v.id === voiceEl.value) || info.voices[0];
}

function currentLang() {
  const l = (info.languages || []).find((x) => x.id === langEl.value);
  return l || { id: "bs", name: "Bosanski" };
}

function updateVoiceInfo() {
  const v = currentVoice();
  if (!v) return;
  const engineName =
    info.engine === "edge" ? "Microsoft neural · 96 kbps" :
    info.engine === "google" ? "rezervni Google glas" : "lokalni pregovornik";
  const langName = langRow && langRow.style.display !== "none" ? currentLang().name.toLowerCase() + " · " : "";
  voiceInfo.textContent = `${v.label} · ${langName}${engineName}`;
}

/* ---------------- pokretanje ---------------- */
async function init() {
  try {
    const r = await fetch("/api/info", { cache: "no-store" });
    if (!r.ok) throw new Error("http");
    info = await r.json();
  } catch (e) {
    info = { engine: "webspeech", voices: [], languages: [] };
  }

  if (info.engine === "webspeech") {
    setupWebSpeech();
  } else {
    if (langRow && info.languages) {
      langRow.style.display = "";
      langEl.innerHTML = "";
      info.languages.forEach((l) => {
        const o = document.createElement("option");
        o.value = l.id;
        o.textContent = l.name + " (" + l.id.toUpperCase() + ")";
        langEl.appendChild(o);
      });
      langEl.value = "bs";
    }

    voiceEl.innerHTML = "";
    const groups = [
      ["domaci", "Domaći (tačan izgovor)"],
      ["univerzalni", "Univerzalni (najprirodniji zvuk)"],
    ];
    groups.forEach(([gid, title]) => {
      const items = info.voices.filter((v) => v.group === gid);
      if (!items.length) return;
      const og = document.createElement("optgroup");
      og.label = title;
      items.forEach((v) => {
        const o = document.createElement("option");
        o.value = v.id;
        o.textContent = v.label;
        og.appendChild(o);
      });
      voiceEl.appendChild(og);
    });
    voiceEl.value = "en-US-AvaMultilingualNeural";

    engineEl.textContent = info.engine === "edge" ? "neural 96 kbps" : "rezervni glas";
    engineEl.className = "badge " + info.engine;
    engineInfo.textContent =
      info.engine === "edge"
        ? "Server: Microsoft neural glasovi + 96 kbps kvalitet — bez API ključa."
        : "Server: rezervni Google glas (instaliraj edge-tts za najrealnije zvukove).";
  }
  updateVoiceInfo();
}

/* ---------------- fallback: Web Speech API ---------------- */
const HR_SR_BS = ["bs", "hr", "sr"];

function setupWebSpeech() {
  if (langRow) langRow.style.display = "none";
  const hint = document.getElementById("voiceHint");
  if (hint) hint.style.display = "none";
  engineEl.textContent = "lokalni glas";
  engineEl.className = "badge local";
  engineInfo.textContent =
    "Server nije aktivan — koristim ugrađeni glas pregovornika (kvalitetniji zvuk dobijaš preko servera).";

  const fill = () => {
    const all = speechSynthesis.getVoices();
    if (!all.length) return;
    const bcs = all.filter((v) => HR_SR_BS.some((l) => (v.lang || "").toLowerCase().startsWith(l)));
    const list = bcs.length ? bcs : all;
    voiceEl.innerHTML = "";
    list.forEach((v, i) => {
      const o = document.createElement("option");
      o.value = String(i);
      o.textContent = `${v.name} — ${v.lang}`;
      voiceEl.appendChild(o);
    });
    voiceEl.dataset.native = "1";
    updateVoiceInfo();
  };
  fill();
  speechSynthesis.onvoiceschanged = fill;
}

function speakNative(text) {
  speechSynthesis.cancel();
  const idx = voiceEl.dataset.native ? voiceEl.value : 0;
  const all = speechSynthesis.getVoices();
  const bcs = all.filter((v) => HR_SR_BS.some((l) => (v.lang || "").toLowerCase().startsWith(l)));
  const list = bcs.length ? bcs : all;
  const v = list[parseInt(idx, 10)];
  const u = new SpeechSynthesisUtterance(text);
  if (v) { u.voice = v; u.lang = v.lang; }
  u.rate = 1 + parseInt(rateEl.value, 10) / 100;
  u.pitch = 1 + parseInt(pitchEl.value, 10) / 100;
  u.volume = parseFloat(volumeEl.value);
  u.onstart = () => setPlayState(true, "Govorim…");
  u.onend = () => { setPlayState(false, "Završeno"); addHistory(text); };
  u.onerror = (e) => {
    if (e.error === "canceled" || e.error === "interrupted") return;
    setPlayState(false, "Greška: " + e.error);
  };
  setStatus("play");
  speechSynthesis.speak(u);
}

/* ---------------- reproducija preko servera ---------------- */
function buildUrl(text) {
  const v = currentVoice();
  const id = v ? v.id : "native-f";
  return "/api/tts?text=" + encodeURIComponent(text) +
    "&voice=" + encodeURIComponent(id) +
    "&lang=" + encodeURIComponent(currentLang().id) +
    "&rate=" + encodeURIComponent(rateParam()) +
    "&pitch=" + encodeURIComponent(pitchParam());
}

function clearTimers() {
  if (watchdog) { clearTimeout(watchdog); watchdog = null; }
  if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
}

function killAudio() {
  if (!audio) return;
  try { audio.pause(); } catch (e) {}
  try { audio.removeAttribute("src"); audio.load(); } catch (e) {}
  audio = null;
}

function stopAll() {
  attempt++;                    // poništava sve što još čeka
  killAudio();
  clearTimers();
  if ("speechSynthesis" in window) speechSynthesis.cancel();
  setPlayState(false, "Zaustavljeno");
}

function setPlayState(isSpeaking, label) {
  speaking = isSpeaking;
  speakBtn.disabled = isSpeaking;
  stopBtn.disabled = !isSpeaking;
  setStatus(isSpeaking ? "play" : "idle", label);
}

function fail(msg) {
  clearTimers();
  speaking = false;
  speakBtn.disabled = false;
  stopBtn.disabled = true;
  setStatus("idle", msg);
}

/* Ako je reprodukcija stala: dohvati odgovor servera — ako je zvuk, ipak pusti,
   ako je greška, korisnik vidi pravu poruku na bosanskom. */
async function recover(text) {
  const my = attempt;
  const url = buildUrl(text);
  clearTimers();
  setStatus("work", "Pokušavam ponovo…");
  let r;
  try {
    r = await fetch(url);
  } catch (e) {
    if (my === attempt) fail("Server ne odgovara — je li aplikacija pokrenuta?");
    return;
  }
  if (my !== attempt) return;
  const ct = r.headers.get("content-type") || "";
  if (r.ok && ct.includes("audio")) {
    try {
      const blob = await r.blob();
      if (my !== attempt) return;
      playBlob(URL.createObjectURL(blob), text);
      return;
    } catch (e) {
      fail("Nisam uspio pustiti zvuk — pokušaj ponovo.");
      return;
    }
  }
  let msg = "Ne mogu izgovoriti ovaj tekst — pokušaj sa drugim.";
  if (ct.includes("json")) {
    const j = await r.json().catch(() => null);
    if (j && j.error) msg = j.error;
  }
  if (my === attempt) fail(msg);
}

function playBlob(blobUrl, text) {
  const my = ++attempt;
  killAudio();
  const a = new Audio(blobUrl);
  audio = a;
  a.volume = parseFloat(volumeEl.value);
  a.onplaying = () => { if (my === attempt) setStatus("play"); };
  a.onended = () => {
    if (my !== attempt) return;
    URL.revokeObjectURL(blobUrl);
    speaking = false; speakBtn.disabled = false; stopBtn.disabled = true;
    setStatus("done");
    addHistory(text);
  };
  a.onerror = () => { if (my === attempt) fail("Nisam uspio pustiti zvuk — pokušaj ponovo."); };
  speaking = true;
  speakBtn.disabled = true;
  stopBtn.disabled = false;
  const p = a.play();
  if (p && p.catch) p.catch(() => { if (my === attempt) fail("Pregovornik je blokirao zvuk."); });
}

function play(text, isRetry) {
  if (info.engine === "webspeech") {
    speakNative(text);
    return;
  }

  const my = ++attempt;
  killAudio();
  clearTimers();

  const url = buildUrl(text);
  lastUrl = url;
  downloadBtn.disabled = false;
  speaking = true;
  speakBtn.disabled = true;
  stopBtn.disabled = false;
  setStatus("work", isRetry ? "Ponovo pokušavam…" : "Pripremam glas…");

  // Korisnik vidi da se dešava nešto, iako zvuk još nije krenuo.
  slowTimer = setTimeout(() => {
    if (my === attempt) setStatus("work", "Još trenutak — pripremam glas…");
  }, 4000);

  // Ako zvuk ne krene za 12 sekundi: jedan automatski pokušaj, pa jasna poruka.
  watchdog = setTimeout(() => {
    if (my !== attempt || !speaking) return;
    if (!isRetry) {
      play(text, true);
    } else {
      fail("Govor nije krenuo — provjeri vezu i pritisni Izgovori.");
    }
  }, 12000);

  const a = new Audio();
  audio = a;
  a.preload = "auto";
  a.volume = parseFloat(volumeEl.value);

  let started = false;

  a.onplaying = () => {
    if (my !== attempt) return;
    started = true;
    clearTimers();
    setStatus("play");
  };
  a.onended = () => {
    if (my !== attempt) return;
    clearTimers();
    speaking = false;
    speakBtn.disabled = false;
    stopBtn.disabled = true;
    setStatus("done");
    addHistory(text);
  };
  a.onerror = () => {
    if (my !== attempt) return;
    if (!started) recover(text);
    else fail("Reprodukcija je prekinuta — pokušaj ponovo.");
  };

  a.src = url;
  const p = a.play();
  if (p && p.catch) {
    p.catch(() => {
      if (my === attempt && !started) recover(text);
    });
  }
}

function speak() {
  const txt = textEl.value.trim();
  if (!txt) {
    setStatus("idle", "Unesi rečenicu");
    textEl.focus();
    return;
  }
  if (speaking) stopAll();
  play(txt);
}

function addHistory(txt) {
  if (history[0] === txt) return;
  history.unshift(txt);
  history = history.slice(0, 12);
  renderHistory();
}

function renderHistory() {
  if (!history.length) {
    historyEl.innerHTML = '<li class="empty">Još nema izgovorenih rečenica.</li>';
    return;
  }
  historyEl.innerHTML = "";
  history.forEach((t) => {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "txt";
    span.textContent = t;
    const btn = document.createElement("button");
    btn.className = "play";
    btn.title = "Ponovo izgovori";
    btn.textContent = "▶";
    btn.onclick = () => play(t);
    li.append(btn, span);
    historyEl.appendChild(li);
  });
}

/* ---------------- brzi primjeri ---------------- */
const QUICK = [
  "Zdravo! Ovo je generisan ženski glas za moj školski projekat.",
  "Dobrodošli na prezentaciju naše grupe.",
  "Ova rečenica je izgovorena pomoću vještačke inteligencije.",
  "Danas ćemo vam pokazati kako radi sinteza govora."
];
QUICK.forEach((q) => {
  const b = document.createElement("button");
  b.className = "chip";
  b.textContent = q.length > 40 ? q.slice(0, 40) + "…" : q;
  b.onclick = () => { textEl.value = q; updateCount(); speak(); };
  $("#quick").appendChild(b);
});

/* ---------------- tiho zagrijavanje: glas spreman prije nego pritisneš Enter ------- */
const warmed = new Set();
const warming = new Set();

function maybeWarm() {
  if (!info || info.engine !== "edge") return;
  const txt = textEl.value.trim();
  if (txt.length < 3) return;
  clearTimeout(warmTimer);
  warmTimer = setTimeout(() => {
    const url = buildUrl(txt) + "&warm=1";
    if (warmed.has(url) || warming.has(url)) return;
    if (warmed.size > 80) warmed.clear();
    warming.add(url);
    fetch(url)
      .then((r) => { warming.delete(url); if (r.ok) warmed.add(url); })
      .catch(() => warming.delete(url));
  }, 650);
}

/* ---------------- događaji ---------------- */
textEl.addEventListener("input", () => {
  updateCount();
  maybeWarm();
});
textEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    speak();
  }
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); speak(); }
  if (e.key === "Escape") stopAll();
});

speakBtn.onclick = speak;
stopBtn.onclick = stopAll;
clearBtn.onclick = () => { textEl.value = ""; updateCount(); textEl.focus(); setStatus("idle"); };
copyBtn.onclick = async () => {
  try { await navigator.clipboard.writeText(textEl.value); copyBtn.textContent = "Kopirano!"; }
  catch { copyBtn.textContent = "Greška"; }
  setTimeout(() => (copyBtn.textContent = "Kopiraj"), 1400);
};
downloadBtn.onclick = () => {
  if (!lastUrl) return;
  const a = document.createElement("a");
  a.href = lastUrl;
  a.download = "glas.mp3";
  document.body.appendChild(a);
  a.click();
  a.remove();
};
$("#clearHistory").onclick = () => { history = []; renderHistory(); };
voiceEl.onchange = updateVoiceInfo;
if (langEl) langEl.onchange = updateVoiceInfo;
[rateEl, pitchEl, volumeEl].forEach((el) => el.addEventListener("input", () => {
  updateSliders();
  if (el === volumeEl && audio) audio.volume = parseFloat(volumeEl.value);
}));

updateCount();
updateSliders();
renderHistory();
init();
