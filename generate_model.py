#!/usr/bin/env python3
"""
Génère une méduse 3D animée dans les deux formats AR, à partir d'UNE seule description :

  assets/meduse-v4.glb   -> Android (WebXR / Scene Viewer) et aperçu 3D dans la page
  assets/meduse-v4.usdz  -> iPhone / iPad (AR Quick Look, modes « Objet » et « AR »)

CAUSE DU DÉFAUT « la méduse reste immobile sur iPhone » (trouvée dans les forums développeurs d'Apple,
sujet « xform called "Scene" breaks animations on Quicklook starting with iOS15 », confirmé par un ingénieur
Apple) : depuis iOS 15, AR Quick Look n'anime plus aucun fichier USDZ dont un Xform s'appelle « Scene ».
Toutes les versions précédentes de ce générateur avaient un Xform racine nommé « Scene ».
La racine s'appelle maintenant « Meduse », et le générateur refuse les noms réservés.

Autres réglages pour AR Quick Look :
  - animation de transformations d'objets (translation, rotation, échelle) sur une hiérarchie de pièces :
    tentacules = chaînes de 4 capsules articulées, ombrelle qui pulse, corps qui flotte ;
  - temps entiers, une clé par image (24 i/s), boucle unique de 3 s (< 10 s : Quick Look la relance seul) ;
  - aucune métadonnée exotique dans l'en-tête.

Aucune dépendance : Python 3 standard uniquement.   Usage : python3 generate_model.py
"""
import json
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
os.makedirs(OUT, exist_ok=True)

NOM = "meduse-v4"
ROOT = "Meduse"                # NE JAMAIS l'appeler « Scene » : voir l'explication ci-dessus
RESERVED = {"Scene", "scene", "Scenes", "Root", "Behaviors"}
P = 3.0                        # durée de la boucle (s)
FPS = 24
N_FRAMES = int(round(FPS * P))  # 72 images, une clé par image dans le USD
K = 36                         # segments d'interpolation dans le glTF (12 par seconde)
assert P < 10, "AR Quick Look ne relance seul que les animations de moins de 10 s"


# --------------------------------------------------------------------------
# 1. Calcul vectoriel
# --------------------------------------------------------------------------
def sub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def dot(a, b): return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def norm(a):
    l = math.sqrt(dot(a, a))
    return (a[0]/l, a[1]/l, a[2]/l) if l > 1e-12 else (0.0, 1.0, 0.0)


def qaxis(axis, ang):
    s = math.sin(ang / 2)
    return (axis[0]*s, axis[1]*s, axis[2]*s, math.cos(ang / 2))


def qmul(a, b):   # quaternions (x, y, z, w) : applique b puis a
    ax, ay, az, aw = a; bx, by, bz, bw = b
    q = (aw*bx + ax*bw + ay*bz - az*by,
         aw*by - ax*bz + ay*bw + az*bx,
         aw*bz + ax*by - ay*bx + az*bw,
         aw*bw - ax*bx - ay*by - az*bz)
    return tuple(-v for v in q) if q[3] < 0 else q     # w >= 0 : pas de saut de signe


# --------------------------------------------------------------------------
# 2. Géométrie (chaque pièce est exprimée dans le repère de son nœud)
# --------------------------------------------------------------------------
Y0 = 0.20            # hauteur du centre de l'ombrelle au-dessus du socle
N_TENT = 6           # tentacules
N_SEG = 4            # capsules par tentacule
SEG_L = 0.034        # longueur d'une capsule
SEG_R = [0.0070, 0.0058, 0.0046, 0.0034]
R_ATT = 0.05         # rayon d'attache des tentacules sous l'ombrelle


def fix_winding(pos, prov, idx):
    out = []
    for i in range(0, len(idx), 3):
        a, b, c = idx[i:i+3]
        n = cross(sub(pos[b], pos[a]), sub(pos[c], pos[a]))
        avg = tuple(prov[a][k] + prov[b][k] + prov[c][k] for k in range(3))
        out += [a, b, c] if dot(n, avg) >= 0 else [a, c, b]
    return out


