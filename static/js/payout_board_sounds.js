/* The Payout Board — Web Audio API sound effects */

(function () {
  'use strict';

  let muted = localStorage.getItem('pb_muted') === '1';
  let audioUnlocked = false;

  // Unlock audio context on first user interaction (required on mobile)
  function unlock() {
    if (audioUnlocked) return;
    audioUnlocked = true;
  }
  document.addEventListener('click',     unlock, { once: true });
  document.addEventListener('touchstart', unlock, { once: true });

  function makeCtx() {
    try {
      return new (window.AudioContext || window.webkitAudioContext)();
    } catch (e) { return null; }
  }

  function playSeq(notes) {
    if (muted) return;
    const ctx = makeCtx();
    if (!ctx) return;
    let t = ctx.currentTime + 0.05;
    notes.forEach(([freq, dur, vol = 0.22, type = 'sine']) => {
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = type;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(vol, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + dur);
      osc.start(t);
      osc.stop(t + dur);
      t += dur + 0.02;
    });
  }

  // Tier closes — short descending dramatic (3 notes, ~0.8s)
  window.pbPlayTierClose = function () {
    playSeq([[660, 0.18, 0.25, 'square'], [495, 0.18, 0.22, 'square'], [330, 0.4, 0.28, 'square']]);
  };

  // Cash out confirmed — cash register ascending (2 notes, ~0.5s)
  window.pbPlayCashOut = function () {
    playSeq([[880, 0.15, 0.25], [1320, 0.3, 0.3]]);
  };

  // Let it ride — single mid chime (0.3s)
  window.pbPlayLetItRide = function () {
    playSeq([[660, 0.3, 0.2]]);
  };

  // Wildcard entry — ascending two-tone (0.4s)
  window.pbPlayWildcard = function () {
    playSeq([[523, 0.15, 0.2], [784, 0.22, 0.22]]);
  };

  // New cashout on board — subtle tick (0.2s)
  window.pbPlayTick = function () {
    playSeq([[1200, 0.08, 0.12, 'triangle'], [900, 0.1, 0.1, 'triangle']]);
  };

  // ── Mute toggle ────────────────────────────────────────
  window.pbInitMuteBtn = function (btnId) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.textContent = muted ? '🔇' : '🔊';
    btn.addEventListener('click', () => {
      muted = !muted;
      localStorage.setItem('pb_muted', muted ? '1' : '0');
      btn.textContent = muted ? '🔇' : '🔊';
    });
  };
})();
