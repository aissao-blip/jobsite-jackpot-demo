/* Hit the Board — Spin wheel */

(function () {
  'use strict';

  // Wheel has 8 segments alternating: win (gold) / lose (navy)
  // Segment 0, 2, 4, 6 = "BETTER LUCK NEXT TIME"  (navy)
  // Segment 1, 3, 5, 7 = "$50 BONUS"              (gold)
  // Pointer is at top (270° in canvas coords = 12 o'clock)
  // Segment centres (mid-angle) counting from 0° at 3-o'clock:
  //   Seg 0 centre = 22.5°, seg 1 = 67.5°, ... seg N = 22.5 + N*45

  const SEGMENTS = 8;
  const SEG_ANGLE = 360 / SEGMENTS; // 45°

  // Which segment indices are wins (0-based)
  const WIN_SEGMENTS  = [1, 3, 5, 7];
  const LOSE_SEGMENTS = [0, 2, 4, 6];

  class SpinWheel {
    constructor(canvasId) {
      this.canvas = document.getElementById(canvasId);
      if (!this.canvas) return;
      this.ctx = this.canvas.getContext('2d');
      this.rotation = 0; // current rotation in degrees
      this.spinning = false;
      this.draw(this.rotation);
    }

    draw(rotDeg) {
      const c = this.canvas;
      const ctx = this.ctx;
      const cx = c.width / 2;
      const cy = c.height / 2;
      const r  = Math.min(cx, cy) - 4;

      ctx.clearRect(0, 0, c.width, c.height);

      for (let i = 0; i < SEGMENTS; i++) {
        const startAngle = ((rotDeg + i * SEG_ANGLE) * Math.PI) / 180;
        const endAngle   = ((rotDeg + (i + 1) * SEG_ANGLE) * Math.PI) / 180;
        const isWin = WIN_SEGMENTS.includes(i);

        // Segment fill
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.arc(cx, cy, r, startAngle, endAngle);
        ctx.closePath();
        ctx.fillStyle = isWin ? '#FFD700' : '#0D1F3C';
        ctx.fill();

        // Segment border
        ctx.strokeStyle = '#B8960C';
        ctx.lineWidth = 2;
        ctx.stroke();

        // Label
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(startAngle + (SEG_ANGLE * Math.PI) / 360);
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = isWin ? '#0A1628' : '#FFD700';
        ctx.font = `bold ${Math.floor(r * 0.11)}px Georgia, serif`;

        const textR = r * 0.68;
        if (isWin) {
          ctx.fillText('$50', textR, -6);
          ctx.font = `${Math.floor(r * 0.09)}px Georgia, serif`;
          ctx.fillText('BONUS', textR, 8);
        } else {
          ctx.font = `${Math.floor(r * 0.075)}px sans-serif`;
          ctx.fillText('BETTER LUCK', textR, -6);
          ctx.fillText('NEXT TIME', textR, 8);
        }
        ctx.restore();
      }

      // Centre circle
      ctx.beginPath();
      ctx.arc(cx, cy, r * 0.12, 0, Math.PI * 2);
      ctx.fillStyle = '#FFD700';
      ctx.fill();
      ctx.strokeStyle = '#B8960C';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Outer ring
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.strokeStyle = '#FFD700';
      ctx.lineWidth = 4;
      ctx.stroke();
    }

    spin(won, onComplete) {
      if (this.spinning) return;
      this.spinning = true;

      // Pick a target segment
      const targetSegments = won ? WIN_SEGMENTS : LOSE_SEGMENTS;
      const targetSeg = targetSegments[Math.floor(Math.random() * targetSegments.length)];

      // The pointer is at 270° (top). In our coord system the pointer lands on
      // the segment whose arc contains 270°. We want the mid-point of targetSeg
      // to land at 270° after rotation.
      // Mid-angle of seg i (before rotation) = i * 45 + 22.5  (from 3 o'clock)
      const segMid = targetSeg * SEG_ANGLE + SEG_ANGLE / 2;
      // We need: (this.rotation + segMid) % 360 == 270
      // => additional rotation needed to bring segMid to 270
      const extraNeeded = ((270 - (this.rotation + segMid)) % 360 + 360) % 360;
      // Spin at least 5 full rotations + the extra
      const totalRotation = 5 * 360 + extraNeeded;
      const finalRotation = this.rotation + totalRotation;

      const duration = 5000; // ms
      const startTime = performance.now();
      const startRot = this.rotation;
      const self = this;

      function easeOut(t) {
        // cubic ease-out
        return 1 - Math.pow(1 - t, 3);
      }

      function frame(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = easeOut(progress);
        self.rotation = startRot + totalRotation * eased;
        self.draw(self.rotation % 360);

        if (progress < 1) {
          requestAnimationFrame(frame);
        } else {
          self.rotation = finalRotation % 360;
          self.spinning = false;
          if (onComplete) onComplete(won);
        }
      }

      requestAnimationFrame(frame);
    }
  }

  // ── Init on DOM ready ─────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    const canvas = document.getElementById('htbWheelCanvas');
    if (!canvas) return;

    // Set canvas resolution for crisp rendering
    const size = 320;
    canvas.width = size;
    canvas.height = size;

    const wheel = new SpinWheel('htbWheelCanvas');
    const spinBtn = document.getElementById('htbSpinBtn');
    const resultText = document.getElementById('htbSpinResultText');
    const resultSub = document.getElementById('htbSpinResultSub');
    const token = canvas.dataset.token || '';

    if (!spinBtn) return;

    spinBtn.addEventListener('click', () => {
      if (wheel.spinning) return;
      spinBtn.disabled = true;
      spinBtn.textContent = 'SPINNING…';

      fetch('/hit-the-board/api/spin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      })
        .then((r) => r.json())
        .then((data) => {
          if (data.error) {
            spinBtn.textContent = 'SPIN UNAVAILABLE';
            return;
          }
          wheel.spin(data.won, (won) => {
            if (won) {
              if (window.playCelebration) window.playCelebration();
              if (resultText) {
                resultText.textContent = 'YOU WON $50!';
                resultText.className = 'htb-spin-result-text won';
              }
              if (resultSub) {
                resultSub.textContent =
                  'Your $50 bonus will be paid via the payout provider in the next pay period along with any tier prize you earned.';
              }
            } else {
              if (window.playSoftChime) window.playSoftChime();
              if (resultText) {
                resultText.textContent = 'Better luck next time!';
                resultText.className = 'htb-spin-result-text lost';
              }
              if (resultSub) {
                resultSub.textContent = 'Not this time. Good luck on the board!';
              }
            }
          });
        })
        .catch(() => {
          spinBtn.disabled = false;
          spinBtn.textContent = 'SPIN THE WHEEL';
        });
    });
  });
})();