def smooth_normals(pos, idx):
    acc = [[0.0, 0.0, 0.0] for _ in pos]
    for i in range(0, len(idx), 3):
        a, b, c = idx[i:i+3]
        n = cross(sub(pos[b], pos[a]), sub(pos[c], pos[a]))
        for v in (a, b, c):
            for k in range(3): acc[v][k] += n[k]
    return [norm(tuple(a)) for a in acc]


def cylinder(r, h, y0=0.0, seg=48):
    pos, nrm, idx = [], [], []
    for j in range(seg + 1):
        a = 2 * math.pi * j / seg
        n = (math.cos(a), 0.0, math.sin(a))
        pos.append((r*n[0], y0, r*n[2])); nrm.append(n)
        pos.append((r*n[0], y0+h, r*n[2])); nrm.append(n)
    for j in range(seg):
        a = 2*j; idx += [a, a+2, a+1, a+1, a+2, a+3]
    for top in (True, False):
        y = y0 + (h if top else 0.0)
        n = (0.0, 1.0 if top else -1.0, 0.0)
        c = len(pos); pos.append((0.0, y, 0.0)); nrm.append(n)
        for j in range(seg + 1):
            a = 2 * math.pi * j / seg
            pos.append((r*math.cos(a), y, r*math.sin(a))); nrm.append(n)
        for j in range(seg):
            idx += [c, c+1+j, c+2+j]
    return pos, nrm, fix_winding(pos, nrm, idx)


def sphere(r, centre, lat=16, lon=24):
    pos, nrm, idx = [], [], []
    for i in range(lat + 1):
        th = math.pi * i / lat
        for j in range(lon + 1):
            ph = 2 * math.pi * j / lon
            n = (math.sin(th)*math.cos(ph), math.cos(th), math.sin(th)*math.sin(ph))
            pos.append((centre[0]+n[0]*r, centre[1]+n[1]*r, centre[2]+n[2]*r)); nrm.append(n)
    for i in range(lat):
        for j in range(lon):
            a = i*(lon+1)+j; b = a+lon+1
            idx += [a, b, a+1, a+1, b, b+1]
    return pos, nrm, fix_winding(pos, nrm, idx)


def capsule(r, length, sides=12, n_h=4):
    """Capsule verticale : calotte haute centrée en y=0, calotte basse centrée en y=-length.
    Le pivot est au centre de la calotte haute : les articulations restent sans trou quand la chaîne plie."""
    pos, nrm, idx = [], [], []
    for half in (0, 1):
        for i in range(n_h + 1):
            th = (math.pi / 2) * i / n_h + half * (math.pi / 2)
            cy = -length * half
            for j in range(sides):
                ph = 2 * math.pi * j / sides
                n = (math.sin(th)*math.cos(ph), math.cos(th), math.sin(th)*math.sin(ph))
                pos.append((r*n[0], cy + r*n[1], r*n[2])); nrm.append(n)
    rings = 2 * (n_h + 1)
    for i in range(rings - 1):
        for j in range(sides):
            a = i*sides + j; b = i*sides + (j+1) % sides
            c = (i+1)*sides + j; d = (i+1)*sides + (j+1) % sides
            idx += [a, c, b, b, c, d]
    return pos, nrm, fix_winding(pos, nrm, idx)


def bell_geometry():
    """Ombrelle : dôme + jupe festonnée, centrée sur l'origine du nœud « Floating »."""
    R, H, n_th, n_ph = 0.072, 0.056, 14, 48
    profil = []
    for i in range(n_th + 1):
        th = (math.pi / 2) * i / n_th
        profil.append((R*math.sin(th), H*math.cos(th), i / n_th))
    profil += [(R*1.035, -0.006, 1.0), (R*1.02, -0.012, 1.0), (R*0.94, -0.018, 1.0)]
    pos, prov = [], []
    centre = (0.0, 0.010, 0.0)
    for (r, y, f) in profil:
        for j in range(n_ph):
            ph = 2 * math.pi * j / n_ph
            rr = r * (1 + 0.045 * (f ** 3) * math.cos(8 * ph))
            p = (rr*math.cos(ph), y, rr*math.sin(ph))
            pos.append(p); prov.append(norm(sub(p, centre)))
    idx = []
    for i in range(len(profil) - 1):
        for j in range(n_ph):
            a = i*n_ph + j; b = i*n_ph + (j+1) % n_ph
            c = (i+1)*n_ph + j; d = (i+1)*n_ph + (j+1) % n_ph
            idx += [a, c, b, b, c, d]
    idx = fix_winding(pos, prov, idx)
    return pos, smooth_normals(pos, idx), idx


