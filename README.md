# Méduse en réalité augmentée (iPhone + Android)

Une méduse 3D animée (ombrelle qui pulse, six tentacules qui ondulent) que l'on pose sur une surface plane
réelle avec la caméra du téléphone.

## Contenu

| Fichier | Rôle |
|---|---|
| `index.html` | La page (utilise la bibliothèque `<model-viewer>` de Google, chargée depuis jsDelivr) |
| `assets/meduse-v4.glb` | Modèle avec animation intégrée, pour Android (WebXR, Scene Viewer) et l'aperçu 3D |
| `assets/meduse-v4.usdz` | Le même modèle et la même animation, pour l'iPhone (AR Quick Look) |
| `musique.js` | Lit les 3 fichiers MP3 ci-dessous via l'API Web Audio, avec un fondu enchaîné à chaque répétition |
| `generate_music.py` | Régénère les 3 fichiers MP3 (Python 3 + numpy/scipy + ffmpeg, uniquement pour changer la composition) |
| `assets/musique-calme.mp3` | Nappes lentes et petites cloches |
| `assets/musique-rythmee.mp3` | 100 BPM, grosse caisse, claps, basse et arpège |
| `assets/musique-mysterieuse.mp3` | Nappe grave continue, gongs et bourrasques espacés |
| `generate_model.py` | Régénère les deux modèles (Python 3, aucune dépendance) |
| `_headers` | Types MIME corrects sur Netlify et Cloudflare Pages |

## Mettre en ligne (HTTPS obligatoire)

La caméra et la RA ne fonctionnent qu'en HTTPS. Le plus simple : glisser-déposer le dossier sur
Netlify Drop, Cloudflare Pages, Vercel, ou le pousser sur GitHub Pages. Ouvrez ensuite l'adresse sur le téléphone.

## Tester en local

    python3 -m http.server 8000

- Android : branchez le téléphone en USB (débogage USB activé), lancez `adb reverse tcp:8000 tcp:8000`,
  puis ouvrez `http://localhost:8000` dans Chrome (localhost est considéré comme sécurisé).
- iPhone : utilisez un tunnel HTTPS (par exemple `cloudflared tunnel --url http://localhost:8000`).

## Comment ça marche selon l'appareil

- Android / Chrome : session WebXR dans la page (détection du sol, réticule, toucher pour poser).
  Sinon, bascule automatique sur Google Scene Viewer.
- iPhone / iPad / Safari : AR Quick Look avec `meduse-v4.usdz` (modes « Objet » et « AR »).

## Animation intégrée aux modèles

Les deux fichiers contiennent la même animation de 3 s, écrite dans le fichier lui-même (pas ajoutée par la page).
Elle est faite de transformations sur une hiérarchie de pièces : les tentacules sont des chaînes de 4 capsules
articulées qui ondulent, l'ombrelle pulse (changement d'échelle) et le corps entier flotte.

- `meduse-v4.glb` : 26 nœuds animés (animation « Nage »), lus par model-viewer, par la RA WebXR de Chrome
  et par Scene Viewer.
- `meduse-v4.usdz` : les mêmes 26 nœuds animés au format USD, lus par AR Quick Look sur iPhone,
  dans les modes « Objet » et « AR ».

### Pourquoi la méduse restait immobile sur iPhone

Cause trouvée dans les forums développeurs d'Apple (sujet « xform called "Scene" breaks animations on Quicklook
starting with iOS15 », reconnu par un ingénieur Apple) : **depuis iOS 15, AR Quick Look n'anime plus un fichier USDZ
qui contient un nœud (Xform) nommé « Scene »**, même si l'animation est correcte. Toutes les versions précédentes
de ce site avaient un nœud racine appelé « Scene ». Il s'appelle maintenant « Meduse », et `generate_model.py`
refuse les noms réservés. Si vous créez vos propres USDZ, n'utilisez jamais « Scene » (Blender l'ajoute par défaut).

