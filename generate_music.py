#!/usr/bin/env python3
"""
Génère les trois musiques de fond du site, en MP3, à partir d'une seule description musicale.

  assets/musique-calme.mp3         nappes lentes et petites cloches
  assets/musique-rythmee.mp3       100 BPM, grosse caisse, claps, basse et arpège
  assets/musique-mysterieuse.mp3   nappe grave continue, gongs et bourrasques espacés

Tout est synthétisé (oscillateurs, filtres, réverbération, écho) : aucun fichier audio importé,
donc rien à licencier. Ce script sert à RÉGÉNÉRER les MP3 si vous voulez changer la composition ;
le site lui-même ne charge que les 3 fichiers .mp3 déjà produits, il n'a pas besoin de ce script
pour fonctionner.

Bouclage sans coupure : chaque piste est rendue en boucle + une « queue » supplémentaire (la
réverbération et l'écho ont besoin de temps pour se stabiliser), puis la queue est recollée sur le
début par un fondu enchaîné (voir crossfade_loop). Le format MP3 ajoute lui-même un très bref silence
technique en tête et en fin de fichier (particularité connue du codec) : la page ne boucle donc pas
le fichier tel quel, elle le rejoue avec un fondu enchaîné (voir musique.js) qui masque ce défaut.

Dépendances (uniquement pour régénérer) : numpy, scipy, et l'outil en ligne de commande ffmpeg
(avec le codec libmp3lame). Aucune de ces dépendances n'est nécessaire pour faire fonctionner le site.
Usage : python3 generate_music.py
"""
import os
import subprocess
import numpy as np
from scipy.signal import lfilter, fftconvolve
import scipy.io.wavfile as wav

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)

"""Petite bibliothèque DSP pour rejouer hors ligne (en fichiers) la même composition que musique.js."""

FS = 44100

def hz(midi): return 440.0 * 2.0 ** ((midi - 69) / 12.0)

def env_lin_exp(n, fs, attaque, plateau, chute, vol, exp_floor=0.0008):
    """Enveloppe: montée linéaire (attaque s) -> plateau (jusqu'à 'plateau' s) -> descente exponentielle (chute s)."""
    t = np.arange(n) / fs
    e = np.empty(n)
    a_end = attaque
    p_end = max(attaque, plateau)
    e[:] = vol
    m1 = t < a_end
    e[m1] = vol * (t[m1] / max(a_end, 1e-9))
    m3 = t >= p_end
    if chute > 1e-9:
        e[m3] = vol * np.exp(-5.0 * (t[m3] - p_end) / chute)
    else:
        e[m3] = 0.0
    return e

def env_attack_release(n, fs, attaque, dur, chute, vol):
    """Montée linéaire -> plateau à vol jusqu'à dur -> descente linéaire sur 'chute' s (pad())."""
    t = np.arange(n) / fs
    e = np.empty(n)
    a_end = attaque
    m1 = t < a_end
    e[m1] = vol * (t[m1] / max(a_end, 1e-9))
    m2 = (t >= a_end) & (t < dur)
    e[m2] = vol
    m3 = t >= dur
    if chute > 1e-9:
        e[m3] = np.maximum(0.0, vol * (1 - (t[m3] - dur) / chute))
    else:
        e[m3] = 0.0
    return e

def osc(freq, n, fs, kind="sine"):
    ph = np.cumsum(np.full(n, 2 * np.pi * freq / fs)) if np.isscalar(freq) else np.cumsum(2 * np.pi * freq / fs)
    if kind == "sine": return np.sin(ph)
    if kind == "sawtooth":
        x = (ph / (2 * np.pi)) % 1.0
        return 2 * x - 1
    if kind == "triangle":
        return (2 / np.pi) * np.arcsin(np.sin(ph))
    raise ValueError(kind)

# ---- filtres RBJ (cookbook audio EQ) : coefficients biquad -----------------
def _biquad_lp(f0, Q, fs):
    w0 = 2 * np.pi * f0 / fs; al = np.sin(w0) / (2 * Q); cw = np.cos(w0)
    b0 = (1 - cw) / 2; b1 = 1 - cw; b2 = (1 - cw) / 2
    a0 = 1 + al; a1 = -2 * cw; a2 = 1 - al
    return np.array([b0, b1, b2]) / a0, np.array([1, a1 / a0, a2 / a0])

