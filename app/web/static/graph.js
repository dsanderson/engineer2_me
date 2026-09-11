/* Draws the reference edges over an already-rendered HTML graph, and dims everything
   unrelated while a node is hovered or focused — following one line at a time is the
   only way a dense graph stays readable. With JS off the nodes and the edge table below
   them still say everything; only the curves are missing. */
(function () {
  "use strict";

  function points(a, b, box) {
    var ax = a.left + a.width / 2 - box.left,
      bx = b.left + b.width / 2 - box.left;
    if (b.top >= a.bottom - 1) return [ax, a.bottom - box.top, bx, b.top - box.top, "v"];
    if (a.top >= b.bottom - 1) return [ax, a.top - box.top, bx, b.bottom - box.top, "v"];
    // same band: leave from the side, so the curve does not run under the two nodes
    var left = a.left <= b.left;
    return [
      (left ? a.right : a.left) - box.left,
      a.top + a.height / 2 - box.top,
      (left ? b.left : b.right) - box.left,
      b.top + b.height / 2 - box.top,
      "h",
    ];
  }

  function path(p) {
    var x1 = p[0], y1 = p[1], x2 = p[2], y2 = p[3];
    if (p[4] === "v") {
      var dy = Math.max(8, Math.abs(y2 - y1) * 0.45);
      return "M" + x1 + "," + y1 + " C" + x1 + "," + (y1 + dy) + " " + x2 + "," + (y2 - dy) + " " + x2 + "," + y2;
    }
    var dx = Math.max(10, Math.abs(x2 - x1) * 0.4);
    return "M" + x1 + "," + y1 + " C" + (x1 + dx) + "," + y1 + " " + (x2 - dx) + "," + y2 + " " + x2 + "," + y2;
  }

  function setup(root) {
    var svg = root.querySelector("svg.gedges");
    var canvas = root.querySelector(".glayers");
    var data = root.querySelector("script.gdata");
    if (!svg || !canvas || !data) return;
    var edges;
    try {
      edges = JSON.parse(data.textContent);
    } catch (err) {
      return;
    }
    var nodes = {};
    Array.prototype.forEach.call(root.querySelectorAll(".gnode"), function (n) {
      nodes[n.getAttribute("data-id")] = n;
    });
    edges = edges.filter(function (e) {
      return nodes[e.f] && nodes[e.t];
    });

    function draw() {
      var box = canvas.getBoundingClientRect();
      if (!box.width) return;
      svg.setAttribute("viewBox", "0 0 " + box.width + " " + box.height);
      svg.setAttribute("width", box.width);
      svg.setAttribute("height", box.height);
      var out = [];
      edges.forEach(function (e) {
        var d = path(points(nodes[e.f].getBoundingClientRect(), nodes[e.t].getBoundingClientRect(), box));
        out.push(
          '<path class="gedge rel-' + e.r + '" d="' + d + '" data-f="' + e.f + '" data-t="' + e.t + '"></path>'
        );
      });
      svg.innerHTML = out.join("");
    }

    function focus(id) {
      root.classList.add("focusing");
      Array.prototype.forEach.call(root.querySelectorAll(".gnode"), function (n) {
        n.classList.remove("me", "on");
      });
      Array.prototype.forEach.call(svg.querySelectorAll(".gedge"), function (p) {
        var from = p.getAttribute("data-f"), to = p.getAttribute("data-t");
        var hit = from === id || to === id;
        p.classList.toggle("on", hit);
        if (!hit) return;
        var other = nodes[from === id ? to : from];
        if (other) other.classList.add("on");
      });
      nodes[id].classList.add("me");
    }

    function clear() {
      root.classList.remove("focusing");
      Array.prototype.forEach.call(root.querySelectorAll(".gnode.me, .gnode.on"), function (n) {
        n.classList.remove("me", "on");
      });
    }

    ["mouseover", "focusin"].forEach(function (evt) {
      root.addEventListener(evt, function (ev) {
        var node = ev.target.closest && ev.target.closest(".gnode");
        if (node) focus(node.getAttribute("data-id"));
      });
    });
    ["mouseout", "focusout"].forEach(function (evt) {
      root.addEventListener(evt, function (ev) {
        var to = ev.relatedTarget;
        if (!to || !to.closest || !to.closest(".gnode")) clear();
      });
    });

    draw();
    if (window.ResizeObserver) new ResizeObserver(draw).observe(canvas);
    else window.addEventListener("resize", draw);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(draw);
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".hgraph"), setup);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
