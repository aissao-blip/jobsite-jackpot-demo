/**
 * payout_board_cashout.js
 * Handles inline check-status on the live board.
 * Shows full private player profile. Only visible to the BP who entered their code.
 * Public leaderboard shows only alias / tier / platform (handled server-side in template).
 */
(function () {
  'use strict';

  function pbEsc(str) {
    return String(str || '').replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])
    );
  }

  function isValidCode(val) {
    return /^JSJ-[A-Z0-9]{7}$/i.test(val) || /^[A-Z0-9]{4}-[A-Z0-9]{4}$/i.test(val);
  }

  function fmt(n) { return Number(n || 0).toLocaleString(); }

  /* ── Main status check ─────────────────────────────────────────────────── */

  window.pbCheckStatus = function () {
    const input    = document.getElementById('pb-code-input');
    const resultEl = document.getElementById('pb-status-result');
    if (!input || !resultEl) return;
    const raw = input.value.trim().toUpperCase();
    if (!raw) return;
    if (!isValidCode(raw)) {
      resultEl.style.display = 'block';
      resultEl.innerHTML = `<div style="color:#ef4444;font-size:13px;">Invalid code format. Use <strong>JSJ-ABC1234</strong>.</div>`;
      return;
    }
    resultEl.style.display = 'block';
    resultEl.innerHTML = '<div style="color:#64748b;font-size:13px;">Checking…</div>';

    fetch('/payout-board/check-status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: raw }),
    })
    .then(r => r.json())
    .then(data => {
      if (data.error) {
        resultEl.innerHTML = `<div style="color:#ef4444;font-size:13px;">${pbEsc(data.error)}</div>`;
        return;
      }
      if (!data.found) {
        resultEl.innerHTML = `<div style="color:#64748b;font-size:13px;">${pbEsc(data.message)}</div>`;
        return;
      }

      // ── Already cashed out ────────────────────────────────────────────────
      if (data.already_cashed_out) {
        resultEl.innerHTML = `
          <div style="background:#0a0e1a;border:1px solid rgba(34,197,94,0.2);border-radius:12px;padding:20px;">
            <div style="font-size:10px;letter-spacing:2px;color:#64748b;text-transform:uppercase;margin-bottom:6px;">PRIVATE PROFILE</div>
            <div style="font-size:22px;font-weight:900;color:#4ade80;margin-bottom:4px;">💰 Cashed Out</div>
            <div style="font-size:16px;font-weight:800;color:#f1f5f9;margin-bottom:10px;">${pbEsc(data.alias)}</div>
            <div style="font-size:13px;color:#64748b;margin-bottom:4px;">Tier: <strong style="color:#9fe822;">${pbEsc(data.tier_name)}</strong> ($${fmt(data.prize)})</div>
            <div style="font-size:13px;color:#64748b;">Payment processing via the payout provider.</div>
          </div>`;
        return;
      }

      // ── Active player. YOUR STATUS + NEXT MOVE card ─────────────────────
      const code       = data.plus_code || raw;
      const cashoutUrl = `/payout-board/cashout?code=${encodeURIComponent(code)}`;
      const bonusBank  = Number(data.bonus_bank || 0);
      const views      = Number(data.impressions || 0);
      const qualified  = !!data.qualified;

      // Status label (plain English, not badge)
      let statusLabel = '';
      let statusColor = '#64748b';
      if (data.status === 'Disqualified') {
        statusLabel = '⛔ Disqualified';
        statusColor = '#f87171';
      } else if (qualified) {
        statusLabel = '🟢 Eligible for Decision';
        statusColor = '#9fe822';
      } else if (data.tier > 0 && data.tier_full) {
        statusLabel = '⚡ Tier Full. Keep Growing';
        statusColor = '#f59e0b';
      } else if (data.overtime_active) {
        statusLabel = '⚡ In Overtime. Game Active';
        statusColor = '#f59e0b';
      } else if (data.has_played_minigame) {
        statusLabel = '🎮 Mini-Game Played. Grow to Unlock Next';
        statusColor = '#94a3b8';
      } else {
        statusLabel = '🎮 Active. Keep Growing';
        statusColor = '#94a3b8';
      }

      // Tier row for the status table (only if they have one)
      const tierRow = (data.tier_name && data.tier > 0) ? `
        <div style="display:flex;justify-content:space-between;padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px;">
          <span style="color:#94a3b8;">Current Tier:</span>
          <span style="color:#9fe822;font-weight:700;">${pbEsc(data.tier_name)} ($${fmt(data.prize)})</span>
        </div>` : '';

      // Bonus bank row
      const bonusRow = bonusBank > 0 ? `
        <div style="display:flex;justify-content:space-between;padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px;">
          <span style="color:#94a3b8;">🏦 Bonus Bank:</span>
          <span style="color:#9fe822;font-weight:700;">+$${fmt(bonusBank)} permanent</span>
        </div>` : '';

      // NEXT MOVE section
      let nextMove = '';
      if (qualified && data.tier_name) {
        const spotsMsg = data.spots_remaining > 0
          ? `${data.spots_remaining} spot${data.spots_remaining !== 1 ? 's' : ''} still open`
          : 'Tier full. Keep growing';
        nextMove = `
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;margin-top:16px;">
            <div style="font-size:10px;letter-spacing:2px;color:#9fe822;text-transform:uppercase;font-weight:700;margin-bottom:12px;">YOUR NEXT MOVE</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.7;margin-bottom:14px;">
              You qualify for <strong style="color:#fff;">${pbEsc(data.tier_name)}</strong>. Choose your path:
              <div style="margin-top:8px;padding-left:10px;border-left:2px solid rgba(159,232,34,0.3);">
                <div style="margin-bottom:4px;">💰 <strong style="color:#fff;">Cash Out</strong>: lock in your guaranteed $${fmt(data.prize)}${bonusBank > 0 ? ` + $${fmt(bonusBank)} bonus` : ''} payout</div>
                <div style="color:#64748b;font-size:11px;margin-bottom:10px;">OR</div>
                <div>⚡ <strong style="color:#fff;">Go Overtime</strong>: release your spot, play the mower game for a +$100 permanent bonus</div>
              </div>
            </div>
            <a href="${pbEsc(cashoutUrl)}"
               style="display:block;width:100%;padding:14px;background:#9fe822;color:#000;font-weight:900;
                      font-size:13px;letter-spacing:1px;text-transform:uppercase;border-radius:8px;
                      text-align:center;text-decoration:none;box-sizing:border-box;">
              GO TO DECISION PAGE →
            </a>
            <div style="font-size:11px;color:#475569;text-align:center;margin-top:8px;">${pbEsc(spotsMsg)}</div>
          </div>`;
      } else if (data.tier > 0 && data.tier_full) {
        nextMove = `
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;margin-top:16px;">
            <div style="font-size:10px;letter-spacing:2px;color:#f59e0b;text-transform:uppercase;font-weight:700;margin-bottom:12px;">YOUR NEXT MOVE</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.7;">
              <strong style="color:#fff;">${pbEsc(data.tier_name)}</strong> is full. Keep growing your views to unlock a higher tier. Spots open up when other players go overtime.
            </div>
          </div>`;
      } else if (data.overtime_active && data.can_play_game) {
        nextMove = `
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;margin-top:16px;">
            <div style="font-size:10px;letter-spacing:2px;color:#f59e0b;text-transform:uppercase;font-weight:700;margin-bottom:12px;">YOUR NEXT MOVE</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.7;margin-bottom:14px;">
              ⚡ You're in <strong style="color:#f59e0b;">Overtime</strong>. Head to your decision page to play the Perfect Stripes mini-game and earn your +$100 bonus.
            </div>
            <a href="${pbEsc(cashoutUrl)}"
               style="display:block;width:100%;padding:14px;background:#f59e0b;color:#000;font-weight:900;
                      font-size:13px;letter-spacing:1px;text-transform:uppercase;border-radius:8px;
                      text-align:center;text-decoration:none;box-sizing:border-box;">
              GO TO DECISION PAGE →
            </a>
          </div>`;
      } else {
        const viewsNeeded = Math.max(500 - views, 0);
        nextMove = `
          <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:16px;margin-top:16px;">
            <div style="font-size:10px;letter-spacing:2px;color:#94a3b8;text-transform:uppercase;font-weight:700;margin-bottom:12px;">YOUR NEXT MOVE</div>
            <div style="font-size:13px;color:#94a3b8;line-height:1.7;">
              ${viewsNeeded > 0
                ? `You need <strong style="color:#fff;">${fmt(viewsNeeded)} more views</strong> to unlock Entry Level ($150). Keep promoting your post.`
                : `Keep growing your views. When you hit a new tier threshold, come back with your Player Code to claim your spot.`
              }
              <div style="margin-top:10px;font-size:12px;color:#64748b;">
                Tiers: <strong style="color:#fff;">500 views</strong> = $150 &nbsp;·&nbsp;
                <strong style="color:#fff;">2,000</strong> = $400 &nbsp;·&nbsp;
                <strong style="color:#fff;">4,000</strong> = $600 &nbsp;·&nbsp;
                <strong style="color:#fff;">7,000</strong> = $1,500
              </div>
            </div>
          </div>`;
      }

      // Submission history (shown when BP has submitted at least one update)
      const hasUpdate = Number(data.co_impressions || 0) > 0;
      const historySection = hasUpdate ? `
        <div style="margin-top:12px;padding:10px 14px;background:rgba(255,255,255,0.02);
                    border-radius:8px;border:1px solid rgba(255,255,255,0.05);">
          <div style="font-size:10px;letter-spacing:2px;color:#64748b;text-transform:uppercase;
                      font-weight:700;margin-bottom:8px;">SUBMISSION HISTORY</div>
          <div style="display:grid;grid-template-columns:1fr auto;gap:4px 16px;font-size:12px;">
            <span style="color:#64748b;">Original Submission</span>
            <span style="color:#94a3b8;text-align:right;font-family:monospace;">${fmt(data.original_impressions)} views</span>
            <span style="color:#9fe822;font-weight:700;">Latest Update ✓</span>
            <span style="color:#f1f5f9;font-weight:700;text-align:right;font-family:monospace;">${fmt(data.co_impressions)} views</span>
          </div>
        </div>` : '';

      // Update views form
      const updateSection = `
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(255,255,255,0.06);">
          <button onclick="pbToggleUpdateForm()" style="font-size:12px;color:#9fe822;background:none;border:none;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:3px;">
            📤 Submit updated views &amp; screenshot to unlock a higher tier →
          </button>
          <div id="pb-update-form" style="display:none;margin-top:12px;">
            <input type="hidden" id="pb-update-code" value="${pbEsc(code)}">
            <div style="display:flex;flex-direction:column;gap:8px;">
              <input id="pb-update-views" type="number" min="1" placeholder="Updated view count"
                     style="width:100%;background:#080d1a;border:1px solid rgba(255,255,255,0.12);color:#fff;
                            padding:9px 12px;border-radius:8px;font-size:13px;outline:none;box-sizing:border-box;">
              <div id="pb-update-drop" onclick="document.getElementById('pb-update-file').click()"
                   style="border:2px dashed rgba(255,255,255,0.12);border-radius:10px;padding:14px;
                          text-align:center;cursor:pointer;font-size:12px;color:#64748b;transition:border-color .2s;"
                   onmouseover="this.style.borderColor='rgba(159,232,34,0.4)'"
                   onmouseout="this.style.borderColor='rgba(255,255,255,0.12)'">
                📸 <strong style="color:#e2e8f0;">Click to upload</strong> or drag &amp; drop new screenshot
                <div id="pb-update-fname" style="color:#9fe822;font-size:11px;margin-top:4px;"></div>
              </div>
              <input type="file" id="pb-update-file" accept="image/*" style="display:none;"
                     onchange="document.getElementById('pb-update-fname').textContent = this.files[0]?.name || ''">
              <button onclick="pbSubmitUpdate()"
                      style="width:100%;background:#9fe822;color:#000;font-weight:900;font-size:12px;
                             letter-spacing:1px;text-transform:uppercase;padding:11px;border-radius:8px;
                             border:none;cursor:pointer;">
                SUBMIT UPDATED VIEWS
              </button>
              <div id="pb-update-result" style="display:none;font-size:12px;text-align:center;padding:6px 0;"></div>
            </div>
          </div>
        </div>`;

      resultEl.innerHTML = `
        <div style="background:#0a0e1a;border:1px solid rgba(255,255,255,0.08);border-radius:12px;padding:20px;">

          <!-- YOUR STATUS section -->
          <div style="font-size:10px;letter-spacing:2px;color:#64748b;text-transform:uppercase;font-weight:700;margin-bottom:10px;">YOUR STATUS</div>

          <!-- Identity row -->
          <div style="margin-bottom:12px;">
            <div style="font-size:22px;font-weight:900;color:#f1f5f9;margin-bottom:2px;">${pbEsc(data.alias)}</div>
            <div style="font-size:11px;color:#475569;font-family:monospace;letter-spacing:1px;">${pbEsc(code)}</div>
          </div>

          <!-- Stats table -->
          <div style="border:1px solid rgba(255,255,255,0.05);border-radius:8px;overflow:hidden;margin-bottom:4px;">
            <div style="display:flex;justify-content:space-between;padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px;">
              <span style="color:#94a3b8;">Submitted Views:</span>
              <span style="color:#f1f5f9;font-weight:600;">${fmt(views)} views</span>
            </div>
            ${tierRow}
            ${bonusRow}
            <div style="display:flex;justify-content:space-between;padding:9px 14px;font-size:13px;">
              <span style="color:#94a3b8;">Status:</span>
              <span style="color:${statusColor};font-weight:700;">${statusLabel}</span>
            </div>
          </div>

          ${historySection}
          ${nextMove}
          ${updateSection}

          <div style="margin-top:12px;font-size:11px;color:#334155;text-align:center;">
            🔒 This profile is only visible to you. Public board shows your alias only.
          </div>
          <div style="margin-top:10px;text-align:center;">
            <button onclick="pbClearStatus()"
                    style="font-size:11px;color:#475569;background:none;border:none;cursor:pointer;text-decoration:underline;text-underline-offset:2px;padding:0;">
              Check a different code
            </button>
          </div>
        </div>`;
    })
    .catch(() => {
      resultEl.innerHTML = `<div style="color:#ef4444;font-size:13px;">Connection error. Try again.</div>`;
    });
  };

  /* ── Update views toggle & submit ─────────────────────────────────────── */

  window.pbToggleUpdateForm = function () {
    const form = document.getElementById('pb-update-form');
    if (!form) return;
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
  };

  window.pbSubmitUpdate = function () {
    const code     = (document.getElementById('pb-update-code')?.value || '').trim();
    const views    = document.getElementById('pb-update-views')?.value?.trim();
    const fileEl   = document.getElementById('pb-update-file');
    const resultEl = document.getElementById('pb-update-result');
    const btn      = document.querySelector('#pb-update-form button[onclick="pbSubmitUpdate()"]');

    if (!views || parseInt(views) <= 0) {
      if (resultEl) { resultEl.style.display='block'; resultEl.style.color='#ef4444'; resultEl.textContent='❌ Enter a valid view count.'; }
      return;
    }
    if (!fileEl || !fileEl.files[0]) {
      if (resultEl) { resultEl.style.display='block'; resultEl.style.color='#ef4444'; resultEl.textContent='❌ Screenshot is required.'; }
      return;
    }

    if (btn) btn.textContent = 'SUBMITTING…';
    const fd = new FormData();
    fd.append('code',            code);
    fd.append('impressions',     views);
    fd.append('screenshot_file', fileEl.files[0]);

    fetch('/payout-board/update-views', { method: 'POST', body: fd })
    .then(r => r.json())
    .then(d => {
      if (d.ok) {
        // Success. Brief confirmation then re-run full status check so the
        // card immediately shows the new tier, views, and submission history.
        if (btn) btn.textContent = 'SUBMITTED ✓';
        if (resultEl) {
          resultEl.style.display = 'block';
          resultEl.style.color   = '#9fe822';
          resultEl.textContent   = '✅ Views updated! Loading your new status…';
        }
        // Put code back into the main input so pbCheckStatus() can read it
        const codeInput = document.getElementById('pb-code-input');
        if (codeInput) codeInput.value = code;
        setTimeout(() => { window.pbCheckStatus(); }, 700);
      } else {
        if (resultEl) {
          resultEl.style.display = 'block';
          resultEl.style.color   = '#ef4444';
          resultEl.textContent   = '❌ ' + (d.error || 'Update failed. Try again.');
        }
        if (btn) btn.textContent = 'SUBMIT UPDATED VIEWS';
      }
    })
    .catch(() => {
      if (resultEl) { resultEl.style.display='block'; resultEl.style.color='#ef4444'; resultEl.textContent='❌ Connection error. Try again.'; }
      if (btn) btn.textContent = 'SUBMIT UPDATED VIEWS';
    });
  };

  /* ── Clear status (check another code) ───────────────────────────────── */

  window.pbClearStatus = function () {
    const resultEl = document.getElementById('pb-status-result');
    const input    = document.getElementById('pb-code-input');
    if (resultEl) { resultEl.style.display = 'none'; resultEl.innerHTML = ''; }
    if (input)    { input.value = ''; input.focus(); }
  };

  /* ── Input auto-format ────────────────────────────────────────────────── */

  document.addEventListener('DOMContentLoaded', () => {
    const inp = document.getElementById('pb-code-input');
    if (inp) {
      inp.addEventListener('input', () => { inp.value = inp.value.toUpperCase(); });
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') window.pbCheckStatus(); });
    }
  });
})();
