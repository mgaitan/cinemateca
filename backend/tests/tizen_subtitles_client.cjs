const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const nodes = new Map();
let document;
function node(tag = 'div') {
  return {
    tagName: tag.toUpperCase(), children: [], hidden: false, value: '', style: {}, offsetHeight: 200,
    set textContent(value) { this.text = value; this.children = []; },
    get textContent() { return this.text || ''; },
    get firstChild() { return this.children[0]; },
    appendChild(child) { this.children.push(child); },
    setAttribute() {}, focus() { document.activeElement = this; },
  };
}
document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
  createElement: node, addEventListener() {}, body: {}, documentElement: {},
};
const movie = {id: 'movie_test_12345', title: 'Película', year: '2024', canDownload: true, durationSeconds: 300};
const pending = [];
const requests = [];
const subtitles = 'WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<i>Español</i> &amp; texto\n\n00:00:32.000 --> 00:00:36.000\nDespués del salto\n';
let state = 'NONE';
let listener;
let stream;
let selectedTrack;
let silent;
class XHR {
  open(method, url) { this.url = url; }
  send() {
    requests.push(this);
    pending.push(() => {
      if (this.aborted) return;
      if (this.url.endsWith('/subtitles/external_es_12345')) this.responseText = subtitles;
      else if (this.url.endsWith('/subtitles')) this.responseText = JSON.stringify({tracks: [{
        id: 'external_es_12345', label: 'pelicula.es.srt', src: '/api/movies/movie_test_12345/subtitles/external_es_12345',
      }]});
      else if (this.url.includes('/api/movies?')) this.responseText = JSON.stringify({movies: [movie], count: 1, next_offset: null});
      else this.responseText = JSON.stringify({movie, versions: [movie]});
      this.status = 200;
      this.onload();
    });
  }
  abort() { this.aborted = true; }
}
const player = {
  close() { state = 'NONE'; }, open(url) { stream = url; state = 'IDLE'; },
  getState() { return state; }, setDisplayRect() {}, setDisplayMethod() {},
  setListener(value) { listener = value; },
  prepareAsync(success) { pending.push(() => { state = 'READY'; success(); }); },
  play() { state = 'PLAYING'; }, pause() { state = 'PAUSED'; }, getDuration() { return 300000; },
  getTotalTrackInfo() { return [{type: 'TEXT', index: 3, extra_info: '{"track_lang":"spa"}'}]; },
  setSelectTrack(type, index) { assert.equal(type, 'TEXT'); selectedTrack = index; },
  setSilentSubtitle(value) { silent = value; },
  seekTo(position, success) { listener.oncurrentplaytime(position); success(); },
};
const window = {webapis: {avplay: player}, clearTimeout() {}, setTimeout() {}, addEventListener() {}};
const context = {window, document, XMLHttpRequest: XHR};
const directory = path.resolve(__dirname, '../../tizen');
vm.runInNewContext(fs.readFileSync(path.join(directory, 'subtitles.js'), 'utf8'), context);
vm.runInNewContext(fs.readFileSync(path.join(directory, 'app.js'), 'utf8'), context);
function flush() { while (pending.length) pending.shift()(); }
function element(id) { return document.getElementById(id); }
function choose(value) { element('subtitles').value = value; element('subtitles').onchange(); }

flush();
element('movies').firstChild.onclick(); flush();
element('play').onclick(); flush();
assert.equal(element('subtitles').value, 'external:external_es_12345');
assert.equal(silent, true);
listener.oncurrentplaytime(1500);
assert.equal(element('subtitle-overlay').textContent, 'Español & texto');
element('pause').onclick();
assert.equal(state, 'PAUSED');
assert.equal(element('subtitle-overlay').textContent, 'Español & texto');

choose('native:3');
assert.equal(selectedTrack, 3);
listener.onsubtitlechange('2000', '<i>Incrustado</i>', '0');
assert.equal(element('subtitle-overlay').textContent, 'Incrustado');
listener.oncurrentplaytime(3500);
assert.equal(element('subtitle-overlay').hidden, true);
choose('');
listener.onsubtitlechange('3000', 'No mostrar', '0');
assert.equal(element('subtitle-overlay').hidden, true);
element('stop').onclick();

element('compatible').onclick(); flush();
assert.equal(element('subtitles').value, 'external:external_es_12345');
assert.equal(element('subtitles').children.some(option => option.value === 'native:3'), false);
listener.oncurrentplaytime(2000);
element('forward').onclick(); flush();
assert.ok(stream.endsWith('?start=32'));
listener.oncurrentplaytime(1500);
assert.equal(element('subtitle-overlay').textContent, 'Después del salto');
assert.equal(requests.filter(request => request.url.endsWith('/subtitles/external_es_12345')).length, 2);
choose('');
element('forward').onclick(); flush();
listener.oncurrentplaytime(1000);
assert.equal(element('subtitles').value, '');
assert.equal(element('subtitle-overlay').hidden, true);

element('stop').onclick();
element('play').onclick();
const late = requests.at(-1);
element('stop').onclick();
late.responseText = JSON.stringify({tracks: [{id: 'stale', label: 'stale.es.srt'}]});
late.status = 200;
late.onload();
assert.equal(element('subtitle-overlay').hidden, true);
assert.equal(element('playback').hidden, true);
assert.equal(requests.some(request => request.url.includes('stale')), false);
