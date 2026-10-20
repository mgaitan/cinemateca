(function () {
  'use strict';
  var server = 'https://cinemateca.fastapicloud.dev';
  var selected = null;
  var versions = [];
  var nextOffset = 0;
  var query = '';
  var loading = false;
  var catalogRequest = null;
  var detailRequest = null;
  var seen = {};
  var returnFocus = null;
  var playing = false;
  var preparing = false;
  var compatible = false;
  var position = 0;
  var offset = 0;
  var duration = 0;
  var generation = 0;
  var subtitleMovieId = '';
  var subtitleChoice = '';
  var subtitleManual = false;
  var externalTracks = [];
  var nativeTracks = [];
  var subtitleCues = {};
  var nativeCaption = null;
  var subtitleListRequest = null;
  var subtitleTextRequest = null;
  var loadingSubtitle = '';
  var controlsTimer;
  var player = window.webapis && window.webapis.avplay;

  function element(id) { return document.getElementById(id); }
  function message(text) { element('message').textContent = text; }
  function status(text) { element('play-status').textContent = text; }
  function clock(milliseconds) {
    var seconds = Math.floor(Math.max(0, milliseconds) / 1000);
    var minutes = Math.floor(seconds / 60);
    return Math.floor(minutes / 60) + ':' + ('0' + minutes % 60).slice(-2) + ':' + ('0' + seconds % 60).slice(-2);
  }
  function request(path, success, failure, raw) {
    var xhr = new XMLHttpRequest();
    xhr.open('GET', server + path);
    xhr.timeout = 45000;
    xhr.onload = function () {
      if (xhr.status >= 200 && xhr.status < 300) {
        try { success(raw ? xhr.responseText : JSON.parse(xhr.responseText)); }
        catch (error) { failure('No se pudo leer la respuesta del servidor.'); }
      } else if (xhr.status === 401 || xhr.status === 403) {
        failure('Activá el acceso abierto de la biblioteca desde Cinemateca en tu celular o computadora.');
      } else { failure('El servidor no pudo completar la operación. Intentá de nuevo.'); }
    };
    xhr.onerror = xhr.ontimeout = function () { failure('No se pudo conectar con Cinemateca. Revisá la conexión e intentá de nuevo.'); };
    xhr.send();
    return xhr;
  }
  function titleKey(movie) {
    var title = movie.title;
    if (title.normalize) { title = title.normalize('NFD').replace(/[\u0300-\u036f]/g, ''); }
    return movie.tmdbId ? 'tmdb:' + movie.tmdbId : title.toLowerCase() + '|' + movie.year;
  }
  function loadCatalog(reset) {
    if (reset && catalogRequest) { catalogRequest.abort(); loading = false; }
    if (loading || (!reset && nextOffset === null)) { return; }
    if (reset) { nextOffset = 0; seen = {}; element('movies').textContent = ''; }
    loading = true;
    element('more').disabled = true;
    message('Cargando películas…');
    catalogRequest = request('/api/movies?limit=16&offset=' + nextOffset + '&q=' + encodeURIComponent(query), function (data) {
      loading = false;
      message(data.count ? '' : 'No se encontraron películas.');
      element('count').textContent = data.count + ' películas';
      data.movies.forEach(function (movie) {
        var key = titleKey(movie);
        if (seen[key]) { return; }
        seen[key] = true;
        var button = document.createElement('button');
        button.className = 'movie';
        button.setAttribute('data-focus', '');
        var poster = document.createElement('img');
        poster.alt = '';
        if (movie.poster || movie.backdrop) { poster.src = movie.poster || movie.backdrop; }
        var title = document.createElement('span');
        title.textContent = movie.title;
        var facts = document.createElement('small');
        facts.textContent = [movie.year, movie.country].filter(Boolean).join(' · ');
        button.appendChild(poster); button.appendChild(title); button.appendChild(facts);
        button.onclick = function () { returnFocus = button; loadDetails(movie.id); };
        element('movies').appendChild(button);
      });
      nextOffset = data.next_offset;
      element('more').hidden = nextOffset === null;
      element('more').disabled = false;
      if (reset && element('movies').firstChild) { element('movies').firstChild.focus(); }
    }, function (error) {
      loading = false; message(error); element('more').hidden = false; element('more').disabled = false;
    });
  }
  function showDetails(movie) {
    selected = movie;
    element('catalog').hidden = true;
    element('details').hidden = false;
    element('title').textContent = movie.title;
    element('facts').textContent = [movie.year, movie.country, movie.duration ? movie.duration + ' min' : ''].filter(Boolean).join(' · ');
    element('credits').textContent = [movie.director, (movie.cast || []).join(', ')].filter(Boolean).join(' · ');
    element('overview').textContent = movie.overview || '';
    element('poster').hidden = !(movie.poster || movie.backdrop);
    if (!element('poster').hidden) { element('poster').src = movie.poster || movie.backdrop; }
    element('play').disabled = element('compatible').disabled = !movie.canDownload;
    element('versions-label').hidden = versions.length < 2;
    element('versions').textContent = '';
    versions.forEach(function (version) {
      var option = document.createElement('option');
      option.value = version.id; option.textContent = version.filename || version.title;
      option.selected = version.id === movie.id;
      element('versions').appendChild(option);
    });
    message(movie.canDownload ? '' : 'El propietario deshabilitó la descarga y reproducción del archivo.');
    element(movie.canDownload ? 'play' : 'back').focus();
  }
  function loadDetails(id) {
    if (detailRequest) { detailRequest.abort(); }
    message('Cargando ficha…');
    detailRequest = request('/api/movies/' + encodeURIComponent(id), function (data) {
      versions = data.versions; showDetails(data.movie);
    }, message);
  }
  function back() {
    if (playing) { stopPlayback(); return; }
    if (!element('details').hidden) {
      element('details').hidden = true; element('catalog').hidden = false; message('');
      if (returnFocus) { returnFocus.focus(); }
    } else if (window.tizen && window.tizen.application) { window.tizen.application.getCurrentApplication().exit(); }
  }
  function showControls() {
    window.clearTimeout(controlsTimer);
    element('controls').hidden = false;
    positionSubtitles();
    controlsTimer = window.setTimeout(function () { if (playing && !preparing) { element('controls').hidden = true; positionSubtitles(); } }, 6000);
  }
  function positionSubtitles() {
    element('subtitle-overlay').style.bottom = element('controls').hidden ? '8%' : (element('controls').offsetHeight + 24) + 'px';
  }
  function renderSubtitles() {
    var text = '';
    if (playing && subtitleChoice.indexOf('external:') === 0) {
      text = window.CinematecaSubtitles.textAt(subtitleCues[subtitleChoice] || [], position);
    } else if (playing && subtitleChoice.indexOf('native:') === 0 && nativeCaption && position < nativeCaption.end) {
      text = nativeCaption.text;
    }
    element('subtitle-overlay').textContent = text;
    element('subtitle-overlay').hidden = !text;
  }
  function resetSubtitles() {
    if (subtitleListRequest) { subtitleListRequest.abort(); }
    if (subtitleTextRequest) { subtitleTextRequest.abort(); }
    subtitleListRequest = null; subtitleTextRequest = null; loadingSubtitle = '';
    subtitleMovieId = ''; subtitleChoice = ''; subtitleManual = false;
    externalTracks = []; nativeTracks = []; subtitleCues = {}; nativeCaption = null;
    element('subtitle-status').textContent = '';
    renderSubtitles();
  }
  function spanish(label) {
    return /(?:^|[. _-])(es|esp|spa|spanish|espa[nñ]ol|castellano|latino)(?:[. _-]|$)/i.test(label);
  }
  function chooseSubtitle(choice, manual) {
    if (manual) { subtitleManual = true; }
    if (choice !== subtitleChoice) { nativeCaption = null; }
    subtitleChoice = choice;
    element('subtitles').value = choice;
    if (subtitleTextRequest && loadingSubtitle !== choice) {
      subtitleTextRequest.abort(); subtitleTextRequest = null; loadingSubtitle = '';
    }
    renderSubtitles();
    if (!playing || preparing) { return; }
    try {
      player.setSilentSubtitle(true);
      if (choice.indexOf('native:') === 0) { player.setSelectTrack('TEXT', Number(choice.slice(7))); }
    } catch (error) { element('subtitle-status').textContent = 'El televisor no pudo activar esta pista incrustada. Probá un subtítulo externo.'; }
    if (choice.indexOf('external:') !== 0 || subtitleCues[choice] || loadingSubtitle === choice) { return; }
    var track = externalTracks.filter(function (item) { return 'external:' + item.id === choice; })[0];
    if (!track) { return; }
    loadingSubtitle = choice;
    element('subtitle-status').textContent = 'Cargando ' + track.label + '…';
    var movieId = subtitleMovieId;
    subtitleTextRequest = request(track.src, function (source) {
      if (movieId !== subtitleMovieId || choice !== subtitleChoice) { return; }
      subtitleTextRequest = null; loadingSubtitle = '';
      var cues = window.CinematecaSubtitles.parse(source);
      if (!cues.length) { element('subtitle-status').textContent = 'Este subtítulo no contiene tiempos válidos. Elegí otra pista.'; return; }
      subtitleCues[choice] = cues;
      element('subtitle-status').textContent = '';
      renderSubtitles();
    }, function (error) {
      if (movieId !== subtitleMovieId || choice !== subtitleChoice) { return; }
      subtitleTextRequest = null; loadingSubtitle = '';
      element('subtitle-status').textContent = 'No se pudo cargar ' + track.label + '. ' + error;
    }, true);
  }
  function refreshSubtitleOptions() {
    var choices = [{value: '', label: 'Desactivados'}];
    var chosen = subtitleChoice;
    nativeTracks.forEach(function (track) {
      var info = track.extra_info || {};
      if (typeof info === 'string') { try { info = JSON.parse(info); } catch (error) { info = {}; } }
      var language = info.track_lang || info.language || 'Pista ' + (track.index + 1);
      choices.push({value: 'native:' + track.index, label: 'Incrustado: ' + language, spanish: spanish(language)});
    });
    externalTracks.forEach(function (track) {
      choices.push({value: 'external:' + track.id, label: 'Externo: ' + track.label, spanish: spanish(track.label)});
    });
    if (!choices.some(function (choice) { return choice.value === chosen; })) { chosen = ''; subtitleManual = false; }
    if (!subtitleManual) {
      var preferred = choices.filter(function (choice) { return choice.spanish && choice.value.indexOf('external:') === 0; })[0] ||
        choices.filter(function (choice) { return choice.spanish; })[0] ||
        choices.filter(function (choice) { return choice.value.indexOf('external:') === 0; })[0] || choices[1];
      chosen = preferred ? preferred.value : '';
    }
    element('subtitles').textContent = '';
    choices.forEach(function (choice) {
      var option = document.createElement('option'); option.value = choice.value; option.textContent = choice.label;
      element('subtitles').appendChild(option);
    });
    chooseSubtitle(chosen, false);
  }
  function prepareSubtitles() {
    nativeTracks = []; nativeCaption = null;
    if (subtitleMovieId === selected.id) { return; }
    resetSubtitles(); subtitleMovieId = selected.id;
    var movieId = subtitleMovieId;
    element('subtitle-status').textContent = 'Buscando subtítulos externos…';
    refreshSubtitleOptions();
    subtitleListRequest = request('/api/movies/' + encodeURIComponent(movieId) + '/subtitles', function (data) {
      if (movieId !== subtitleMovieId) { return; }
      subtitleListRequest = null; externalTracks = data.tracks;
      element('subtitle-status').textContent = externalTracks.length ? '' : 'No se encontraron archivos de subtítulos externos para esta película.';
      refreshSubtitleOptions();
    }, function (error) {
      if (movieId !== subtitleMovieId) { return; }
      subtitleListRequest = null; element('subtitle-status').textContent = 'No se pudieron buscar subtítulos externos. ' + error;
    });
  }
  function releasePlayer() {
    generation += 1;
    nativeCaption = null;
    element('subtitle-overlay').textContent = ''; element('subtitle-overlay').hidden = true;
    window.clearTimeout(controlsTimer);
    if (player) { try { player.close(); } catch (error) { status('No se pudo cerrar la reproducción.'); } }
  }
  function stopPlayback() {
    releasePlayer(); playing = false; preparing = false;
    resetSubtitles();
    element('playback').hidden = true; element('library').hidden = false;
    document.body.className = ''; document.documentElement.className = '';
    element('play').focus();
  }
  function playbackError(error) {
    stopPlayback();
    message('No se pudo reproducir el archivo (' + (error.name || error) + '). Probá el otro modo de audio. El códec de video también debe ser compatible con el televisor.');
  }
  function startPlayback(audioCompatible, start, paused) {
    if (!selected || !selected.canDownload) { return; }
    if (!player) { message('La reproducción nativa requiere instalar Cinemateca.wgt en un televisor Samsung Tizen.'); return; }
    releasePlayer();
    var current = generation;
    compatible = audioCompatible;
    offset = compatible ? start : 0;
    position = start * 1000;
    duration = (selected.durationSeconds || selected.duration * 60) * 1000;
    playing = true; preparing = true;
    document.body.className = 'playing'; document.documentElement.className = 'playing';
    element('library').hidden = true; element('playback').hidden = false;
    element('playing-title').textContent = selected.title;
    prepareSubtitles();
    element('time').textContent = clock(position);
    status('Preparando reproducción…'); showControls(); element('pause').focus();
    var url = server + '/api/' + (compatible ? 'play/' : 'stream/') + encodeURIComponent(selected.id);
    if (compatible) { url += '?start=' + start; }
    try {
      player.open(url);
      player.setDisplayRect(0, 0, 1920, 1080);
      player.setDisplayMethod('PLAYER_DISPLAY_MODE_LETTER_BOX');
      player.setListener({
        onbufferingstart: function () { if (current === generation) { status('Cargando video…'); showControls(); } },
        onbufferingcomplete: function () { if (current === generation) { status(''); } },
        oncurrentplaytime: function (milliseconds) {
          if (current !== generation) { return; }
          position = offset * 1000 + milliseconds;
          element('time').textContent = clock(position) + (duration ? ' / ' + clock(duration) : '');
          renderSubtitles();
        },
        onsubtitlechange: function (milliseconds, text, type) {
          if (current !== generation || subtitleChoice.indexOf('native:') !== 0) { return; }
          if (String(type || '0') !== '0') {
            nativeCaption = null; element('subtitle-status').textContent = 'Esta pista usa subtítulos de imagen. Probá un archivo externo.';
          } else { nativeCaption = {text: window.CinematecaSubtitles.plainText(text || ''), end: position + Number(milliseconds || 0)}; }
          renderSubtitles();
        },
        onstreamcompleted: function () { if (current === generation) { stopPlayback(); } },
        onerror: function (error) { if (current === generation) { playbackError(error); } }
      });
      player.prepareAsync(function () {
        if (current !== generation) { return; }
        preparing = false;
        if (!duration) { duration = offset * 1000 + player.getDuration(); }
        function begin() {
          if (current !== generation) { return; }
          player.play();
          if (paused) { player.pause(); }
          try { nativeTracks = compatible ? [] : player.getTotalTrackInfo().filter(function (track) { return track.type === 'TEXT'; }); }
          catch (error) { nativeTracks = []; element('subtitle-status').textContent = 'No se pudieron leer las pistas incrustadas. Podés usar un archivo externo.'; }
          refreshSubtitleOptions();
          element('pause').textContent = paused ? 'Reanudar' : 'Pausar';
          status(paused ? 'En pausa' : ''); showControls();
        }
        try {
          if (!compatible && start) { player.seekTo(start * 1000, begin, playbackError); }
          else { begin(); }
        } catch (error) { playbackError(error); }
      }, function (error) { if (current === generation) { playbackError(error); } });
    } catch (error) { playbackError(error); }
  }
  function pause() {
    if (!playing || preparing) { return; }
    try {
      var paused = player.getState() === 'PAUSED';
      if (paused) { player.play(); } else { player.pause(); }
      element('pause').textContent = paused ? 'Pausar' : 'Reanudar';
      status(paused ? '' : 'En pausa'); showControls();
    } catch (error) { playbackError(error); }
  }
  function seek(delta) {
    if (!playing || preparing) { return; }
    var target = Math.max(0, Math.min(position + delta * 1000, duration ? duration - 1000 : 172800000));
    nativeCaption = null;
    try {
      if (compatible) { startPlayback(true, target / 1000, player.getState() === 'PAUSED'); }
      else { position = target; renderSubtitles(); player.seekTo(target, function () {}, playbackError); }
      showControls();
    } catch (error) { playbackError(error); }
  }
  function moveFocus(direction) {
    var origin = document.activeElement.getBoundingClientRect();
    var best = null;
    var bestScore = Infinity;
    Array.prototype.forEach.call(document.querySelectorAll('[data-focus]'), function (candidate) {
      if (candidate.disabled || !candidate.offsetWidth || !candidate.offsetHeight || candidate === document.activeElement) { return; }
      var rect = candidate.getBoundingClientRect();
      var horizontal = (rect.left + rect.right - origin.left - origin.right) / 2;
      var vertical = (rect.top + rect.bottom - origin.top - origin.bottom) / 2;
      var forward = direction === 37 ? -horizontal : direction === 39 ? horizontal : direction === 38 ? -vertical : vertical;
      var sideways = direction === 37 || direction === 39 ? Math.abs(vertical) : Math.abs(horizontal);
      var score = forward + sideways * 3;
      if (forward > 1 && score < bestScore) { best = candidate; bestScore = score; }
    });
    if (best) { best.focus(); }
  }
  element('search-form').onsubmit = function (event) { event.preventDefault(); query = element('search').value.trim(); loadCatalog(true); };
  element('reset').onclick = function () { element('search').value = ''; query = ''; loadCatalog(true); };
  element('more').onclick = function () { loadCatalog(false); };
  element('back').onclick = back;
  element('versions').onchange = function () {
    var id = this.value;
    versions.some(function (version) { if (version.id === id) { showDetails(version); return true; } return false; });
  };
  element('play').onclick = function () { startPlayback(false, 0, false); };
  element('compatible').onclick = function () { startPlayback(true, 0, false); };
  element('pause').onclick = pause;
  element('rewind').onclick = function () { seek(-30); };
  element('forward').onclick = function () { seek(30); };
  element('subtitles').onchange = function () { element('subtitle-status').textContent = ''; chooseSubtitle(this.value, true); showControls(); };
  element('stop').onclick = stopPlayback;
  document.addEventListener('keydown', function (event) {
    var code = event.keyCode;
    if (playing) { showControls(); }
    if (code === 10009 || code === 27 || (playing && code === 413)) { event.preventDefault(); back(); }
    else if (playing && (code === 10252 || code === 415 || code === 19 || code === 32)) {
      event.preventDefault();
      if ((code === 415 && player.getState() !== 'PAUSED') || (code === 19 && player.getState() !== 'PLAYING')) { return; }
      pause();
    } else if (playing && (code === 412 || code === 417)) { event.preventDefault(); seek(code === 412 ? -30 : 30); }
    else if (code >= 37 && code <= 40 && document.activeElement.tagName !== 'SELECT') {
      if (document.activeElement.tagName === 'INPUT' && (code === 37 || code === 39)) { return; }
      event.preventDefault(); moveFocus(code);
    }
  });
  if (window.tizen && window.tizen.tvinputdevice) {
    ['MediaPlayPause', 'MediaPlay', 'MediaPause', 'MediaStop', 'MediaRewind', 'MediaFastForward'].forEach(function (key) {
      try { window.tizen.tvinputdevice.registerKey(key); } catch (error) { message('Algunas teclas multimedia no están disponibles en este modelo.'); }
    });
  }
  document.addEventListener('visibilitychange', function () { if (document.hidden && playing) { stopPlayback(); } });
  window.addEventListener('unload', releasePlayer);
  element('search').focus(); loadCatalog(true);
}());