def _biquad_hp(f0, Q, fs):
    w0 = 2 * np.pi * f0 / fs; al = np.sin(w0) / (2 * Q); cw = np.cos(w0)
    b0 = (1 + cw) / 2; b1 = -(1 + cw); b2 = (1 + cw) / 2
    a0 = 1 + al; a1 = -2 * cw; a2 = 1 - al
    return np.array([b0, b1, b2]) / a0, np.array([1, a1 / a0, a2 / a0])

def _biquad_bp(f0, Q, fs):
    w0 = 2 * np.pi * f0 / fs; al = np.sin(w0) / (2 * Q); cw = np.cos(w0)
    b0 = al; b1 = 0.0; b2 = -al
    a0 = 1 + al; a1 = -2 * cw; a2 = 1 - al
    return np.array([b0, b1, b2]) / a0, np.array([1, a1 / a0, a2 / a0])

def lowpass(x, f0, Q, fs=FS):
    b, a = _biquad_lp(f0, Q, fs); return lfilter(b, a, x)

def highpass(x, f0, Q, fs=FS):
    b, a = _biquad_hp(f0, Q, fs); return lfilter(b, a, x)

def bandpass(x, f0, Q, fs=FS):
    b, a = _biquad_bp(f0, Q, fs); return lfilter(b, a, x)

# ---- filtre variable dans le temps (state-variable, Chamberlin) -----------
def svf_sweep(x, f0_arr, Q, fs=FS, mode="lp"):
    n = len(x); low = 0.0; band = 0.0; out = np.empty(n)
    f0_arr = np.asarray(f0_arr); q_inv = 1.0 / Q
    for i in range(n):
        f = 2 * np.sin(np.pi * min(f0_arr[i], fs * 0.49) / fs)
        high = x[i] - low - q_inv * band
        band += f * high
        low += f * band
        out[i] = low if mode == "lp" else band
    return out

# ---- bus (réverbération + écho) -------------------------------------------
def make_reverb_ir(duree, fs=FS, seed=0, decay_pow=2.5):
    rng = np.random.default_rng(seed)
    n = int(duree * fs)
    ir = (rng.random(n) * 2 - 1) * (1 - np.arange(n) / n) ** decay_pow
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    return ir

def echo_send(x, delai, fb, cutoff, fs=FS, taps=8):
    d = int(round(delai * fs))
    # retard (delay) puis filtre passe-bas, répété ; chaque passage supplémentaire pondéré par fb
    def delay(sig):
        y = np.zeros_like(sig)
        if d < len(sig): y[d:] = sig[: len(sig) - d]
        return y
    term = lowpass(delay(x), cutoff, 0.707, fs)
    y = term.copy(); scale = 1.0
    for _ in range(taps - 1):
        term = lowpass(delay(term), cutoff, 0.707, fs)
        scale *= fb
        y += scale * term
    return y

class Bus:
    """Un bus par piste : on y ajoute des voix, puis on applique réverb + écho + limiteur doux."""
    def __init__(self, n_samples, rev, echo, delai, seed=0):
        self.buf = np.zeros(n_samples)
        self.rev, self.echo, self.delai = rev, echo, delai
        self.ir = make_reverb_ir(2.6, seed=seed)

    def add(self, t0, signal):
        i0 = int(round(t0 * FS)); i1 = min(i0 + len(signal), len(self.buf))
        if i1 > i0: self.buf[i0:i1] += signal[: i1 - i0]

    def mixdown(self):
        dry = self.buf
        wet_rev = fftconvolve(dry, self.ir, mode="full")[: len(dry)] * self.rev * 6.0
        wet_echo = echo_send(dry * self.echo, self.delai, 0.35, 2400)
        mix = dry + wet_rev + wet_echo
        return np.tanh(mix * 1.15) * 0.98

def to_stereo(mono, width_ms=11):
    d = int(FS * width_ms / 1000)
    r = np.zeros_like(mono); r[d:] = mono[: len(mono) - d]; r[:d] = mono[:d]
    return np.stack([mono, r], axis=1)

def normalize_peak(x, target=0.9):
    p = np.max(np.abs(x)) + 1e-9
    return x * (target / p)

def crossfade_loop(raw, loop_len_s, tail_s, fs=FS):
    """raw doit être périodique par construction (LOOP + TAIL rendu) ; recolle la fin sur le début."""
    loop_n, tail_n = int(round(loop_len_s * fs)), int(round(tail_s * fs))
    head, tail = raw[:loop_n].copy(), raw[loop_n: loop_n + tail_n]
    fade = np.linspace(0, 1, tail_n)
    head[:tail_n] = head[:tail_n] * fade + tail * (1 - fade)
    return head

