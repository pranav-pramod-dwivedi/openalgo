const rangeData = {
  '1D': { value: '+$1,420.20', caption: 'since 09:00 today', path: 'M0 185 C32 174 42 188 70 164 S117 169 145 143 S185 153 214 131 S255 138 282 111 S327 117 353 95 S392 103 425 78 S468 82 494 62 S538 73 570 53 S614 43 642 54 S688 33 716 39 S754 19 780 25' },
  '1W': { value: '+$4,892.70', caption: 'since Sep 25, 2026', path: 'M0 201 C32 204 42 183 70 191 S117 172 145 176 S185 146 214 157 S255 137 282 140 S327 107 353 125 S392 91 425 115 S468 73 494 86 S538 62 570 75 S614 43 642 52 S688 31 716 40 S754 16 780 25' },
  '1M': { value: '+$12,460.90', caption: 'since Sep 02, 2026', path: 'M0 208 C32 202 41 181 71 188 S117 168 144 175 S185 143 214 157 S254 132 281 139 S326 117 352 130 S393 97 425 114 S468 83 493 90 S538 67 569 76 S614 38 641 52 S687 32 716 40 S752 18 780 25' },
  '3M': { value: '+$19,842.35', caption: 'since Jul 02, 2026', path: 'M0 219 C32 208 41 220 71 193 S117 205 144 174 S185 186 214 163 S254 153 281 141 S326 150 352 117 S393 123 425 101 S468 115 493 83 S538 93 569 73 S614 79 641 44 S687 54 716 32 S752 39 780 19' },
  '1Y': { value: '+$42,164.60', caption: 'since Oct 02, 2025', path: 'M0 231 C32 239 41 219 71 223 S117 201 144 207 S185 172 214 185 S254 151 281 164 S326 135 352 147 S393 116 425 127 S468 106 493 114 S538 81 569 98 S614 61 641 73 S687 47 716 62 S752 26 780 25' }
};

const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
let toastTimer;

function updateMoment() {
  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const copy = hour < 12
    ? 'Your capital is working quietly in the background.'
    : hour < 18
      ? 'The market is moving. Your strategy is staying composed.'
      : 'The day is closing. Your portfolio is still being watched.';
  const date = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(now);
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(now);
  $('#dateLabel').textContent = date;
  $('#timeLabel').textContent = time;
  $('#greetingLabel').textContent = greeting;
  $('#introCopy').textContent = copy;
}

updateMoment();
setInterval(updateMoment, 30000);

