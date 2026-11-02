/* The Payout Board, 30-second auto-refresh + live board rendering */

(function () {
  'use strict';

  const REFRESH_MS = 30 * 1000; // 30s so admin actions (Paid, Verify, DQ) appear quickly
  let prevTierFull = {};   // tier -> was full on last render
  let prevCashoutCount = 0;

  // ── Countdown timer ────────────────────────────────────

  function startCountdown(closeDateStr, daysEl, hoursEl, minsEl, secsEl) {
    if (!closeDateStr || !daysEl) return;
    const target = new Date(closeDateStr).getTime();

    function tick() {
      const diff = target - Date.now();
      if (diff <= 0) {
        [daysEl, hoursEl, minsEl, secsEl].forEach(el => { if (el) el.textContent = '00'; });
        return;
      }
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      if (daysEl)  daysEl.textContent  = String(d).padStart(2, '0');
      if (hoursEl) hoursEl.textContent = String(h).padStart(2, '0');
      if (minsEl)  minsEl.textContent  = String(m).padStart(2, '0');
      if (secsEl)  secsEl.textContent  = String(s).padStart(2, '0');
    }

    tick();
    setInterval(tick, 1000);
  }

  function startWildcardCountdown(seconds) {
    let remaining = seconds;
    const el = document.getElementById('pbWildcardTimer');
    if (!el || remaining <= 0) return;

    function tick() {
      if (remaining <= 0) { el.textContent = '00:00:00'; return; }
      const h = Math.floor(remaining / 3600);
      const m = Math.floor((remaining % 3600) / 60);
      const s = remaining % 60;
      el.textContent = [h, m, s].map(n => String(n).padStart(2, '0')).join(':');
      remaining--;
    }
    tick();
    setInterval(tick, 1000);
  }

  // ── Tier cards ─────────────────────────────────────────

  function renderDots(container, claimed, cap) {
    container.innerHTML = '';
    for (let i = 0; i < cap; i++) {
      const dot = document.createElement('div');
      dot.className = 'pb-dot' + (i < claimed ? ' claimed' : '');
      container.appendChild(dot);
    }
  }

  function applyTierData(tiers) {
    Object.entries(tiers).forEach(([t, data]) => {
      // data-tier in the DOM uses the tier name string (e.g. "Entry Level"),
      // while the API keys by number. Look up by name so they always match.
      const card = document.querySelector(`[data-tier="${data.name}"]`);
      if (!card) return;

      // Dots
      const dotsEl = card.querySelector('.pb-spot-dots');
      if (dotsEl) renderDots(dotsEl, data.claimed, data.cap);

      // Claimed count
      const claimedLabel = card.querySelector('.pb-tier-claimed-label');
      if (claimedLabel) {
        claimedLabel.textContent = `${data.claimed}/${data.cap} claimed`;
      }

      // Card open/closed styling + tier-close sound on the full transition.
      // Key by tier NAME to match the init seed (data-tier is the name), otherwise
      // wasFull is always undefined and the close sound replays for already-full tiers.
      const wasFull = prevTierFull[data.name] || false;
      if (data.full) {
        card.classList.remove('open'); card.classList.add('closed');
        if (!wasFull && window.pbPlayTierClose) pbPlayTierClose();
      } else {
        card.classList.remove('closed'); card.classList.add('open');
      }
      prevTierFull[data.name] = data.full;

      // Rebuild the single status badge deterministically so the 30s refresh mirrors
      // the server-rendered template EXACTLY. The previous code overwrote this element
      // to a bare "OPEN", permanently destroying scarcity / LOCKED / PLAYING NOW labels
      // (and the pb-tier-spots-label hook itself). Priority: full > locked > playing > spots.
      const badge = card.querySelector('.pb-tier-badge');
      if (badge) {
        const baseCls = 'pb-tier-badge self-start text-[9px] font-bold px-2.5 py-0.5 rounded-full tracking-widest';
        if (data.full) {
          badge.className = baseCls + ' closed-badge';
          badge.textContent = '⛔ TIER FULL';
          badge.style.background = 'rgba(239,68,68,0.12)';
          badge.style.color = '#f87171';
        } else if (data.locked) {
          badge.className = baseCls;
          badge.textContent = '🔒 LOCKED';
          badge.style.background = 'rgba(245,158,11,0.12)';
          badge.style.color = '#fbbf24';
        } else if (data.playing_now) {
          badge.className = baseCls + ' open';
          badge.innerHTML = '<span class="blink">●</span> PLAYING NOW';
          badge.style.background = 'rgba(159,232,34,0.1)';
          badge.style.color = '#9fe822';
        } else {
          badge.className = baseCls + ' open pb-tier-spots-label';
          badge.textContent = data.remaining === 1 ? '🔥 LAST SPOT'
                            : data.remaining === 2 ? '⚡ 2 SPOTS LEFT'
                            : `${data.remaining} SPOTS LEFT`;
          badge.style.background = 'rgba(159,232,34,0.1)';
          badge.style.color = data.remaining === 1 ? '#f87171'
                            : data.remaining === 2 ? '#fbbf24' : '#9fe822';
        }
      }
    });
  }

  // ── Cashout board ───────────────────────────────────────

  function renderCashouts(cashouts) {
    const tbody = document.getElementById('pbCashoutBody');
    if (!tbody) return;

    const isNew = cashouts.length > prevCashoutCount && prevCashoutCount > 0;
    if (isNew && window.pbPlayTick) pbPlayTick();
    prevCashoutCount = cashouts.length;

    const countEl = document.getElementById('pbCashoutCount');
    if (countEl) countEl.textContent = cashouts.length + ' CONFIRMED';

    if (cashouts.length === 0) {
      tbody.innerHTML = '<div class="text-xs text-slate-500 py-3">No confirmed cashouts yet. Be the first.</div>';
      return;
    }

    // Mirror the exact Jinja2 server-rendered structure so the layout never changes
    tbody.innerHTML = cashouts.map(c => {
      const bonusPart = c.mower_bonus > 0 ? ` + $${c.mower_bonus}` : '';
      const prizeStr  = `$${c.prize}${bonusPart}`;
      return `
        <div class="pb-cashout-row flex items-center gap-4 py-3 border-t border-white/5">
          <div class="pb-cashout-alias text-sm font-bold flex-1" style="color:#9fe822;">${esc(c.alias)}</div>
          <div class="pb-cashout-tier-pill text-xs text-slate-400">${esc(c.tier_name)}</div>
          <div class="text-xs text-slate-500 hidden md:block">${esc(c.platform)}</div>
          <div class="pb-cashout-prize text-sm font-black">${prizeStr}</div>
        </div>`;
    }).join('');
  }

  function esc(str) {
    return String(str || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Leaderboard ─────────────────────────────────────────

  function renderLeaderboard(entries) {
    const listEl  = document.getElementById('pbLeaderboardList');
    const countEl = document.getElementById('pbLeaderboardCount');
    if (!listEl) return;

    if (countEl) countEl.textContent = entries.length + ' Creator' + (entries.length !== 1 ? 's' : '');

    if (entries.length === 0) {
      listEl.innerHTML = '<div style="font-size:12px;color:#475569;text-align:center;padding:32px 0;">No players yet. Be the first!</div>';
      return;
    }

    listEl.innerHTML = entries.slice(0, 12).map((sub, idx) => {
      const rank      = idx + 1;
      const isCashed  = sub.cashed_out === 'Yes';
      const isOT      = !isCashed && (sub.has_played_minigame || sub.status === 'Overtime');
      const isChallenger = !isCashed && !isOT && !!sub.challenger_tier_name;
      const isActive  = !isCashed && !isOT && !isChallenger;
      const qualified = (sub.tier_prize || 0) > 0;
      const bonus     = sub.mower_bonus  || 0;
      const tierPrize = sub.tier_prize   || 0;
      const views     = (sub.impressions || 0).toLocaleString();
      const tier      = (sub.tier_name && sub.tier_name !== 'None') ? sub.tier_name : '';

      let borderColor = 'rgba(159,232,34,0.12)';
      if (isCashed)  borderColor = 'rgba(34,197,94,0.15)';
      else if (isOT) borderColor = 'rgba(245,158,11,0.15)';
      else if (isChallenger) borderColor = 'rgba(251,191,36,0.35)';

      const bonusBadge = bonus > 0
        ? `<span style="display:inline-flex;align-items:center;gap:3px;font-size:9px;font-weight:700;background:rgba(159,232,34,0.12);color:#9fe822;border-radius:5px;padding:2px 6px;white-space:nowrap;">🚜 +$${bonus}</span>`
        : '';

      const tierLine = tier
        ? `<div style="font-size:10px;color:#475569;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">${esc(sub.platform||'Unknown')} · ${views} views · <span style="color:#64748b;">${esc(tier)}</span></div>`
        : `<div style="font-size:10px;color:#475569;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">${esc(sub.platform||'Unknown')} · ${views} views</div>`;

      let statusHtml = '';
      let rightHtml  = '';
      if (isCashed) {
        statusHtml = `<span style="font-size:9px;font-weight:800;letter-spacing:.06em;background:rgba(34,197,94,.15);color:#4ade80;border-radius:6px;padding:3px 8px;white-space:nowrap;">CASHED OUT</span>`;
        rightHtml  = `<span style="font-size:14px;font-weight:900;color:#4ade80;white-space:nowrap;">$${(tierPrize + bonus).toLocaleString()}</span>`;
      } else if (isOT) {
        statusHtml = `<span style="font-size:9px;font-weight:800;letter-spacing:.06em;background:rgba(245,158,11,.15);color:#f59e0b;border-radius:6px;padding:3px 8px;white-space:nowrap;">⚡ OVERTIME</span>`;
        rightHtml  = `<span style="font-size:12px;color:#64748b;font-style:italic;">Riding...</span>`;
      } else if (isChallenger) {
        const cr = sub.challenger_rank || 1;
        statusHtml = `<span style="font-size:9px;font-weight:800;letter-spacing:.06em;background:rgba(251,191,36,.14);color:#fbbf24;border-radius:6px;padding:3px 8px;white-space:nowrap;">🎯 CHALLENGER${cr > 1 ? ' #' + cr : ''}</span>`;
        rightHtml  = `<span style="font-size:11px;font-weight:700;color:#fbbf24;white-space:nowrap;">${cr === 1 ? 'Next for ' : ''}${esc(sub.challenger_tier_name)} $${(sub.challenger_prize||0).toLocaleString()}</span>`;
      } else {
        statusHtml = `<span style="font-size:9px;font-weight:800;letter-spacing:.06em;background:rgba(159,232,34,.15);color:#9fe822;border-radius:6px;padding:3px 8px;white-space:nowrap;">ACTIVE</span>`;
        rightHtml  = qualified ? `<span style="font-size:9px;color:#334155;">→ Decision ready</span>` : '';
      }

      return `<div style="background:#0b1120;border:1px solid ${borderColor};border-radius:12px;padding:12px 14px;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:5px;">
          <span style="font-size:10px;font-weight:700;color:#334155;min-width:20px;">#${rank}</span>
          <span style="font-size:13px;font-weight:800;color:#f1f5f9;flex:1;">${esc(sub.alias)}</span>
          ${bonusBadge}
        </div>
        ${tierLine}
        <div style="display:flex;align-items:center;justify-content:space-between;gap:6px;">
          ${statusHtml}
          ${rightHtml}
        </div>
      </div>`;
    }).join('');
  }

  // ── Prize pool stats ────────────────────────────────────
  function renderPrizePool(stats) {
    const $ = id => document.getElementById(id);
    if ($('pbLockedIn')  && stats.locked_in  !== undefined) $('pbLockedIn').textContent  = '$' + stats.locked_in.toLocaleString();
    if ($('pbRemaining') && stats.remaining  !== undefined) $('pbRemaining').textContent  = '$' + stats.remaining.toLocaleString();
    if ($('pbBonusTotal') && stats.bonus_awarded !== undefined) $('pbBonusTotal').textContent = '$' + stats.bonus_awarded.toLocaleString();
  }

  // ── Overtime stats ───────────────────────────────────────
  function renderOvertimeStats(stats) {
    const $ = id => document.getElementById(id);
    if ($('pbOvertimeCount') && stats.players_in_overtime !== undefined) $('pbOvertimeCount').textContent = stats.players_in_overtime;
    if ($('pbGamesPlayed')   && stats.games_played        !== undefined) $('pbGamesPlayed').textContent   = stats.games_played;
    if ($('pbBonusAwarded')  && stats.total_bonus         !== undefined) $('pbBonusAwarded').textContent  = '$' + stats.total_bonus.toLocaleString();
  }

  // ── Activity feed ────────────────────────────────────────
  function renderActivityFeed(feed) {
    const el = document.getElementById('pbActivityFeed');
    if (!el || !Array.isArray(feed)) return;
    if (feed.length === 0) {
      el.innerHTML = '<div style="font-size:11px;color:#334155;text-align:center;padding:12px 0;">No activity yet. Be the first!</div>';
      return;
    }
    const colorMap = { cashout: '#4ade80', bonus: '#9fe822', overtime: '#f59e0b', qualified: '#64748b' };
    el.innerHTML = feed.map((ev, i) =>
      `<div style="font-size:11px;color:${colorMap[ev.type]||'#64748b'};padding:7px 0;line-height:1.4;${i > 0 ? 'border-top:1px solid rgba(255,255,255,0.04);' : ''}">${esc(ev.text)}</div>`
    ).join('');
  }

  // ── Main refresh ────────────────────────────────────────

  function refresh() {
    fetch('/payout-board/api/board-data')
      .then(r => r.json())
      .then(data => {
        if (!data.visible) return;
        if (data.tiers)       applyTierData(data.tiers);
        if (data.cashouts)    renderCashouts(data.cashouts);
        if (data.leaderboard) renderLeaderboard(data.leaderboard);
        if (data.prize_pool_stats) renderPrizePool(data.prize_pool_stats);
        if (data.overtime_stats)   renderOvertimeStats(data.overtime_stats);
        if (data.activity_feed)    renderActivityFeed(data.activity_feed);

        // Countdown is owned by the server-rendered DOM + the init handler below.
        // The 30s refresh must NOT start its own timer: during the pre-launch window
        // the target is the START date, but data.close_date is the END date. Starting
        // a second interval here would make the clock fight itself and jump ahead.

        // Wildcard panel
        const wcPanel = document.getElementById('pbWildcardPanel');
        if (wcPanel) {
          wcPanel.style.display = data.wildcard_open ? 'block' : 'none';
        }
        const wcSpotsEl = document.getElementById('pbWildcardSpotsLeft');
        if (wcSpotsEl && data.wildcard_spots_left !== undefined) {
          wcSpotsEl.textContent = data.wildcard_spots_left;
        }

        // Status banner (game-ended or cash-prizes-locked-in) — mirror the server flags so
        // the 30s poll never wipes the server-rendered banner. Content is server-rendered;
        // here we only toggle visibility.
        const lockedBanner = document.getElementById('pbLockedBanner');
        if (lockedBanner && lockedBanner.children.length) {
          lockedBanner.style.display = (data.game_locked || data.cashouts_locked) ? 'flex' : 'none';
        }
      })
      .catch(() => { /* silent */ });
  }

  // ── Init ────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', () => {
    // Record initial tier state from server-rendered HTML
    document.querySelectorAll('[data-tier]').forEach(card => {
      prevTierFull[card.dataset.tier] = card.classList.contains('closed');
    });

    // Count initial cashouts
    const body = document.getElementById('pbCashoutBody');
    if (body) prevCashoutCount = body.querySelectorAll('.pb-cashout-row').length;

    // Campaign countdown from the server-rendered data attribute (the single source of
    // truth for the target date. START date pre-launch, END date once live).
    const cdEl = document.getElementById('pbCountdown');
    if (cdEl && cdEl.dataset.closeDate) {
      window._pbCountdownStarted = true;  // claim it so refresh() never double-starts
      startCountdown(
        cdEl.dataset.closeDate,
        document.getElementById('pbDays'),
        document.getElementById('pbHours'),
        document.getElementById('pbMins'),
        document.getElementById('pbSecs'),
      );
    }

    // Wildcard countdown from data attribute
    const wcEl = document.getElementById('pbWildcardTimer');
    if (wcEl && wcEl.dataset.seconds) {
      startWildcardCountdown(parseInt(wcEl.dataset.seconds, 10));
    }

    // Mute button
    if (window.pbInitMuteBtn) pbInitMuteBtn('pbMuteBtn');

    // Accordion
    document.querySelectorAll('.pb-accordion-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        btn.classList.toggle('open');
        const body = btn.nextElementSibling;
        if (body) body.classList.toggle('open');
      });
    });

    setInterval(refresh, REFRESH_MS);
  });
})();