def pad(accord, dur, vol, attaque, relache, avec_basse, rng):
    n = int(round((dur + relache) * FS))
    e = env_attack_release(n, FS, attaque, dur, relache, vol)
    out = np.zeros(n)
    for m in accord["notes"]:
        for det in (-6, 6):
            out += osc(hz(m) * 2 ** (det / 1200), n, FS, "triangle")
    if avec_basse:
        out += osc(hz(accord["racine"]), n, FS, "sine")
    out *= e
    return lowpass(out, 1100, 1.0)

def cloche(midi):
    n = int(round(3.3 * FS))
    out = np.zeros(n)
    for ratio, amp in ((1.0, 0.06), (2.01, 0.02)):
        e = env_lin_exp(n, FS, 0.01, 0.01, 3.19, amp)
        out += osc(hz(midi) * ratio, n, FS, "sine") * e
    return out

def grosse_caisse():
    n = int(round(0.42 * FS))
    t = np.arange(n) / FS
    freq = np.where(t < 0.13, 140 * (42 / 140) ** (np.minimum(t, 0.13) / 0.13), 42.0)
    e = env_lin_exp(n, FS, 0.0, 0.0, 0.4, 0.9)
    return osc(freq, n, FS, "sine") * e

def souffle(rng, dur, kind, freq, q, vol):
    n = int(round((dur + 0.02) * FS))
    bruit = rng.random(n) * 2 - 1
    e = env_lin_exp(n, FS, 0.0, 0.0, dur, vol)
    filt = highpass(bruit, freq, q) if kind == "hp" else bandpass(bruit, freq, q)
    return filt * e

def charleston(rng, vol): return souffle(rng, 0.05, "hp", 7000, 0.7, vol)
def clap(rng): return souffle(rng, 0.16, "bp", 1600, 0.9, 0.35)

def basse(midi, dur):
    n = int(round((dur + 0.05) * FS))
    e = env_lin_exp(n, FS, 0.01, 0.01, dur, 0.2)
    return lowpass(osc(hz(midi), n, FS, "sawtooth") * e, 380, 2.0)

def pincee(midi, vol):
    n = int(round(0.32 * FS))
    t = np.arange(n) / FS
    e = env_lin_exp(n, FS, 0.005, 0.005, 0.295, vol)
    cutoff = np.where(t < 0.25, 3200 * (500 / 3200) ** (np.minimum(t, 0.25) / 0.25), 500.0)
    return svf_sweep(osc(hz(midi), n, FS, "triangle"), cutoff, 1.4, FS, "lp") * e

def drone(midi, dur, vol):
    n = int(round((dur + 0.1) * FS))
    e = env_attack_release(n, FS, dur * 0.35, dur * 0.65, dur * 0.35, vol)
    out = sum(osc(hz(midi) * 2 ** (det / 1200), n, FS, "sawtooth") for det in (0, -5, 7))
    return lowpass(out * e, 340, 0.8)

def gong(midi, vol):
    n = int(round(5.6 * FS))
    out = np.zeros(n)
    for ratio, amp in ((1.0, 1.0), (1.51, 0.5), (2.02, 0.3), (3.01, 0.18)):
        e = env_lin_exp(n, FS, 0.02, 0.02, 5.48, vol * amp)
        out += osc(hz(midi) * ratio, n, FS, "sine") * e
    return out

def souffle_vent(rng, dur, vol):
    n = int(round((dur + 0.05) * FS))
    t = np.arange(n) / FS
    bruit = rng.random(n) * 2 - 1
    half = dur / 2
    freq = np.where(t < half, 220 + (420 - 220) * np.minimum(t, half) / half,
                     420 + (220 - 420) * np.minimum(np.maximum(t - half, 0), half) / half)
    e = np.where(t < dur * 0.4, vol * np.minimum(t, dur * 0.4) / (dur * 0.4),
                 np.maximum(0.0, vol * (1 - (t - dur * 0.4) / (dur * 0.6))))
    return svf_sweep(bruit, freq, 0.6, FS, "bp") * e

ACCORDS = [
    {"racine": 45, "notes": [57, 60, 64]},
    {"racine": 41, "notes": [57, 60, 65]},
    {"racine": 48, "notes": [55, 60, 64]},
    {"racine": 43, "notes": [55, 59, 62]},
]
PENTATONIQUE = [69, 72, 74, 76, 79, 81, 84]
GRAVES = [33, 36, 38]

