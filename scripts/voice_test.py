"""Voice bake-off: the same Nepali text through every candidate voice.

Output (out/voicetest/): one wav per voice, timing.txt, and voice_test.mp4 where each voice plays
behind a big letter (A, B, C ...) so the owner can compare by ear without knowing which is which.
key.txt maps letters to voices. Every engine is optional: if one fails to install or run, it is skipped.
"""
import asyncio
import base64
import json
import os
import random
import subprocess
import time
import traceback
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

OUT = Path("out/voicetest")
OUT.mkdir(parents=True, exist_ok=True)
TEXT = ("भूकम्प आउँदा नआत्तिनुहोस्। सबैभन्दा पहिला घुँडा टेकेर बस्नुहोस्, टाउको छोप्नुहोस् र बलियो टेबुलको खुट्टा समात्नुहोस्। "
        "हल्लाइ रोकिएपछि मात्र खुला ठाउँमा जानुहोस्, लिफ्ट प्रयोग नगर्नुहोस्। "
        "घरमा तीन दिनलाई पुग्ने पानी, सुख्खा खाना र टर्च तयार राख्नुहोस्। "
        "आपत्कालीन अवस्थामा प्रहरीको एक सय नम्बरमा फोन गर्नुहोस्।")
FONT = "assets/fonts/NotoSans.ttf"
results = []  # (name, wav, seconds_to_make)


def run(name, fn):
    wav = OUT / f"{name}.wav"
    t = time.time()
    try:
        fn(wav)
        if wav.exists() and wav.stat().st_size > 10_000:
            results.append((name, wav, time.time() - t))
            print(f"[ok] {name} {time.time() - t:.1f}s")
        else:
            print(f"[skip] {name}: no audio")
    except Exception as e:
        print(f"[fail] {name}: {e}")
        traceback.print_exc(limit=1)


def to_wav(src, wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "24000", "-ac", "1", str(wav)], check=True)


# ---- Gemini voices. Text only; anchor direction goes in speech_metadata.style (never in the text) ----
STYLE = "Calm, confident Nepali television news anchor; steady medium pace; clear diction; warm but neutral."


def gemini(voice, model, style=True):
    def f(wav):
        key = os.environ["GEMINI_API_KEY"]
        part = {"text": TEXT}
        if style:
            part["speech_metadata"] = {"style": STYLE}
        body = {"contents": [{"parts": [part]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
        for attempt in range(3):
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                              json=body, timeout=180)
            if r.status_code == 400 and style and "speech_metadata" in r.text:
                print("  speech_metadata not accepted -> plain text"); part.pop("speech_metadata", None); continue
            if r.status_code == 429:
                time.sleep(30 * (attempt + 1)); continue
            r.raise_for_status()
            inline = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
            raw = base64.b64decode(inline["data"])
            if "wav" in inline.get("mimeType", ""):
                wav.write_bytes(raw)
            else:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1",
                                "-i", "pipe:0", str(wav)], input=raw, check=True)
            return
        raise RuntimeError(f"{model} {voice}: {r.status_code} {r.text[:120]}")
    return f


# ---- kala-tts (Ampixa, CPU, MIT code / CC-BY-SA weights) ----
def kala(speaker):
    def f(wav):
        import kala_tts
        try:
            kala_tts.synthesize_to_file(TEXT, str(wav), speaker=speaker)
        except TypeError:
            wav.write_bytes(kala_tts.synthesize(TEXT, speaker=speaker))
    return f


# ---- Indic Parler-TTS (AI4Bharat, Apache-2.0) ----
def parler(wav):
    import torch
    import soundfile as sf
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer
    name = "ai4bharat/indic-parler-tts"
    model = ParlerTTSForConditionalGeneration.from_pretrained(name)
    tok = AutoTokenizer.from_pretrained(name)
    dtok = AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path)
    desc = ("Amrita speaks in a clear, calm and confident news-reading tone at a moderate pace. "
            "The recording is of very high quality with no background noise.")
    d = dtok(desc, return_tensors="pt")
    p = tok(TEXT, return_tensors="pt")
    torch.set_num_threads(2)
    with torch.no_grad():
        audio = model.generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                               prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask)
    sf.write(str(wav), audio.cpu().numpy().squeeze(), model.config.sampling_rate)


# ---- Piper ne_NP (rhasspy voices) ----
def piper(wav):
    from huggingface_hub import hf_hub_download
    base = "ne/ne_NP/google/medium/ne_NP-google-medium"
    onnx = hf_hub_download("rhasspy/piper-voices", base + ".onnx")
    hf_hub_download("rhasspy/piper-voices", base + ".onnx.json")
    subprocess.run(["piper", "--model", onnx, "--output_file", str(wav)], input=TEXT.encode(), check=True)


# ---- edge-tts baseline ----
def edge(wav):
    import edge_tts
    mp3 = wav.with_suffix(".mp3")
    asyncio.run(edge_tts.Communicate(TEXT, "ne-NP-HemkalaNeural").save(str(mp3)))
    to_wav(mp3, wav)


only = os.getenv("ENGINES", "gemini,kala,piper,edge,parler").split(",")
if "gemini" in only:
    run("gemini_lite_Aoede_current", gemini("Aoede", "gemini-3.8-flash-lite-tts", style=False)); time.sleep(8)
    for v in ["Aoede", "Kore", "Erinome", "Sulafat", "Charon"]:
        run(f"gemini_flash_{v}_styled", gemini(v, "gemini-3.8-flash-tts")); time.sleep(8)
if "kala" in only:
    for sp in ["kala", "barsha"]:
        run(f"kala_{sp}", kala(sp))
if "piper" in only:
    run("piper_google_medium", piper)
if "edge" in only:
    run("edge_hemkala", edge)
if "parler" in only:
    run("indic_parler_amrita", parler)

# ---- blind comparison video ----
random.seed(int(time.time()))
order = results[:]
random.shuffle(order)
letters = "ABCDEFGHIJKLMNOP"
key, segs = [], []
for (name, wav, secs), L in zip(order, letters):
    key.append(f"{L} = {name}  (made in {secs:.0f}s, audio {float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(wav)],capture_output=True,text=True).stdout):.1f}s)")
    im = Image.new("RGB", (1080, 1920), (20, 26, 44))
    d = ImageDraw.Draw(im)
    d.text((540, 820), L, font=ImageFont.truetype(FONT, 520), fill=(246, 241, 231), anchor="mm")
    d.text((540, 1250), "Voice test", font=ImageFont.truetype(FONT, 70), fill=(220, 20, 60), anchor="mm")
    png = OUT / f"card_{L}.png"; im.save(png)
    seg = OUT / f"seg_{L}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png), "-i", str(wav),
                    "-af", "apad=pad_dur=1.2,loudnorm=I=-16:TP=-1.5", "-ar", "48000", "-ac", "2",
                    "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-shortest", str(seg)], check=True)
    segs.append(seg)
(OUT / "key.txt").write_text("\n".join(key) + "\n")
print("\n".join(key))
lst = OUT / "list.txt"
lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                str(OUT / "voice_test.mp4")], check=True)
print(json.dumps({"voices": len(segs)}))

# ---- post the blind test as a Facebook DRAFT so it can be heard on the phone ----
if os.getenv("POST_DRAFT") == "1":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pipeline import config, facebook, token
    config.FB_PAGE_TOKEN = token.current()
    vid = facebook.publish_reel(OUT / "voice_test.mp4", "Voice test (draft, not for publishing)", "DRAFT")
    print(f"[draft] video {vid}")