### Autres réglages pour AR Quick Look

- **Animation de transformations** (translation, rotation, échelle), plus fiable que l'animation de squelette.
- **Durée de 3 s, écrite une seule fois.** Au-delà de 10 s, Quick Look affiche un curseur de lecture au lieu de
  boucler seul.
- **Temps entiers, une clé par image** (24 i/s).
- **Mouvements amples** : la pointe d'un tentacule se déplace de plus de 8 cm, l'ombrelle change de hauteur de 4 cm,
  le corps monte et descend de 4 cm.
- Aucune métadonnée exotique dans l'en-tête du fichier.
- Dans la page, l'animation démarre toujours (le bouton « Mettre en pause » permet de l'arrêter).

`generate_model.py` produit les deux fichiers à partir des mêmes données.

## Personnaliser

- Autre objet : remplacez `assets/meduse-v4.glb` (avec animation) et `assets/meduse-v4.usdz`, ou modifiez le
  squelette, la forme et l'animation dans `generate_model.py` puis relancez-le.
- Si vous remplacez les fichiers, gardez un nouveau nom (par exemple `meduse-v5.*`) et mettez à jour `index.html` :
  Safari met les fichiers en cache et afficherait sinon l'ancienne version.
- Pour ne pas dépendre d'un CDN, téléchargez `model-viewer.min.js` et changez l'attribut `src` de la balise `<script>`.

## Musique de fond

Trois boutons (« Musique calme », « Musique rythmée » et « Musique mystérieuse ») en haut à gauche de la scène.
Un seul son à la fois : toucher un autre bouton change de musique, toucher le bouton actif l'arrête. Le son démarre
au toucher (obligatoire sur iPhone).

Les trois pistes sont de vrais fichiers MP3 (`assets/musique-*.mp3`), pas une synthèse en direct. `musique.js` les
charge et les joue via l'API Web Audio plutôt qu'une simple balise `<audio loop>`, pour une raison précise :

- **Le format MP3 ajoute lui-même un très court silence technique en tête et en fin de fichier** (une particularité
  connue du codec, présente dans n'importe quel MP3, pas un défaut de nos fichiers). Une balise `<audio loop>`
  ferait entendre ce silence à chaque répétition, comme un petit accroc régulier.
- `musique.js` programme donc chaque répétition pour qu'elle chevauche légèrement la précédente (180 ms), avec un
  fondu enchaîné qui masque ce silence : la boucle s'entend comme continue. Vérifié en mesurant le niveau sonore en
  continu sur plusieurs répétitions : il ne redescend jamais à zéro, y compris pour la piste la plus courte (19,2 s).
- Les trois fichiers sont téléchargés et décodés dès l'ouverture de la page (pas seulement au premier toucher), pour
  qu'ils soient déjà prêts dès qu'on appuie sur un bouton.

Pour changer les fichiers : remplacez `assets/musique-calme.mp3`, `assets/musique-rythmee.mp3` et
`assets/musique-mysterieuse.mp3` par vos propres MP3 (gardez les mêmes noms, ou modifiez l'objet `FICHIERS` en
tête de `musique.js`). Aucune contrainte de durée : le fondu enchaîné à 180 ms s'adapte à la durée réelle de
chaque fichier.

Pour changer la composition synthétisée : modifiez `generate_music.py` (accords dans `ACCORDS`, tempo dans
`bpm`, etc.) puis relancez-le ; il nécessite numpy, scipy et l'outil ffmpeg (avec le codec libmp3lame), mais
uniquement pour régénérer les fichiers — le site lui-même n'en a pas besoin.

Pour ajouter une quatrième piste : déposez un 4e MP3 dans `assets/`, ajoutez son nom et son fichier dans l'objet
`FICHIERS` (`musique.js`), ajoutez son nom dans `noms` (`index.html`) et un bouton `data-piste="xxx"` à côté des
trois autres.