def rendre_calme():
    LOOP, TAIL = 32.0, 4.0
    n = int(round((LOOP + TAIL) * FS))
    bus = Bus(n, rev=0.5, echo=0.3, delai=0.5, seed=1)
    rng = np.random.default_rng(20)
    t = 0.0; i = 0
    while t < LOOP + TAIL:
        bus.add(t, pad(ACCORDS[i % 4], 8, 0.05, 2.5, 2.5, True, rng))
        bt = t + 0.8 + rng.random()
        while bt < t + 8:
            bus.add(bt, cloche(int(rng.choice(PENTATONIQUE))))
            bt += 1.4 + rng.random() * 2.4
        t += 8; i += 1
    return crossfade_loop(bus.mixdown(), LOOP, TAIL)

def rendre_rythmee():
    bpm = 100; pas = 60 / bpm / 4
    N_CYCLES = 2                      # 1 cycle = 4 accords * 16 pas = 9,6 s
    LOOP = N_CYCLES * 16 * pas * 4
    TAIL = 1.2
    n = int(round((LOOP + TAIL) * FS))
    bus = Bus(n, rev=0.22, echo=0.28, delai=pas * 4 * 0.75, seed=2)
    rng = np.random.default_rng(21)
    ARPEGE = [0, 1, 2, 1, 0, 1, 2, 1]
    BASSE = {0: 0, 3: 0, 6: 12, 8: 0, 11: 0, 14: 12}
    t = 0.0; nstep = 0
    while t < LOOP + TAIL:
        s = nstep % 16; accord = ACCORDS[(nstep // 16) % 4]
        if s == 0: bus.add(t, pad(accord, pas * 16, 0.03, 0.15, 0.5, False, rng))
        if s % 4 == 0: bus.add(t, grosse_caisse())
        if s in (4, 12): bus.add(t, clap(rng))
        if s % 4 == 2: bus.add(t, charleston(rng, 0.10))
        elif s % 2 == 1: bus.add(t, charleston(rng, 0.03))
        if s in BASSE: bus.add(t, basse(accord["racine"] + BASSE[s], pas * 2.2))
        if s % 2 == 0: bus.add(t, pincee(accord["notes"][ARPEGE[(s // 2) % 8]] + 12, 0.11 if s == 0 else 0.075))
        t += pas; nstep += 1
    return crossfade_loop(bus.mixdown(), LOOP, TAIL)

def rendre_mysterieuse():
    N_CYCLES = 2                      # 1 cycle = 3 notes graves * 11 s
    LOOP = N_CYCLES * len(GRAVES) * 11.0
    TAIL = 6.0
    n = int(round((LOOP + TAIL) * FS))
    bus = Bus(n, rev=0.65, echo=0.22, delai=0.9, seed=3)
    rng = np.random.default_rng(22)
    t = 0.0; i = 0
    while t < LOOP + TAIL:
        bus.add(t, drone(GRAVES[i % len(GRAVES)], 11, 0.05))
        if i % 2 == 0:
            bus.add(t + 1.5 + rng.random() * 2, gong(int(rng.choice(PENTATONIQUE)) - 12, 0.07))
        if rng.random() < 0.6:
            bus.add(t + rng.random() * 6, souffle_vent(rng, 5 + rng.random() * 3, 0.05))
        t += 11; i += 1
    return crossfade_loop(bus.mixdown(), LOOP, TAIL)

def to_mp3(mono_mix, path_mp3, label):
    st = to_stereo(normalize_peak(mono_mix, 0.9))
    path_wav = path_mp3[:-4] + ".wav"
    wav.write(path_wav, FS, (st * 32767).astype(np.int16))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path_wav, "-codec:a", "libmp3lame",
                    "-b:a", "128k", "-ar", "44100", path_mp3], check=True)
    os.remove(path_wav)
    print(f"{label:14s} -> {os.path.basename(path_mp3)}  ({len(mono_mix)/FS:.1f} s, {os.path.getsize(path_mp3)/1024:.0f} Ko)")


if __name__ == "__main__":
    to_mp3(rendre_calme(), os.path.join(OUT, "musique-calme.mp3"), "calme")
    to_mp3(rendre_rythmee(), os.path.join(OUT, "musique-rythmee.mp3"), "rythmee")
    to_mp3(rendre_mysterieuse(), os.path.join(OUT, "musique-mysterieuse.mp3"), "mysterieuse")
