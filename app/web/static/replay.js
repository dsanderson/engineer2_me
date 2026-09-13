/* Drives the mission replay: one slider position = one event, and the graph above it is
   redrawn to the state that event left behind. The rules for what an event does to an item
   live in app/replay.py — the server ships frames that already carry the change, and this
   only folds them. With JS off the same page still works: the range input is inside a GET
   form, and every position renders server-side. */
(function () {
  "use strict";

  var STATUSES = ["draft", "open", "claimed", "proposed", "verified", "failed", "deprecated", "abandoned"];

  function setup(root) {
    var data = root.querySelector("script.rdata");
    var range = root.querySelector(".rrange");
    var graph = root.querySelector(".hgraph");
    if (!data || !range || !graph) return;
    var line;
    try {
      line = JSON.parse(data.textContent);
    } catch (err) {
      return;
    }

    var frames = line.frames || [];
    var announced = {};
    (line.announced || []).forEach(function (k) {
      announced[k] = 1;
    });
    var nodes = {};
    Array.prototype.forEach.call(graph.querySelectorAll(".gnode"), function (n) {
      nodes[n.getAttribute("data-id")] = n;
    });

    var svg = graph.querySelector("svg.gedges");
    var caption = root.querySelector(".rcap");
    var counts = root.querySelector(".rcounts");
    var pos = root.querySelector(".rpos");
    var tail = root.querySelector(".rtail");
    var playBtn = root.querySelector(".rplay");
    var speed = root.querySelector(".rspeed");
    var onlyBox = root.querySelector(".ronly");
    var onlyLabel = root.querySelector(".ronly-label");
    var go = root.querySelector(".rgo");

    // JS is here, so reveal what only works with it and retire the no-JS submit.
    [playBtn, speed, onlyLabel].forEach(function (el) {
      if (el) el.hidden = false;
    });
    if (go) go.hidden = true;

    function title(id) {
      var node = nodes[id];
      var link = node && node.querySelector(".gtitle");
      return link ? link.textContent : id.slice(0, 8);
    }

    function fold(upto) {
      var status = {},
        conf = {},
        present = {},
        edges = {};
      for (var k in line.status0) status[k] = line.status0[k];
      for (var c in line.conf0) conf[c] = 1;
      (line.present0 || []).forEach(function (i) {
        present[i] = 1;
      });
      for (var n = 0; n < upto; n++) {
        var f = frames[n];
        if (f.n) present[f.i] = 1;
        if (f.s) status[f.i] = f.s;
        if (f.c === 1) conf[f.i] = 1;
        else if (f.c === 0) delete conf[f.i];
        if (f.e) edges[f.e] = 1;
        if (f.x) delete edges[f.x];
      }
      return { status: status, conf: conf, present: present, edges: edges };
    }

    function paintNodes(state) {
      var tally = { items: 0 };
      for (var id in nodes) {
        var node = nodes[id];
        var here = !!state.present[id];
        var st = state.status[id] || "draft";
        node.classList.toggle("gabsent", !here);
        STATUSES.forEach(function (s) {
          node.classList.toggle("st-" + s, s === st);
        });
        node.classList.toggle("retired", st === "deprecated" || st === "abandoned");
        var tick = node.querySelector(".gconf");
        if (tick) tick.classList.toggle("gconf-off", !state.conf[id]);
        if (here) {
          tally.items++;
          tally[st] = (tally[st] || 0) + 1;
        }
      }
      return tally;
    }

    function paintEdges(state) {
      if (!svg) return;
      Array.prototype.forEach.call(svg.querySelectorAll(".gedge"), function (path) {
        var f = path.getAttribute("data-f"),
          t = path.getAttribute("data-t");
        var key = f + "|" + t;
        var on = announced[key] ? !!state.edges[key] : !!(state.present[f] && state.present[t]);
        path.classList.toggle("gedge-off", !on);
      });
    }

    function paintCaption(frame, at) {
      if (!caption) return;
      var ts = caption.querySelector(".rts"),
        ev = caption.querySelector(".rev"),
        link = caption.querySelector(".rtitle"),
        label = caption.querySelector(".rlabel"),
        by = caption.querySelector(".rby");
      caption.classList.toggle("moved", !!(frame && frame.s));
      caption.setAttribute("data-at", String(at));
      if (ev) ev.hidden = !frame;
      if (link) link.hidden = !frame;
      if (!frame) {
        if (ts) ts.textContent = "";
        if (label) label.textContent = "before anything happened";
        if (by) by.textContent = "";
        return;
      }
      if (ts) ts.textContent = frame.a.replace("T", " ").replace(/Z$/, "");
      if (ev) {
        ev.textContent = frame.t;
        ev.className = "badge ev ev-" + frame.t + " rev";
      }
      if (link) {
        link.textContent = title(frame.i);
        link.setAttribute("href", "/items/" + frame.i);
      }
      if (label) label.textContent = frame.l;
      if (by) by.textContent = frame.b ? "by " + frame.b : "";
    }

    function paintCounts(tally) {
      if (!counts) return;
      Array.prototype.forEach.call(counts.querySelectorAll(".rnum"), function (el) {
        el.textContent = String(tally[el.getAttribute("data-count")] || 0);
      });
    }

    function paintTail(at) {
      if (!tail) return;
      var rows = frames.slice(Math.max(0, at - 10), at).reverse();
      tail.innerHTML = rows
        .map(function (f) {
          return (
            '<li class="' +
            (f.s ? "rmoved" : "") +
            '"><small class="muted ts">' +
            esc(f.a.replace("T", " ").replace(/Z$/, "")) +
            '</small> <span class="badge ev ev-' +
            esc(f.t) +
            '">' +
            esc(f.t) +
            '</span> <a href="/items/' +
            esc(f.i) +
            '">' +
            esc(title(f.i)) +
            '</a> <small class="muted detail">' +
            esc(f.l) +
            "</small></li>"
          );
        })
        .join("");
    }

    function esc(text) {
      return String(text).replace(/[&<>"]/g, function (ch) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
      });
    }

    var current = -1;

    function render(at) {
      at = Math.max(0, Math.min(at, frames.length));
      current = at;
      if (Number(range.value) !== at) range.value = String(at);
      var state = fold(at);
      var frame = at > 0 ? frames[at - 1] : null;
      paintCounts(paintNodes(state));
      paintEdges(state);
      paintCaption(frame, at);
      paintTail(at);
      if (pos) pos.textContent = "event " + at + " of " + frames.length;
      Array.prototype.forEach.call(graph.querySelectorAll(".gnode.focus"), function (n) {
        n.classList.remove("focus");
      });
      if (frame && nodes[frame.i]) {
        nodes[frame.i].classList.add("focus");
        if (frame.s) flash(nodes[frame.i]);
      }
      syncUrl(at);
    }

    var urlTimer = null;

    function syncUrl(at) {
      // Coalesced: a drag fires `input` continuously, and browsers rate-limit replaceState.
      if (urlTimer) clearTimeout(urlTimer);
      urlTimer = setTimeout(function () {
        try {
          history.replaceState(null, "", "?at=" + at);
        } catch (err) {
          /* a file:// or sandboxed origin refuses; the slider still works */
        }
      }, 200);
    }

    function flash(node) {
      node.classList.remove("rflash");
      void node.offsetWidth; // restart the animation rather than let it be ignored
      node.classList.add("rflash");
    }

    function interesting(i) {
      // Frame indexes are 1-based against the slider: frame i-1 produced position i.
      return !onlyBox || !onlyBox.checked || (frames[i - 1] && frames[i - 1].s);
    }

    function step(dir) {
      var at = current + dir;
      while (at > 0 && at < frames.length && !interesting(at)) at += dir;
      render(at);
      return at;
    }

    range.addEventListener("input", function () {
      stop();
      render(Number(range.value));
    });

    Array.prototype.forEach.call(root.querySelectorAll(".rstep"), function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        stop();
        var role = a.getAttribute("data-role");
        if (role === "start") render(0);
        else if (role === "end") render(frames.length);
        else step(role === "prev" ? -1 : 1);
      });
    });

    var timer = null;

    function stop() {
      if (timer) clearInterval(timer);
      timer = null;
      if (playBtn) playBtn.textContent = "play";
    }

    function play() {
      stop();
      if (current >= frames.length) render(0);
      playBtn.textContent = "pause";
      timer = setInterval(function () {
        if (step(1) >= frames.length) stop();
      }, Number(speed && speed.value) || 220);
    }

    if (playBtn) {
      playBtn.addEventListener("click", function () {
        if (timer) stop();
        else play();
      });
    }
    if (speed) {
      speed.addEventListener("change", function () {
        if (timer) play();
      });
    }
    root.addEventListener("keydown", function (ev) {
      if (ev.target === range && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) return;
      if (ev.key === "ArrowRight") step(1);
      else if (ev.key === "ArrowLeft") step(-1);
    });

    // graph.js rebuilds every path on resize, which drops the classes we just set.
    if (svg && window.MutationObserver) {
      new MutationObserver(function () {
        paintEdges(fold(current));
      }).observe(svg, { childList: true });
    }

    render(Number(range.value));
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".replay"), setup);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