function showToast(message) {
  const toast = $('#toast');
  $('#toastText').textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

function setSection(section) {
  $$('.nav-item, .mobile-nav-item, .mobile-toolbar-item').forEach((item) => item.classList.toggle('active', item.dataset.section === section));
  $('#pageLabel').textContent = section;
  document.title = `PranavPay — ${section}`;
  const targetId = { 'Quick overview': 'quickOverviewPage', Wallet: 'walletPage', Transactions: 'transactionsPage', Manage: 'managePage', 'Account settings': 'accountPage' }[section] || 'quickOverviewPage';
  $$('.page-view').forEach((page) => {
    const isTarget = page.id === targetId;
    if (isTarget) {
      page.hidden = false;
      requestAnimationFrame(() => page.classList.add('is-visible'));
    } else {
      page.classList.remove('is-visible');
      page.hidden = true;
    }
  });
  if (section !== 'Quick overview') showToast(`${section} is ready in your workspace`);
}

$$('[data-section]').forEach((item) => item.addEventListener('click', () => setSection(item.dataset.section)));

const pathSections = { '/quick-overview': 'Quick overview', '/wallet': 'Wallet', '/transactions': 'Transactions', '/manage': 'Manage', '/account': 'Account settings' };
const initialSection = pathSections[window.location.pathname];
if (initialSection) setSection(initialSection);

$$('[data-range]').forEach((button) => {
  button.addEventListener('click', () => {
    const range = button.dataset.range;
    $$('.range-button').forEach((item) => item.classList.toggle('active', item === button));
    const data = rangeData[range];
    $('.chart-value').textContent = data.value;
    $('.chart-caption').textContent = data.caption;
    const line = $('.chart-line');
    line.setAttribute('d', data.path);
    line.style.animation = 'none';
    requestAnimationFrame(() => {
      line.style.animation = '';
    });
    showToast(`Performance view set to ${range}`);
  });
});

$$('[data-decision]').forEach((button) => {
  button.addEventListener('click', () => {
    vibrate(6);
    const entry = button.closest('.decision-entry');
    const isOpen = entry.classList.toggle('open');
    button.querySelector('.decision-arrow').textContent = isOpen ? '⌃' : '⌄';
  });
});

const aiCard = $('.ai-card');
const aiStatusText = $('#aiStatusText');
const aiDescription = $('#aiDescription');
const pauseLabel = $('#pauseLabel');
const lastAction = $('#lastAction');
const pauseAi = $('#pauseAi');

pauseAi.addEventListener('click', () => {
  const isPaused = aiCard.classList.toggle('paused');
  aiCard.classList.remove('stopped');
  if (isPaused) {
    aiStatusText.textContent = 'Trading paused';
    aiDescription.textContent = 'The agent is standing by. Your open positions remain untouched.';
    pauseLabel.textContent = 'Resume AI';
    const pauseStripLabel = $('#globalPauseLabel');
    if (pauseStripLabel) pauseStripLabel.textContent = 'Resume AI';
    lastAction.textContent = 'Paused by you';
    showToast('AI trading paused');
  } else {
    aiStatusText.textContent = 'Trading actively';
    aiDescription.textContent = 'Scanning 18 markets for high-conviction opportunities. No action needed.';
    pauseLabel.textContent = 'Pause AI';
    const resumeStripLabel = $('#globalPauseLabel');
    if (resumeStripLabel) resumeStripLabel.textContent = 'Pause AI';
    lastAction.textContent = 'Bought BTC';
    showToast('AI trading resumed');
  }
});

$('#manageAi').addEventListener('click', () => {
  setSection('Manage');
  showToast('AI controls opened');
});

$$('[data-action]').forEach((button) => {
  button.addEventListener('click', () => {
    const action = button.dataset.action;
    if (action === 'profile') {
      setSection('Account settings');
      return;
    }
    if (action === 'edit-profile' || action === 'edit-preferences' || action === 'security') {
      showToast(`${action.replace('edit-', '').replace('-', ' ')} settings are ready to edit`);
      return;
    }
    const messages = {
      search: 'Search is ready for your portfolio',
      notifications: 'You are all caught up',
      export: 'Your report is being prepared',
      'balance-menu': 'Balance options opened',
      'ai-menu': 'AI options opened',
      positions: 'All positions are up to date',
      activity: 'Showing your latest activity',
      'row-menu': 'Position actions opened',
      support: 'Support is here when you need it',
      status: 'Status details opened'
    };
    showToast(messages[action] || 'Action complete');
  });
});


const modalBackdrop = $('#modalBackdrop');
const modalTitle = $('#modalTitle');
const modalEyebrow = $('#modalEyebrow');
const modalDescription = $('#modalDescription');
const modalBody = $('#modalBody');
const modalConfirm = $('#modalConfirm');
const modalCard = $('.modal-card');
let modalMode = 'risk';
let approvalTimerId;

function vibrate(pattern = 8) {
  if ('vibrate' in navigator) navigator.vibrate(pattern);
}

function openModal({ eyebrow = 'Control center', title, description, body, mode = 'generic', confirm = 'Save changes' }) {
  modalMode = mode;
  modalEyebrow.textContent = eyebrow;
  modalTitle.textContent = title;
  modalDescription.textContent = description;
  modalBody.innerHTML = body;
  modalConfirm.textContent = confirm;
  modalCard.classList.toggle('approval-modal', mode === 'approval');
  modalBackdrop.hidden = false;
  document.body.style.overflow = 'hidden';
  if (approvalTimerId) clearInterval(approvalTimerId);
  if (mode === 'approval') {
    let remaining = 20 * 60;
    const tick = () => {
      const timer = $('#approvalTimer');
      if (!timer) return;
      const minutes = String(Math.floor(remaining / 60)).padStart(2, '0');
      const seconds = String(remaining % 60).padStart(2, '0');
      timer.textContent = `${minutes}:${seconds}`;
      if (remaining <= 0) {
        $$('.approval-action', modalBody).forEach((button) => { button.disabled = true; button.textContent = 'Expired'; });
        clearInterval(approvalTimerId);
      }
      remaining -= 1;
    };
    tick();
    approvalTimerId = setInterval(tick, 1000);
  }
  setTimeout(() => modalConfirm.focus(), 40);
}

function closeModal() {
  modalBackdrop.hidden = true;
  document.body.style.overflow = '';
  modalCard.classList.remove('approval-modal');
  if (approvalTimerId) clearInterval(approvalTimerId);
}

function formToggle(label, detail, on = true, key = '') {
  return `<div class="form-row"><label>${label}<small>${detail}</small></label><button class="toggle ${on ? 'on' : ''}" type="button" data-toggle="${key}" aria-pressed="${on}"></button></div>`;
}

function showFeature(feature) {
  vibrate();
  const features = {
    risk: () => openModal({ eyebrow: 'Safety layer', title: 'Risk controls', description: 'Tune the boundaries your AI follows before it takes its next action.', mode: 'risk', body: `<div class="modal-form">${formToggle('Daily loss guard', 'Pause AI after a 3% portfolio drawdown', true, 'loss-guard')}${formToggle('Volatility filter', 'Skip entries when volatility is above 2.4× average', true, 'vol-filter')}${formToggle('Diversification guard', 'Keep at least 20% in cash', true, 'cash-guard')}<div class="form-row"><label>Maximum position size<small>AI will not exceed this per asset</small></label><input class="modal-input" value="$15,000" aria-label="Maximum position size" /></div></div>` }),
    approval: () => openModal({ eyebrow: 'Human in the loop', title: 'Manual approvals', description: 'Approve or decline a proposed trade before the market moves. Alerts expire after 20 minutes.', mode: 'approval', confirm: 'Close', body: `<div class="approval-queue"><div class="approval-expiry"><span>Approval expires in</span><strong id="approvalTimer">20:00</strong></div><div class="pending-trade"><div class="pending-trade-head"><span class="asset-icon">₿</span><span><strong>Buy Bitcoin</strong><small>0.04 BTC · Market order</small></span><span class="confidence-chip">87% confidence</span></div><div class="pending-trade-meta"><span>Estimated total</span><strong>$2,653.60</strong><span>Reason</span><strong>Momentum signal</strong></div><div class="approval-buttons"><button class="approve-button approval-action" type="button" data-approval="approve">Approve</button><button class="decline-button approval-action" type="button" data-approval="decline">Decline</button></div></div><div class="modal-form approval-settings">${formToggle('Auto-trade', 'Let AI place trades below the threshold automatically', true, 'auto-trade')}${formToggle('Dangerous permission', 'Allow high-volatility trades when conviction is strong', false, 'dangerous-permission')}<div class="form-row"><label>Approval threshold<small>Ask before any single trade above</small></label><input class="modal-input" value="$5,000" aria-label="Approval threshold" /></div></div></div>` }),
    wallet: () => openModal({ eyebrow: 'Connected money', title: 'Wallet management', description: 'Move money, connect accounts, or review your transfer history.', mode: 'wallet', confirm: 'Done', body: `<div class="modal-form">${formToggle('Chase Checking ·•••• 2481', 'Primary funding source', true, 'chase')}${formToggle('Coinbase wallet · 0x7A…91B2', 'Crypto settlement wallet', true, 'coinbase')}<div class="wallet-actions"><button type="button" data-control="deposit">Deposit</button><button type="button" data-control="withdraw">Withdraw</button><button type="button" data-control="transfers">Transfer history</button></div></div>` }),
    alerts: () => openModal({ eyebrow: 'Notifications', title: 'Notifications', description: 'Choose which signals deserve a nudge and where they should appear.', mode: 'alerts', confirm: 'Save notifications', body: `<div class="modal-form notifications-only">${formToggle('AI trade notifications', 'When PranavPay opens or trims a position', true, 'trade-alerts')}${formToggle('Portfolio drawdown alerts', 'When the portfolio drops 2% in a day', true, 'drawdown-alerts')}${formToggle('Unusual market activity', 'When volatility or volume moves sharply', true, 'market-alerts')}${formToggle('Low buying power warning', 'When available cash falls below $45,000', true, 'cash-alerts')}${formToggle('Weekly portfolio summary', 'Send a concise Monday morning review', true, 'weekly-alerts')}</div>` }),
    command: () => openModal({ eyebrow: 'Quick actions', title: 'Command palette', description: 'Jump directly to any control or view with a single action.', mode: 'command', confirm: 'Close', body: `<div class="palette-list"><button class="palette-item" type="button" data-control="pause">Pause or resume AI <kbd>P</kbd></button><button class="palette-item" type="button" data-control="emergency">Emergency stop all trading <kbd>Esc</kbd></button><button class="palette-item" type="button" data-control="approval">Review manual approvals <kbd>M</kbd></button><button class="palette-item" type="button" data-control="risk">Open risk controls <kbd>R</kbd></button><button class="palette-item" type="button" data-control="alerts">Open notifications <kbd>N</kbd></button><button class="palette-item" type="button" data-section="Wallet">Open wallet <kbd>W</kbd></button><button class="palette-item" type="button" data-control="timeline">Open AI decision timeline <kbd>T</kbd></button><button class="palette-item" type="button" data-control="allocation">View portfolio allocation <kbd>A</kbd></button><button class="palette-item" type="button" data-control="export">Export performance report <kbd>E</kbd></button></div>` }),
    timeline: () => openModal({ eyebrow: 'AI transparency', title: 'Why AI made this trade', description: 'A plain-language view of the signals behind the latest action.', mode: 'explain', confirm: 'Got it', body: `<div class="modal-form"><div class="strategy-summary"><span class="card-label">Latest action · 09:39</span><strong>Bought 0.04 BTC at $66,340</strong><p>Momentum accelerated above the 20-day average while your crypto allocation remained below its target range.</p></div>${formToggle('Momentum signal', 'Strong · 87% confidence', true, 'signal-momentum')}${formToggle('Portfolio fit', 'Within your balanced risk profile', true, 'signal-fit')}${formToggle('Downside check', 'Contained · 1.4% expected drawdown', true, 'signal-downside')}</div>` }),
    allocation: () => openModal({ eyebrow: 'Portfolio intelligence', title: 'Allocation details', description: 'Your capital distribution, health score, and best/worst performers at a glance.', mode: 'allocation', confirm: 'Close', body: `<div class="modal-form"><div class="health-row"><div class="health-score">86<span>/100</span></div><div><strong>Healthy portfolio</strong><small>Strong diversification with a calm cash runway</small></div></div><div class="winners-row"><div><span class="card-label">Best performer</span><strong>NVDA <em>+12.10%</em></strong></div><div><span class="card-label">Needs attention</span><strong>ETH <em>−2.18%</em></strong></div></div></div>` }),
    calendar: () => openModal({ eyebrow: 'Performance history', title: 'Profit & loss calendar', description: 'A monthly view of days where the portfolio moved with or against you.', mode: 'calendar', confirm: 'Close', body: `<div class="calendar-grid"><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span><span>S</span><i></i><i></i><i class="loss"></i><i></i><i></i><i class="muted-day"></i><i class="muted-day"></i><i></i><i></i><i></i><i class="loss"></i><i></i><i></i><i></i><i></i><i class="muted-day"></i><i></i><i></i><i></i><i class="loss"></i><i></i><i></i><i></i></div>` }),
    deposit: () => openModal({ eyebrow: 'Wallet', title: 'Deposit funds', description: 'Add buying power from your connected bank account.', mode: 'deposit', confirm: 'Review deposit', body: `<div class="modal-form"><div class="form-row"><label>From account<small>Chase Checking ·•••• 2481</small></label><strong>Connected</strong></div><div class="form-row"><label>Deposit amount<small>Available instantly after review</small></label><input class="modal-input" value="$5,000" aria-label="Deposit amount" /></div></div>` }),
    withdraw: () => openModal({ eyebrow: 'Wallet', title: 'Withdraw funds', description: 'Move available cash back to your connected bank account.', mode: 'withdraw', confirm: 'Review withdrawal', body: `<div class="modal-form"><div class="form-row"><label>To account<small>Chase Checking ·•••• 2481</small></label><strong>Connected</strong></div><div class="form-row"><label>Withdrawal amount<small>Buying power: $42,180.90</small></label><input class="modal-input" value="$1,000" aria-label="Withdrawal amount" /></div></div>` }),
    transfers: () => openModal({ eyebrow: 'Wallet', title: 'Transfer history', description: 'The latest money moving in and out of your trading wallet.', mode: 'transfers', confirm: 'Close', body: `<div class="activity-list"><div class="activity-item"><div class="activity-icon transfer-activity">↓</div><div class="activity-copy"><strong>Deposit completed</strong><span>+$5,000 from Chase •••• 2481</span></div><time>3h</time></div><div class="activity-item"><div class="activity-icon transfer-activity">↑</div><div class="activity-copy"><strong>Withdrawal completed</strong><span>−$2,000 to Chase •••• 2481</span></div><time>Sep 28</time></div></div>` }),
    weekly: () => openModal({ eyebrow: 'Portfolio review', title: 'Weekly summary', description: 'Your report is ready: portfolio value rose 3.36% this week with risk inside your comfort zone.', mode: 'weekly', confirm: 'Export report', body: `<div class="strategy-summary"><span class="card-label">Week ending Oct 02, 2026</span><strong>+$4,892.70 · 3.36%</strong><p>AI made 12 trades, kept cash above its guardrail, and improved diversification across equities and crypto.</p></div>` }),
    'alert-detail': () => openModal({ eyebrow: 'Alert detail', title: 'Low buying power', description: 'Your available buying power is $2,819 below the comfort threshold you set.', mode: 'alert-detail', confirm: 'Adjust threshold', body: `<div class="modal-form">${formToggle('Remind me tomorrow', 'Keep this alert active', true, 'remind')}${formToggle('Pause this alert', 'Do not notify me again this week', false, 'mute')}</div>` })
  };
  (features[feature] || features.risk)();
}

function engageEmergencyStop() {
  vibrate([20, 30, 20]);
  aiCard.classList.remove('paused');
  aiCard.classList.add('stopped');
  aiStatusText.textContent = 'Trading stopped';
  aiDescription.textContent = 'All new orders are blocked. Your open positions remain untouched.';
  pauseLabel.textContent = 'Resume AI';
  const globalPauseLabel = $('#globalPauseLabel');
  if (globalPauseLabel) globalPauseLabel.textContent = 'Resume AI';
  lastAction.textContent = 'Emergency stop';
  showToast('Emergency stop engaged — no new orders');
}

$$('[data-control]').forEach((button) => {
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    const action = button.dataset.control;
    if (action === 'pause') {
      pauseAi.click();
      return;
    }
    if (action === 'emergency') {
      engageEmergencyStop();
      return;
    }
    if (action === 'export') {
      const report = `PRANAVPAY PERFORMANCE REPORT\nGenerated ${new Date().toLocaleString()}\n\nPortfolio balance: $148,240.80\nToday: +$4,820.55 (3.36%)\nBuying power: $42,180.90\nPortfolio health: 86/100\nAI status: ${aiStatusText.textContent}\n\nOpen positions: AAPL, BTC, ETH, NVDA\nAI strategy: Patient momentum\n`;
      const blob = new Blob([report], { type: 'text/plain' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = 'pranavpay-performance-report.txt';
      link.click();
      URL.revokeObjectURL(link.href);
      showToast('Performance report exported');
      return;
    }
    showFeature(action);
  });
});

$$('[data-close-modal]').forEach((button) => button.addEventListener('click', closeModal));
modalBackdrop.addEventListener('click', (event) => { if (event.target === modalBackdrop) closeModal(); });
modalConfirm.addEventListener('click', () => {
  vibrate();
  if (modalMode === 'weekly') {
    closeModal();
    document.querySelector('[data-control="export"]')?.click();
    return;
  }
  if (modalMode === 'emergency') {
    closeModal();
    return;
  }
  const input = $('.modal-input', modalBody);
  closeModal();
  showToast(input ? `${input.value} saved to your controls` : 'Changes saved to PranavPay');
});

modalBody.addEventListener('click', (event) => {
  const approval = event.target.closest('[data-approval]');
  if (approval) {
    vibrate(approval.dataset.approval === 'approve' ? [8, 20, 8] : [20]);
    const action = approval.dataset.approval === 'approve' ? 'approved' : 'declined';
    closeModal();
    showToast(`BTC trade ${action}`);
    return;
  }
  const section = event.target.closest('[data-section]');
  if (section) {
    closeModal();
    setSection(section.dataset.section);
    return;
  }
  const control = event.target.closest('[data-control]');
  if (control) {
    const action = control.dataset.control;
    if (action === 'emergency') {
      closeModal();
      engageEmergencyStop();
      return;
    }
    if (action === 'pause') {
      closeModal();
      pauseAi.click();
      return;
    }
    if (action === 'export') {
      closeModal();
      document.querySelector('[data-control="export"]')?.click();
    } else {
      showFeature(action);
    }
    return;
  }
  const toggle = event.target.closest('[data-toggle]');
  if (!toggle) return;
  vibrate(6);
  const next = !toggle.classList.contains('on');
  toggle.classList.toggle('on', next);
  toggle.setAttribute('aria-pressed', String(next));
});

$$('[data-toggle]').filter((toggle) => !modalBody.contains(toggle)).forEach((toggle) => {
  toggle.addEventListener('click', () => {
    vibrate(6);
    const next = !toggle.classList.contains('on');
    toggle.classList.toggle('on', next);
    toggle.setAttribute('aria-pressed', String(next));
    showToast(next ? 'Setting enabled' : 'Setting disabled');
  });
});

$$('.filter-chip').forEach((chip) => chip.addEventListener('click', () => {
  $$('.filter-chip').forEach((item) => item.classList.toggle('active', item === chip));
  showToast(`${chip.textContent.trim()} activity filter selected`);
}));

document.addEventListener('pointerdown', (event) => {
  const interactive = event.target.closest('button, .surface-card, .activity-item, .connected-row, .transfer-line, .ledger-row');
  if (!interactive || interactive.matches(':disabled')) return;
  interactive.classList.add('motion-target', 'ripple-host');
  interactive.classList.add('is-pressed');
  setTimeout(() => interactive.classList.remove('is-pressed'), 180);
  const rect = interactive.getBoundingClientRect();
  const ripple = document.createElement('span');
  ripple.className = 'touch-ripple';
  ripple.style.left = `${event.clientX - rect.left}px`;
  ripple.style.top = `${event.clientY - rect.top}px`;
  interactive.appendChild(ripple);
  setTimeout(() => ripple.remove(), 600);
});

const motionSelector = 'main .page-intro, main .control-strip, main .overview-grid, main .primary-grid, main .lower-grid, main .feature-grid, main .wallet-page-grid, main .wallet-ledger, main .transaction-summary, main .transaction-table-card, main .manage-grid, main .manage-bottom-row, main .account-settings-grid, main .account-lower-grid, main .page-footer';
const motionTargets = [...new Set($$(motionSelector))];
motionTargets.forEach((element, index) => {
  element.classList.add('reveal-on-scroll');
  element.style.setProperty('--reveal-delay', `${Math.min(index % 6, 5) * 55}ms`);
});

const revealObserver = 'IntersectionObserver' in window
  ? new IntersectionObserver((entries) => entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-revealed');
        revealObserver.unobserve(entry.target);
      }
    }), { threshold: .08, rootMargin: '0px 0px -8% 0px' })
  : null;
if (revealObserver) motionTargets.forEach((element) => revealObserver.observe(element));
else motionTargets.forEach((element) => element.classList.add('is-revealed'));

let scrollTicking = false;
window.addEventListener('scroll', () => {
  if (scrollTicking) return;
  scrollTicking = true;
  requestAnimationFrame(() => {
    document.body.classList.toggle('is-scrolled', window.scrollY > 10);
    scrollTicking = false;
  });
}, { passive: true });

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !modalBackdrop.hidden) closeModal();
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault();
    showFeature('command');
  }
});

document.addEventListener('click', (event) => {
  if (event.target.closest('button')) vibrate(5);
});
