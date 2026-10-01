/*
 * Trois musiques de fond, lues depuis de vrais fichiers MP3 (assets/musique-*.mp3).
 *
 *   Musique.jouer("calme" | "rythmee" | "mysterieuse")   lance une piste (arrête les autres)
 *   Musique.basculer(nom)                                lance la piste, ou l'arrête si elle joue déjà
 *   Musique.arreter()                                    coupe la musique
 *   Musique.courante()                                   "calme", "rythmee", "mysterieuse" ou null
 *   Musique.surChangement(fn)                            fn(nomCourant) à chaque changement
 *   Musique.disponible                                   false si le navigateur n'a pas Web Audio
 *
 * Pourquoi l'API Web Audio plutôt qu'une simple balise <audio loop> :
 * le format MP3 ajoute un très court silence technique en tête et en fin de fichier (une
 * particularité connue du codec, pas un défaut de nos fichiers). Une balise <audio loop> ferait
 * entendre ce silence à chaque répétition, comme un petit accroc régulier. Ici, chaque répétition
 * est programmée pour chevaucher légèrement la précédente, avec un fondu enchaîné de 180 ms qui
 * masque ce défaut : la boucle s'entend comme continue.
 */
(function () {
  "use strict";

  var AC = window.AudioContext || window.webkitAudioContext;
  var FICHIERS = {
    calme: "assets/musique-calme.mp3",
    rythmee: "assets/musique-rythmee.mp3",
    mysterieuse: "assets/musique-mysterieuse.mp3"
  };
  var CROSSFADE = 0.18;     // recouvrement entre deux répétitions successives (secondes)
  var FONDU_BASCULE = 0.5;  // fondu à l'arrêt ou au changement de piste (secondes)

  var ctx = null;
  var sortie = null;        // bus maître (compresseur)
  var tampons = {};          // nom -> AudioBuffer déjà décodé
  var chargements = {};      // nom -> Promise en cours (évite de décoder deux fois)
  var actuelle = null;       // { nom, boucle }
  var dernierJeton = 0;      // ignore une réponse asynchrone devenue obsolète
  var ecouteurs = [];

  // ------------------------------------------------------------------ init
  function init() {
    if (ctx) return;
    ctx = new AC();
    var compresseur = ctx.createDynamicsCompressor();
    sortie = ctx.createGain();
    sortie.gain.value = 0.9;
    sortie.connect(compresseur);
    compresseur.connect(ctx.destination);

    // Certains navigateurs mettent le son en pause (appel, retour sur l'onglet) : on le relance
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && actuelle && ctx.state !== "running") ctx.resume();
    });

    // Démarre le téléchargement + décodage des 3 pistes tout de suite, pour qu'elles soient
    // déjà prêtes au premier toucher (le décodage ne nécessite pas de geste de l'utilisateur,
    // seule la lecture en a besoin).
    Object.keys(FICHIERS).forEach(charger);
  }

  function charger(nom) {
    if (tampons[nom]) return Promise.resolve(tampons[nom]);
    if (chargements[nom]) return chargements[nom];
    chargements[nom] = fetch(FICHIERS[nom])
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (ab) { return ctx.decodeAudioData(ab); })
      .then(function (buf) { tampons[nom] = buf; return buf; })
      .catch(function (err) { delete chargements[nom]; throw err; });
    return chargements[nom];
  }

  // -------------------------------------------------- lecture en boucle avec fondu enchaîné
  function creerBoucle(buffer) {
    var actif = true;
    var pisteGain = ctx.createGain();
    pisteGain.gain.value = 0;
    pisteGain.connect(sortie);
    pisteGain.gain.linearRampToValueAtTime(1, ctx.currentTime + 0.8);

    var dur = buffer.duration;
    var prochainDepart = ctx.currentTime + 0.05;
    var sources = [];

    function programmerRepetitions() {
      if (!actif) return;
      while (prochainDepart < ctx.currentTime + 2.0) {
        var src = ctx.createBufferSource();
        src.buffer = buffer;
        var g = ctx.createGain();
        src.connect(g);
        g.connect(pisteGain);

        var t0 = prochainDepart;
        g.gain.setValueAtTime(0, t0);
        g.gain.linearRampToValueAtTime(1, t0 + CROSSFADE);
        g.gain.setValueAtTime(1, t0 + dur - CROSSFADE);
        g.gain.linearRampToValueAtTime(0, t0 + dur);
        src.start(t0);
        src.stop(t0 + dur + 0.05);
        sources.push(src);

        prochainDepart = t0 + dur - CROSSFADE;
      }
    }

    programmerRepetitions();
    var minuteur = setInterval(programmerRepetitions, 500);

    return {
      entree: pisteGain,
      arreter: function (fondu) {
        actif = false;
        clearInterval(minuteur);
        var t = ctx.currentTime;
        var f = fondu === undefined ? FONDU_BASCULE : fondu;
        pisteGain.gain.cancelScheduledValues(t);
        pisteGain.gain.setValueAtTime(pisteGain.gain.value, t);
        pisteGain.gain.linearRampToValueAtTime(0, t + f);
        setTimeout(function () {
          sources.forEach(function (s) { try { s.stop(); } catch (e) {} });
          try { pisteGain.disconnect(); } catch (e) {}
        }, f * 1000 + 150);
      }
    };
  }

  // ------------------------------------------------------------------ API
  function notifier() {
    var nom = actuelle ? actuelle.nom : null;
    ecouteurs.forEach(function (fn) { fn(nom); });
  }

  function arreter() {
    dernierJeton++;
    if (actuelle) { if (actuelle.boucle) actuelle.boucle.arreter(); actuelle = null; }
    notifier();
  }

  function jouer(nom) {
    if (!AC || !FICHIERS[nom]) return;
    init();
    if (ctx.state !== "running") ctx.resume();

    var jeton = ++dernierJeton;
    if (actuelle && actuelle.boucle) actuelle.boucle.arreter();
    actuelle = { nom: nom, boucle: null };   // affichage optimiste : le bouton réagit tout de suite
    notifier();

    charger(nom).then(function (buf) {
      if (jeton !== dernierJeton) return;     // une autre piste a été choisie entre-temps
      actuelle = { nom: nom, boucle: creerBoucle(buf) };
    }).catch(function () {
      if (jeton !== dernierJeton) return;
      actuelle = null;
      notifier();
    });
  }

  window.Musique = {
    disponible: !!AC,
    jouer: jouer,
    arreter: arreter,
    basculer: function (nom) { if (actuelle && actuelle.nom === nom) arreter(); else jouer(nom); },
    courante: function () { return actuelle ? actuelle.nom : null; },
    surChangement: function (fn) { ecouteurs.push(fn); }
  };
})();