MATERIALS = {   # couleur, métal, rugosité, émission, opacité, double face
    "Slate":    dict(col=(0.10, 0.12, 0.18), met=0.55, rou=0.45, emi=(0.00, 0.00, 0.00), alpha=1.0, double=False),
    "Gold":     dict(col=(1.00, 0.77, 0.34), met=1.00, rou=0.28, emi=(0.00, 0.00, 0.00), alpha=1.0, double=False),
    "Bell":     dict(col=(0.78, 0.58, 1.00), met=0.00, rou=0.18, emi=(0.20, 0.09, 0.34), alpha=0.72, double=True),
    "Core":     dict(col=(0.45, 1.00, 0.92), met=0.00, rou=0.30, emi=(0.35, 0.95, 0.85), alpha=1.0, double=False),
    "Tentacle": dict(col=(1.00, 0.62, 0.86), met=0.00, rou=0.40, emi=(0.30, 0.10, 0.25), alpha=1.0, double=False),
}

GEOMETRY = {   # clé -> (positions, normales, indices, matériau)
    "BaseLower": (*cylinder(0.090, 0.012, 0.0), "Slate"),
    "BaseTop": (*cylinder(0.058, 0.008, 0.012), "Gold"),
    "Bell": (*bell_geometry(), "Bell"),
    "Core": (*sphere(0.022, (0.0, 0.024, 0.0)), "Core"),
}
for _k in range(N_SEG):
    GEOMETRY[f"Seg{_k}"] = (*capsule(SEG_R[_k], SEG_L), "Tentacle")


# --------------------------------------------------------------------------
# 3. Scène : hiérarchie de nœuds, transformations fixes et animées
#    t = translation fixe | q = rotation fixe (x,y,z,w) | at/aq/as_ = fonctions du temps
# --------------------------------------------------------------------------
NODES = []


def add(name, parent, t=(0.0, 0.0, 0.0), q=None, meshes=(), at=None, aq=None, as_=None):
    NODES.append(dict(name=name, parent=parent, t=t, q=q, meshes=list(meshes), at=at, aq=aq, as_=as_))
    return len(NODES) - 1


MOY_FLARE = [math.radians(a) for a in (5, 5, 4, 4)]
AMP_FLARE = [math.radians(a) for a in (8, 14, 18, 20)]
AMP_TOURB = [math.radians(a) for a in (3, 8, 12, 14)]


def bob(t):
    return 0.020 * math.sin(2 * math.pi * t / P - 0.8)


def pulse(t):
    c = math.sin(2 * math.pi * t / P)
    return (1 + 0.09*c, 1 - 0.11*c, 1 + 0.09*c)


def joint_rotation(i, k):
    def f(t):
        w = 2 * math.pi * t / P - 1.0 * k + 0.5 * i
        alpha = MOY_FLARE[k] + AMP_FLARE[k] * math.sin(w)     # vers l'extérieur (axe tangent = +Z local)
        beta = AMP_TOURB[k] * math.cos(w)                      # ondulation latérale (axe radial = +X local)
        return qmul(qaxis((0.0, 0.0, 1.0), alpha), qaxis((1.0, 0.0, 0.0), beta))
    return f


