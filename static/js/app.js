/*
 * Browser behaviour for the experiment demo.
 *
 * Deliberately small. Unlike the "Part 2 Target" demo there is no live stream
 * here: an experiment's allocation is fixed for a given context key, so there
 * is nothing to push down to the page. Switching visitor is a plain link, and
 * a full page load is honest about what is happening — a new visitor arrives,
 * the server evaluates the flag for them, and that evaluation is the exposure.
 *
 * The only interactive part is sending the conversion metric.
 */

(function () {
  "use strict";

  var state = window.__STATE__ || {};

  // ---------------------------------------------------------------------
  // Toast
  // ---------------------------------------------------------------------

  var toastEl = document.getElementById("toast");
  var toastTimer = null;

  function toast(message, isError) {
    if (!toastEl) return;
    toastEl.textContent = message;
    toastEl.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.className = "toast";
    }, 6000);
  }

  // ---------------------------------------------------------------------
  // The conversion event
  // ---------------------------------------------------------------------

  function sendConversion() {
    return fetch("/api/cta-click", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The server re-evaluates the flag for this same visitor before tracking,
      // so the event is attributed to the arm they were actually exposed to.
      body: JSON.stringify({ visitor: state.visitor, key: state.key })
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        toast(data.message, !data.inExperiment);
        updateTally(data.tally);

        var result = document.getElementById("cta-result");
        if (result) {
          result.textContent = data.inExperiment
            ? "Last event counted toward the '" + data.variation + "' arm."
            : "Last event was sent but not attributed — this visitor is not in the experiment.";
        }
      })
      .catch(function () {
        toast("Could not reach the server — is app.py still running?", true);
      });
  }

  function updateTally(tally) {
    var table = document.getElementById("tally");
    if (!table || !tally) return;

    var body = table.querySelector("tbody");
    var arms = Object.keys(tally).sort();
    if (!arms.length) return;

    body.innerHTML = arms.map(function (arm) {
      var counts = tally[arm];
      return "<tr><td><code>" + arm + "</code></td><td>" +
        counts.exposures + "</td><td>" + counts.conversions + "</td></tr>";
    }).join("");
  }

  // The hero is re-rendered by the server on every page load, but binding by
  // delegation costs nothing and survives any future in-place swap.
  document.addEventListener("click", function (event) {
    var target = event.target;

    if (target.id === "hero-cta" || target.id === "send-conversion") {
      event.preventDefault();
      sendConversion();
      return;
    }

    // Offline-mode only: start/stop the in-memory experiment.
    var toggle = target.getAttribute && target.getAttribute("data-offline-experiment");
    if (toggle !== null && toggle !== undefined) {
      event.preventDefault();
      fetch("/api/offline/experiment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ running: toggle === "true" })
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          toast(data.message + " Reloading…");
          setTimeout(function () { window.location.reload(); }, 900);
        })
        .catch(function () {
          toast("Could not reach the server.", true);
        });
    }
  });
})();
