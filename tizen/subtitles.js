(function (root) {
  'use strict';
  function plainText(value) {
    var entities = {amp: '&', lt: '<', gt: '>', nbsp: ' ', quot: '"', apos: "'", lrm: '\u200e', rlm: '\u200f'};
    return value.replace(/<[^>]*>/g, '').replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|nbsp|quot|apos|lrm|rlm);/gi, function (entity, name) {
      if (name.charAt(0) !== '#') { return entities[name.toLowerCase()]; }
      var code = name.charAt(1).toLowerCase() === 'x' ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      if (code < 0 || code > 1114111) { return entity; }
      if (code <= 65535) { return String.fromCharCode(code); }
      code -= 65536;
      return String.fromCharCode(55296 + (code >> 10), 56320 + (code & 1023));
    });
  }
  function milliseconds(hours, minutes, seconds, fraction) {
    return ((Number(hours || 0) * 60 + Number(minutes)) * 60 + Number(seconds)) * 1000 + Number(fraction);
  }
  function parse(source) {
    var cues = [];
    var timestamp = '(?:(\\d{1,3}):)?(\\d{2}):(\\d{2})[.,](\\d{3})';
    var timing = new RegExp('^' + timestamp + '\\s+-->\\s+' + timestamp + '(?:\\s.*)?$');
    source.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split(/\n\s*\n/).forEach(function (block) {
      if (/^(WEBVTT|NOTE|STYLE|REGION)(?:\s|$)/.test(block)) { return; }
      var lines = block.split('\n');
      for (var index = 0; index < lines.length; index += 1) {
        var match = timing.exec(lines[index].trim());
        if (!match) { continue; }
        var start = milliseconds(match[1], match[2], match[3], match[4]);
        var end = milliseconds(match[5], match[6], match[7], match[8]);
        if (end > start) { cues.push({start: start, end: end, text: plainText(lines.slice(index + 1).join('\n')).trim()}); }
        break;
      }
    });
    return cues.sort(function (first, second) { return first.start - second.start; });
  }
  function textAt(cues, position) {
    var text = [];
    for (var index = 0; index < cues.length; index += 1) {
      if (cues[index].start > position) { break; }
      if (position < cues[index].end) { text.push(cues[index].text); }
    }
    return text.join('\n');
  }
  root.CinematecaSubtitles = {parse: parse, textAt: textAt, plainText: plainText};
}(window));