def build_scene():
    assert ROOT not in {"Scene"} and ROOT not in RESERVED, "nom réservé par AR Quick Look"
    root = add(ROOT, -1)
    base = add("Base", root)
    add("Lower", base, meshes=[("Lower", "BaseLower")])
    add("Top", base, meshes=[("Top", "BaseTop")])
    fl = add("Floating", root, t=(0.0, Y0, 0.0), at=lambda t: (0.0, Y0 + bob(t), 0.0))
    add("Bell", fl, meshes=[("Dome", "Bell"), ("Core", "Core")], as_=pulse)
    for i in range(N_TENT):
        phi = 2 * math.pi * i / N_TENT
        # repère du tentacule : X local = direction radiale, Z local = tangente
        parent = add(f"T{i}", fl, t=(R_ATT*math.cos(phi), -0.010, R_ATT*math.sin(phi)),
                     q=qaxis((0.0, 1.0, 0.0), -phi))
        for k in range(N_SEG):
            parent = add(f"J{i}_{k}", parent, t=(0.0, 0.0 if k == 0 else -SEG_L, 0.0),
                         meshes=[("Seg", f"Seg{k}")], aq=joint_rotation(i, k))


build_scene()


def rest_t(n): return n["at"](0.0) if n["at"] else n["t"]
def rest_q(n): return n["aq"](0.0) if n["aq"] else n["q"]
def rest_s(n): return n["as_"](0.0) if n["as_"] else None


def children(i): return [j for j, n in enumerate(NODES) if n["parent"] == i]


# --------------------------------------------------------------------------
# 4. Export glTF binaire (.glb) : nœuds animés
# --------------------------------------------------------------------------
def export_glb(path):
    blob = bytearray(); views, accessors = [], []

    def add_view(data, target=None):
        while len(blob) % 4: blob.append(0)
        v = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target: v["target"] = target
        blob.extend(data); views.append(v); return len(views) - 1

    def add_acc(view, ctype, count, typ, mn=None, mx=None):
        a = {"bufferView": view, "componentType": ctype, "count": count, "type": typ}
        if mn is not None: a["min"], a["max"] = mn, mx
        accessors.append(a); return len(accessors) - 1

    gl_mats, mat_idx = [], {}
    for name, m in MATERIALS.items():
        mat_idx[name] = len(gl_mats)
        d = {"name": name, "pbrMetallicRoughness": {"baseColorFactor": [*m["col"], m["alpha"]],
             "metallicFactor": m["met"], "roughnessFactor": m["rou"]}, "emissiveFactor": list(m["emi"])}
        if m["alpha"] < 1: d["alphaMode"] = "BLEND"
        if m["double"]: d["doubleSided"] = True
        gl_mats.append(d)

    gl_meshes, mesh_idx = [], {}
    for key, (pos, nrm, idx, mat) in GEOMETRY.items():
        vp = add_view(b"".join(struct.pack("<3f", *p) for p in pos), 34962)
        vn = add_view(b"".join(struct.pack("<3f", *n) for n in nrm), 34962)
        vi = add_view(struct.pack(f"<{len(idx)}I", *idx), 34963)
        mn = [min(p[i] for p in pos) for i in range(3)]; mx = [max(p[i] for p in pos) for i in range(3)]
        mesh_idx[key] = len(gl_meshes)
        gl_meshes.append({"name": key, "primitives": [{"attributes": {
            "POSITION": add_acc(vp, 5126, len(pos), "VEC3", mn, mx), "NORMAL": add_acc(vn, 5126, len(nrm), "VEC3")},
            "indices": add_acc(vi, 5125, len(idx), "SCALAR"), "material": mat_idx[mat], "mode": 4}]})

    gl_nodes, gl_of = [], {}
    for i, n in enumerate(NODES):
        d = {"name": n["name"]}
        t, q, s = rest_t(n), rest_q(n), rest_s(n)
        if any(abs(v) > 0 for v in t): d["translation"] = list(t)
        if q: d["rotation"] = list(q)
        if s: d["scale"] = list(s)
        gl_of[i] = len(gl_nodes); gl_nodes.append(d)
    for i, n in enumerate(NODES):
        kids = [gl_of[j] for j in children(i)]
        for (mname, key) in n["meshes"]:                       # une pièce = un nœud enfant portant le maillage
            gl_nodes.append({"name": mname, "mesh": mesh_idx[key]}); kids.append(len(gl_nodes) - 1)
        if kids: gl_nodes[gl_of[i]]["children"] = kids

    times = [P * i / K for i in range(K + 1)]
    t_acc = add_acc(add_view(struct.pack(f"<{len(times)}f", *times)), 5126, len(times), "SCALAR", [0.0], [P])
    samplers, channels = [], []

    def channel(node, path, typ, values):
        out = add_acc(add_view(b"".join(struct.pack(f"<{len(v)}f", *v) for v in values)), 5126, len(values), typ)
        samplers.append({"input": t_acc, "output": out, "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})

    for i, n in enumerate(NODES):
        if n["at"]: channel(gl_of[i], "translation", "VEC3", [n["at"](x) for x in times])
        if n["aq"]: channel(gl_of[i], "rotation", "VEC4", [n["aq"](x) for x in times])
        if n["as_"]: channel(gl_of[i], "scale", "VEC3", [n["as_"](x) for x in times])

    gltf = {"asset": {"version": "2.0", "generator": "generate_model.py"}, "scene": 0,
            "scenes": [{"nodes": [0]}], "nodes": gl_nodes, "meshes": gl_meshes, "materials": gl_mats,
            "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
            "animations": [{"name": "Nage", "samplers": samplers, "channels": channels}]}
    js = json.dumps(gltf, separators=(",", ":")).encode(); js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
        f.write(struct.pack("<I4s", len(blob), b"BIN\0")); f.write(blob)
    return total, len(channels)


# --------------------------------------------------------------------------
# 5. Export USD (usda) puis paquet .usdz pour AR Quick Look
# --------------------------------------------------------------------------
def n5(v):
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def v3(v): return "(" + ", ".join(n5(x) for x in v) + ")"
def qw(q): return "(" + ", ".join(n5(x) for x in (q[3], q[0], q[1], q[2])) + ")"   # USD : (w, x, y, z)


def export_usda():
    end = N_FRAMES - 1
    L = ["#usda 1.0", "(", f'    defaultPrim = "{ROOT}"', "    metersPerUnit = 1", '    upAxis = "Y"',
         "    startTimeCode = 0", f"    endTimeCode = {end}", f"    timeCodesPerSecond = {FPS}", ")", ""]

    def mesh_block(name, key, ind):
        pos, nrm, idx, mat = GEOMETRY[key]
        p = " " * ind
        mn = [min(q[i] for q in pos) for i in range(3)]; mx = [max(q[i] for q in pos) for i in range(3)]
        return [f'{p}def Mesh "{name}" (', f'{p}    prepend apiSchemas = ["MaterialBindingAPI"]', f"{p})", f"{p}{{",
                f"{p}    float3[] extent = [{v3(mn)}, {v3(mx)}]",
                f"{p}    uniform bool doubleSided = {'true' if MATERIALS[mat]['double'] else 'false'}",
                f"{p}    int[] faceVertexCounts = [{', '.join(['3'] * (len(idx)//3))}]",
                f"{p}    int[] faceVertexIndices = [{', '.join(map(str, idx))}]",
                f"{p}    rel material:binding = </{ROOT}/Materials/{mat}>",
                f"{p}    normal3f[] normals = [{', '.join(v3(x) for x in nrm)}] (",
                f'{p}        interpolation = "vertex"', f"{p}    )",
                f"{p}    point3f[] points = [{', '.join(v3(q) for q in pos)}]",
                f'{p}    uniform token subdivisionScheme = "none"', f"{p}}}"]

    def node_block(i, ind):
        n = NODES[i]; p = " " * ind
        head = f'{p}def Xform "{n["name"]}"'
        L.append(head + (' (\n' + p + '    kind = "component"\n' + p + ')' if n["parent"] < 0 else ""))
        L.append(f"{p}{{")
        order = []
        if n["at"]:
            samples = ", ".join(f"{f}: {v3(n['at'](f / FPS))}" for f in range(N_FRAMES))
            L.append(f"{p}    double3 xformOp:translate.timeSamples = {{ {samples} }}"); order.append('"xformOp:translate"')
        elif any(abs(v) > 0 for v in n["t"]):
            L.append(f"{p}    double3 xformOp:translate = {v3(n['t'])}"); order.append('"xformOp:translate"')
        if n["aq"]:
            samples = ", ".join(f"{f}: {qw(n['aq'](f / FPS))}" for f in range(N_FRAMES))
            L.append(f"{p}    quatf xformOp:orient.timeSamples = {{ {samples} }}"); order.append('"xformOp:orient"')
        elif n["q"]:
            L.append(f"{p}    quatf xformOp:orient = {qw(n['q'])}"); order.append('"xformOp:orient"')
        if n["as_"]:
            samples = ", ".join(f"{f}: {v3(n['as_'](f / FPS))}" for f in range(N_FRAMES))
            L.append(f"{p}    float3 xformOp:scale.timeSamples = {{ {samples} }}"); order.append('"xformOp:scale"')
        if order:
            L.append(f"{p}    uniform token[] xformOpOrder = [{', '.join(order)}]")
        for (mname, key) in n["meshes"]:
            L.extend(mesh_block(mname, key, ind + 4))
        for j in children(i):
            node_block(j, ind + 4)
        if n["parent"] < 0:
            L.append('    def Scope "Materials"'); L.append("    {")
            for name, m in MATERIALS.items():
                base = f"/{ROOT}/Materials/{name}"
                L.extend([f'        def Material "{name}"', "        {",
                          f"            token outputs:surface.connect = <{base}/PBR.outputs:surface>",
                          '            def Shader "PBR"', "            {",
                          '                uniform token info:id = "UsdPreviewSurface"',
                          f"                color3f inputs:diffuseColor = {v3(m['col'])}",
                          f"                color3f inputs:emissiveColor = {v3(m['emi'])}",
                          f"                float inputs:metallic = {n5(m['met'])}",
                          f"                float inputs:roughness = {n5(m['rou'])}",
                          f"                float inputs:opacity = {n5(m['alpha'])}",
                          "                token outputs:surface", "            }", "        }"])
            L.append("    }")
        L.append(f"{p}}}")

    node_block(0, 0)
    return "\n".join(L) + "\n"


def write_usdz(files, path):
    """Archive ZIP non compressée, données alignées sur 64 octets (exigence USDZ)."""
    out = bytearray(); central = []
    for name, data in files:
        nb = name.encode(); crc = zlib.crc32(data) & 0xFFFFFFFF; offset = len(out)
        pad = (-(offset + 30 + len(nb) + 4)) % 64
        extra = struct.pack("<HH", 0x1986, pad) + b"\0" * pad
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x21, crc, len(data), len(data), len(nb), len(extra))
        out += nb + extra + data
        central.append((nb, crc, len(data), offset))
    cd_start = len(out)
    for nb, crc, size, offset in central:
        out += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0x21, crc, size, size,
                           len(nb), 0, 0, 0, 0, 0, offset) + nb
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central), len(out) - cd_start, cd_start, 0)
    with open(path, "wb") as f:
        f.write(out)
    return len(out)


if __name__ == "__main__":
    n, nch = export_glb(os.path.join(OUT, NOM + ".glb"))
    usda = export_usda()
    if os.environ.get("KEEP_USDA"):
        open(os.path.join(OUT, NOM + ".usda"), "w").write(usda)
    m = write_usdz([(NOM + ".usda", usda.encode())], os.path.join(OUT, NOM + ".usdz"))
    animated = sum(1 for x in NODES if x["at"] or x["aq"] or x["as_"])
    print(f"{len(NODES)} nœuds dont {animated} animés | {nch} canaux glTF | USD : {N_FRAMES} images à {FPS} i/s = {N_FRAMES/FPS:.1f} s")
    print(f"{NOM}.glb  : {n/1024:.0f} Ko")
    print(f"{NOM}.usdz : {m/1024:.0f} Ko")
