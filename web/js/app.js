/**
 * web/js/app.js
 * Comprehensive App Controller and Event Orchestrator for Logovo.bet (v2.0).
 */

import { api } from './api.js?v=2.4.6';
import { store } from './store.js?v=2.4.6';
import { tgBridge } from './tg.js?v=2.4.6';
import { UIRenderer } from './ui.js?v=2.4.6';
import { ParticleEffects } from './effects.js?v=2.4.6';

class AppController {
  constructor() {
    this.currentTournamentTab = 'standings';
    // Сортировка таблицы: по умолчанию как её отдаёт бэкенд — по очкам, вниз.
    this.standingsSort = { key: 'points', dir: 'desc' };
    this.init();
  }

  async init() {
    // 1. Subscribe UI renderer to reactive store changes
    store.subscribe((state) => {
      UIRenderer.renderHeader(state.user, state.progression);
      UIRenderer.renderDivisionTabs(state.divisions, state.selectedDivisionId, 'lobby-division-tabs-container');
      UIRenderer.renderDivisionTabs(state.divisions, state.selectedDivisionId, 'tournament-division-tabs-container');
      UIRenderer.renderHotMatches(state.hotMatches);
      UIRenderer.renderOddsMovers(state.oddsMovers);
      UIRenderer.renderRecommendations(state.recommendations, state.searchQuery);
      UIRenderer.renderMatches(state.tours, state.marketCategoryFilter, state.searchQuery, state.selectedDivisionId);
      UIRenderer.renderMatchCenter(state.matchDetail, state.matchStats, state.matchH2H, state.matchInsights, state.matchLive, state.matchMarkets, state.matchCenterSubTab);
      UIRenderer.renderTournaments(state.standings, state.results, state.topScorers, this.currentTournamentTab, state.standingsForm, this.standingsSort);
      UIRenderer.renderPredictionsHistory(state.myBets, state.myBetsFilter);
      UIRenderer.renderSavedCoupons(state.savedCoupons);
      UIRenderer.renderProfile(state.user, state.progression, state.myStats, state.achievements);
      UIRenderer.renderMyClubView(state.myClub.overview);
      if (!state.myClub.overview || state.myClub.overview.registered) {
        UIRenderer.renderMyClubMatches(state.myClub.matches, state.myClubLoading);
        UIRenderer.renderMyClubHistory(state.myClubRecent, state.myClubLoading);
        UIRenderer.renderMyClubSquad(state.myClub.squad, state.myClubSquadMeta, state.myClubLoading);
        UIRenderer.renderMyClubSubTab(state.myClubSubTab);
      }
      UIRenderer.renderSlipDrawer(state.slip, state.stakeAmount);
    });

    // 2. Setup all DOM events
    this.bindEvents();

    // 3. Initial Data Load
    await this.loadInitialData();
  }

  showLockdownScreen() {
    const lockScreen = document.getElementById('app-lockdown-screen');
    if (lockScreen) lockScreen.style.display = 'flex';
    const nav = document.querySelector('.bottom-nav');
    if (nav) nav.style.display = 'none';
    const drawer = document.getElementById('slip-drawer');
    if (drawer) drawer.style.display = 'none';
    const views = document.querySelector('.views-container');
    if (views) views.style.display = 'none';
    const header = document.querySelector('.app-header');
    if (header) header.style.display = 'none';
  }

  async loadInitialData() {
    try {
      const data = await api.getBootstrap();
      if (data.status === 'ok') {
        store.setUser(data.user);

        if (!data.user.has_access) {
          const lockScreen = document.getElementById('access-lock-screen');
          if (lockScreen) lockScreen.style.display = 'flex';
          const nav = document.querySelector('.bottom-nav');
          if (nav) nav.style.display = 'none';
          const drawer = document.getElementById('slip-drawer');
          if (drawer) drawer.style.display = 'none';
          const views = document.querySelector('.views-container');
          if (views) views.style.display = 'none';
          return;
        }

        // Parse URL query parameters (e.g. from deep links)
        const urlParams = new URLSearchParams(window.location.search);
        const targetDivId = urlParams.get('division_id');
        const targetMatchId = urlParams.get('match_id');

        // Fetch divisions
        try {
          const divData = await api.getDivisions();
          if (divData.status === 'ok' && divData.divisions) {
            store.setDivisions(divData.divisions);
            if (targetDivId) {
              store.setSelectedDivisionId(parseInt(targetDivId));
            } else if (divData.divisions.length > 0 && !store.state.selectedDivisionId) {
              store.setSelectedDivisionId(divData.divisions[0].id);
            }
          }
        } catch (err) {
          console.warn("Could not load divisions:", err);
        }

        // Fetch markets line with division
        const toursData = await api.getTours(store.state.selectedDivisionId);
        if (toursData.status === 'ok') {
          store.setTours(toursData.tours);
          if (targetMatchId) {
            const mId = parseInt(targetMatchId);
            this.loadMatchCenter(mId);
            this.switchView('match_center');
          } else {
            // Предзагружаем Матч-Центр первым матчем открытой линии, а не первым
            // матчем первого тура — тот может быть уже сыгран.
            const [firstMatch] = UIRenderer.collectLineMatches(toursData.tours);
            if (firstMatch) this.loadMatchCenter(firstMatch.match_id);
          }
        }

        // Fetch progression, tournaments, user stats & intelligence hub
        this.fetchProgressionData();
        this.fetchTournamentData(store.state.selectedDivisionId);
        this.fetchUserExtras();
        this.fetchIntelligenceHub();
      }
    } catch (err) {
      if (err.status === 403 || err.code === 'LOGOVO_LOCKDOWN' || (err.data && err.data.error === 'LOGOVO_LOCKDOWN')) {
        this.showLockdownScreen();
        return;
      }
      console.error("Failed to bootstrap app:", err);
      const matchesContainer = document.getElementById('matches-list-container');
      if (matchesContainer) {
        matchesContainer.innerHTML = `
          <div style="text-align: center; padding: 40px 20px; color: var(--text-secondary);">
            <div style="font-size: 2.5rem; margin-bottom: 12px;">📱</div>
            <div style="font-weight: 800; font-size: 1.1rem; color: #fff; margin-bottom: 8px;">Откройте через Telegram</div>
            <div style="font-size: 0.85rem; max-width: 320px; margin: 0 auto; line-height: 1.4; color: var(--text-muted);">
              Для работы Mini App требуется авторизация Telegram WebApp. Откройте приложение через меню бота или команду /start в Telegram.
            </div>
          </div>
        `;
      }
    }
  }

  async fetchIntelligenceHub() {
    try {
      const [hotRes, moversRes, recsRes] = await Promise.all([
        api.getHotMatches(),
        api.getOddsMovers(),
        api.getRecommendations()
      ]);
      if (hotRes.status === 'ok') store.setHotMatches(hotRes.hot_matches);
      if (moversRes.status === 'ok') store.setOddsMovers(moversRes.movers);
      if (recsRes.status === 'ok') store.setRecommendations(recsRes.recommendations);
    } catch (e) {
      console.warn("Could not load intelligence hub:", e);
    }
  }

  async fetchProgressionData() {
    try {
      const res = await api.getProgression();
      if (res.status === 'ok') {
        store.setProgression(res.progression, res.streak, res.unclaimed_achievements_count);
      }
      const achRes = await api.getAchievements();
      if (achRes.status === 'ok') {
        store.setAchievements(achRes.achievements);
      }
    } catch (e) {
      console.warn("Could not load progression:", e);
    }
  }

  renderTournamentTab(tab = null) {
    UIRenderer.renderTournaments(
      store.state.standings,
      store.state.results,
      store.state.topScorers,
      tab || this.currentTournamentTab,
      store.state.standingsForm,
      this.standingsSort
    );
  }

  async fetchTournamentData(divisionId = null) {
    try {
      const targetDiv = divisionId || store.state.selectedDivisionId || 1;
      const [stRes, resRes, topRes] = await Promise.all([
        api.getStandings(targetDiv),
        api.getResults(targetDiv),
        api.getTopScorers(targetDiv)
      ]);
      store.setTournamentData(
        stRes.status === 'ok' ? stRes.standings : [],
        resRes.status === 'ok' ? resRes.results : [],
        topRes.status === 'ok' ? topRes.top_scorers : [],
        stRes.status === 'ok' ? (stRes.form || {}) : {}
      );
    } catch (e) {
      console.warn("Could not load tournament data:", e);
    }
  }

  async fetchUserExtras() {
    try {
      const [statsRes, savedRes, tourStatsRes] = await Promise.all([
        api.getMyStats(),
        api.getSavedCoupons(),
        api.getTournamentStats().catch(() => null)
      ]);
      if (statsRes.status === 'ok') store.setMyStats(statsRes.stats);
      if (savedRes.status === 'ok') store.setSavedCoupons(savedRes.saved_coupons);
      if (tourStatsRes && tourStatsRes.status === 'ok') store.setTournamentStats(tourStatsRes.tournament_stats);
    } catch (e) {
      console.warn("Could not load user extras:", e);
    }
  }

  async fetchMyClubData() {
    store.setMyClubLoading(true);
    try {
      const overviewRes = await api.getMyClubOverview();
      if (overviewRes.status === 'ok') {
        store.setMyClubOverview(overviewRes);
        // Незаявленному игроку показываем онбординг — матчи и состав не грузим.
        if (!overviewRes.registered) return;
      }

      const [matchesRes, squadRes] = await Promise.all([
        api.getMyClubMatches().catch(() => null),
        api.getMyClubSquad().catch(() => null)
      ]);

      if (matchesRes && matchesRes.status === 'ok') {
        store.setMyClubMatches(matchesRes.matches || [], matchesRes.recent || []);
      }
      if (squadRes && squadRes.status === 'ok') {
        store.setMyClubSquad(squadRes.players || [], squadRes.top_scorer, squadRes.top_assistant);
      }
    } catch (e) {
      console.warn("Could not load My Club data:", e);
      UIRenderer.renderMyClubError(e.message || "Ошибка подключения к серверу", () => this.fetchMyClubData());
    } finally {
      store.setMyClubLoading(false);
    }
  }

  async refreshMyClubMatches() {
    try {
      const res = await api.getMyClubMatches();
      if (res.status === 'ok') {
        store.setMyClubMatches(res.matches || [], res.recent || []);
      }
    } catch (e) {
      console.warn("Could not refresh My Club matches:", e);
    }
  }

  openMatchTimeModal(matchId, opponentName, currentTime) {
    const modal = document.getElementById('match-time-modal');
    if (!modal) return;

    this.pendingTimeMatchId = matchId;

    const opponentEl = document.getElementById('match-time-modal-opponent');
    if (opponentEl) opponentEl.textContent = `Соперник: ${opponentName || '—'}`;

    const errEl = document.getElementById('match-time-error');
    if (errEl) errEl.style.display = 'none';

    const dateInput = document.getElementById('match-time-date');
    const timeInput = document.getElementById('match-time-time');

    // Предзаполняем сегодняшней датой (локальной, без сдвига в UTC) и
    // ранее предложенным временем, если оно было.
    const now = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    if (dateInput && !dateInput.value) {
      dateInput.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    }
    if (timeInput) {
      const match = (currentTime || '').match(/(\d{1,2}):(\d{2})/);
      timeInput.value = match ? `${pad(match[1])}:${match[2]}` : (timeInput.value || '20:00');
    }

    modal.classList.add('active');
  }

  async submitMatchTime() {
    const matchId = this.pendingTimeMatchId;
    const errEl = document.getElementById('match-time-error');
    const dateInput = document.getElementById('match-time-date');
    const timeInput = document.getElementById('match-time-time');
    const submitBtn = document.getElementById('btn-submit-match-time');

    const showError = (msg) => {
      if (errEl) {
        errEl.textContent = msg;
        errEl.style.display = 'block';
      } else {
        tgBridge.showAlert(msg);
      }
    };

    if (!matchId) return showError('Матч не выбран.');
    if (!timeInput || !timeInput.value) return showError('Укажите время начала матча.');

    // Формат «ДД.ММ.ГГГГ ЧЧ:ММ» — тот же, что понимает бот.
    let timeStr = timeInput.value;
    if (dateInput && dateInput.value) {
      const [y, m, d] = dateInput.value.split('-');
      timeStr = `${d}.${m}.${y} ${timeInput.value}`;
    }

    if (submitBtn) submitBtn.disabled = true;
    try {
      const res = await api.proposeMatchTime(matchId, timeStr);
      if (res.status === 'ok') {
        const modal = document.getElementById('match-time-modal');
        if (modal) modal.classList.remove('active');
        tgBridge.hapticNotification('success');
        this.showSuccessModal('🗓 Время предложено', `Соперник получит предложение: ${timeStr}.`);
        await this.refreshMyClubMatches();
      }
    } catch (e) {
      showError(e.message || 'Не удалось отправить предложение времени.');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async openMatchProtocolModal(matchId) {
    const modal = document.getElementById('match-protocol-modal');
    if (!modal) return;

    UIRenderer.renderMatchProtocolModal(null);
    modal.classList.add('active');

    try {
      const res = await api.getMatchDetail(matchId);
      if (res.status === 'ok' && res.match) {
        UIRenderer.renderMatchProtocolModal(res);
      } else {
        const content = document.getElementById('match-protocol-content');
        if (content) {
          content.innerHTML = `<div style="text-align: center; padding: 24px; color: var(--color-danger);">Не удалось загрузить данные матча #${matchId}.</div>`;
        }
      }
    } catch (err) {
      const content = document.getElementById('match-protocol-content');
      if (content) {
        content.innerHTML = `<div style="text-align: center; padding: 24px; color: var(--color-danger);">${err.message || 'Ошибка сети при загрузке протокола'}</div>`;
      }
    }
  }

  async loadMatchCenter(matchId) {
    try {
      const [detailRes, statsRes, h2hRes, insRes, liveRes, mktsRes] = await Promise.all([
        api.getMatchDetail(matchId),
        api.getMatchStats(matchId),
        api.getMatchH2H(matchId),
        api.getIntelligencePreview(matchId).catch(() => api.getMatchInsights(matchId)),
        api.getMatchLive(matchId),
        api.getMatchMarkets(matchId)
      ]);

      store.setSelectedMatch(
        matchId,
        detailRes.status === 'ok' ? detailRes.match : null,
        statsRes.status === 'ok' ? statsRes : null,
        h2hRes.status === 'ok' ? h2hRes : null,
        insRes.status === 'ok' ? insRes : null,
        liveRes.status === 'ok' ? liveRes : null,
        mktsRes.status === 'ok' ? mktsRes.markets : []
      );
    } catch (e) {
      console.warn("Could not load match center:", e);
    }
  }

  bindEvents() {
    // 1. Navigation Tabs
    document.querySelectorAll('.nav-item').forEach(btn => {
      btn.addEventListener('click', () => {
        const view = btn.dataset.view;
        this.switchView(view);
      });
    });

    // 2b. Division Selector Tabs (Lobby)
    const lobbyDivTabs = document.getElementById('lobby-division-tabs-container');
    if (lobbyDivTabs) {
      lobbyDivTabs.addEventListener('click', async (e) => {
        const btn = e.target.closest('.division-tab-btn');
        if (btn && btn.dataset.divisionId) {
          const divId = parseInt(btn.dataset.divisionId);
          store.setSelectedDivisionId(divId);
          tgBridge.hapticImpact('light');
          try {
            const [toursData] = await Promise.all([
              api.getTours(divId),
              this.fetchTournamentData(divId)
            ]);
            if (toursData.status === 'ok') {
              store.setTours(toursData.tours);
            }
          } catch (err) {
            console.error("Could not reload tours for division:", err);
          }
        }
      });
    }

    // 2c. Division Selector Tabs (Tournaments Hub)
    const tourDivTabs = document.getElementById('tournament-division-tabs-container');
    if (tourDivTabs) {
      tourDivTabs.addEventListener('click', async (e) => {
        const btn = e.target.closest('.division-tab-btn');
        if (btn && btn.dataset.divisionId) {
          const divId = parseInt(btn.dataset.divisionId);
          store.setSelectedDivisionId(divId);
          tgBridge.hapticImpact('light');
          await this.fetchTournamentData(divId);
        }
      });
    }

    // 3. Фильтр по турам удалён: лобби показывает единый список открытой линии.

    // 5. Search Input
    const searchInput = document.getElementById('match-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        store.setSearchQuery(e.target.value);
      });
    }

    // 6. Quick Odds Buttons on Match Cards & Match Center
    document.addEventListener('click', (e) => {
      const oddsBtn = e.target.closest('.odd-btn, .odds-btn');
      if (oddsBtn) {
        const mId = parseInt(oddsBtn.dataset.matchId);
        const outcome = oddsBtn.dataset.outcome;
        const odd = parseFloat(oddsBtn.dataset.odd);
        const mktId = oddsBtn.dataset.marketId ? parseInt(oddsBtn.dataset.marketId) : null;
        const selId = oddsBtn.dataset.selectionId ? parseInt(oddsBtn.dataset.selectionId) : null;
        const selName = oddsBtn.dataset.selectionName || null;

        // Find match object in tours or active match detail
        let targetMatch = null;
        for (const t of store.state.tours) {
          const found = (t.matches || []).find(m => m.match_id === mId || m.id === mId);
          if (found) {
            targetMatch = found;
            break;
          }
        }
        if (!targetMatch && store.state.matchDetail && (store.state.matchDetail.id === mId || store.state.matchDetail.match_id === mId)) {
          targetMatch = store.state.matchDetail;
        }
        if (!targetMatch) {
          targetMatch = { match_id: mId, team1_name: 'Хозяева', team2_name: 'Гости', tour: 1 };
        }

        store.toggleSelection(targetMatch, outcome, odd, {
          market_id: mktId,
          selection_id: selId,
          selection_name: selName
        });
        tgBridge.hapticImpact('light');
      }
    });

    // 7. Match Center Sub-Tabs Switching
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.mc-subtab-btn');
      if (btn && btn.dataset.subtab) {
        store.setMatchCenterSubTab(btn.dataset.subtab);
        tgBridge.hapticImpact('light');
      }
    });

    // 7. Open Match Center from card
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-open-match-center');
      if (btn && btn.dataset.matchId) {
        const mId = parseInt(btn.dataset.matchId);
        this.loadMatchCenter(mId);
        this.switchView('match_center');
      }
    });

    // 8. Open All Markets Modal
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-more-markets');
      if (btn && btn.dataset.matchId) {
        const mId = parseInt(btn.dataset.matchId);
        const modal = document.getElementById('match-markets-modal');
        if (modal) {
          modal.classList.add('active');
          try {
            const data = await api.getMatchMarkets(mId);
            if (data.status === 'ok') {
              UIRenderer.renderMatchMarketsModal(mId, data.markets, `${data.team1_name} — ${data.team2_name}`);
            }
          } catch (err) {
            console.error("Could not load markets:", err);
          }
        }
      }
    });

    // 9. Tournament Sub-tabs
    const btnStandings = document.getElementById('btn-tab-standings');
    const btnResults = document.getElementById('btn-tab-results');
    const btnScorers = document.getElementById('btn-tab-scorers');

    if (btnStandings && btnResults && btnScorers) {
      const setTab = (tab, activeBtn) => {
        this.currentTournamentTab = tab;
        [btnStandings, btnResults, btnScorers].forEach(b => b.classList.remove('active'));
        activeBtn.classList.add('active');
        this.renderTournamentTab(tab);
        tgBridge.hapticImpact('light');
      };

      btnStandings.addEventListener('click', () => setTab('standings', btnStandings));
      btnResults.addEventListener('click', () => setTab('results', btnResults));
      btnScorers.addEventListener('click', () => setTab('scorers', btnScorers));
    }

    // 9b. Сортировка таблицы: делегированный клик по шапке (она перерисовывается)
    const tournamentsContainer = document.getElementById('tournaments-content-container');
    if (tournamentsContainer) {
      tournamentsContainer.addEventListener('click', (e) => {
        const th = e.target.closest('th[data-sort-key]');
        if (!th) return;
        const key = th.dataset.sortKey;
        if (this.standingsSort.key === key) {
          this.standingsSort.dir = this.standingsSort.dir === 'desc' ? 'asc' : 'desc';
        } else {
          // Клуб сортируем по алфавиту, числовые колонки — сразу от большего.
          this.standingsSort = { key, dir: key === 'team' ? 'asc' : 'desc' };
        }
        this.renderTournamentTab(this.currentTournamentTab);
        tgBridge.hapticImpact('light');
      });
    }

    // 10. History Filter Chips
    const historyFilters = document.getElementById('history-filter-pills');
    if (historyFilters) {
      historyFilters.addEventListener('click', (e) => {
        const btn = e.target.closest('.category-pill');
        if (btn && btn.dataset.filter) {
          historyFilters.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
          btn.classList.add('active');
          store.setMyBets(store.state.myBets, btn.dataset.filter);
          tgBridge.hapticImpact('light');
        }
      });
    }

    // 11. Repeat Prediction Button
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-repeat-bet');
      if (btn && btn.dataset.betId) {
        try {
          const res = await api.repeatPrediction(parseInt(btn.dataset.betId));
          if (res.status === 'ok') {
            store.loadCouponSelections(res.selections);
            store.setStakeAmount(res.amount);
            this.toggleSlipDrawer(true);
            this.showSuccessModal('🔄 Прогноз скопирован!', res.message);
          }
        } catch (err) {
          tgBridge.showAlert(err.message);
        }
      }
    });

    // 12. Save Draft Coupon
    const saveSlipBtn = document.getElementById('btn-save-draft-slip');
    if (saveSlipBtn) {
      saveSlipBtn.addEventListener('click', async () => {
        if (store.state.slip.length === 0) {
          tgBridge.showAlert("Купон пуст. Выберите хотя бы один исход.");
          return;
        }
        try {
          const res = await api.saveCoupon(
            `Экспресс (${store.state.slip.length})`,
            store.state.slip,
            store.getTotalOdd()
          );
          if (res.status === 'ok') {
            this.fetchUserExtras();
            this.showSuccessModal('💾 Черновик сохранен', res.message);
          }
        } catch (err) {
          tgBridge.showAlert(err.message);
        }
      });
    }

    // 13. Restore / Delete Saved Coupon
    document.addEventListener('click', async (e) => {
      const restBtn = e.target.closest('.btn-restore-coupon');
      if (restBtn && restBtn.dataset.savedId) {
        const sId = parseInt(restBtn.dataset.savedId);
        const matchSaved = store.state.savedCoupons.find(s => s.id === sId);
        if (matchSaved && matchSaved.selections) {
          store.loadCouponSelections(matchSaved.selections);
          this.toggleSlipDrawer(true);
        }
      }

      const delBtn = e.target.closest('.btn-delete-saved-coupon');
      if (delBtn && delBtn.dataset.savedId) {
        try {
          await api.deleteSavedCoupon(parseInt(delBtn.dataset.savedId));
          this.fetchUserExtras();
        } catch (err) {
          console.warn("Delete saved coupon error:", err);
        }
      }
    });

    // 13b. LIVE-центр удалён из мини-приложения — обработчиков нет.
    // Лайв-данные конкретного матча по-прежнему доступны во вкладке Матч-Центра.

    // 14. Bet Slip Drawer Controls
    const slipBar = document.getElementById('slip-bar-collapsed');
    if (slipBar) {
      slipBar.addEventListener('click', () => {
        this.toggleSlipDrawer();
      });
    }

    const clearSlipBtn = document.getElementById('btn-clear-slip');
    if (clearSlipBtn) {
      clearSlipBtn.addEventListener('click', () => {
        store.clearSlip();
      });
    }

    document.addEventListener('click', (e) => {
      const rmBtn = e.target.closest('.btn-remove-slip-item');
      if (rmBtn && rmBtn.dataset.matchId) {
        store.removeSelection(parseInt(rmBtn.dataset.matchId));
      }
    });

    // Stake quick chips
    document.querySelectorAll('.stake-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.stake-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        const val = chip.dataset.amount;
        if (val === 'all') {
          store.setStakeAmount(store.state.user?.balance || 100);
        } else {
          store.setStakeAmount(parseInt(val));
        }
        const input = document.getElementById('stake-input');
        if (input) input.value = store.state.stakeAmount;
        tgBridge.hapticImpact('light');
      });
    });

    const stakeInput = document.getElementById('stake-input');
    if (stakeInput) {
      stakeInput.addEventListener('input', (e) => {
        store.setStakeAmount(parseInt(e.target.value) || 0);
      });
    }

    // Submit Prediction CTA
    const submitBtn = document.getElementById('btn-submit-prediction');
    if (submitBtn) {
      submitBtn.addEventListener('click', async () => {
        if (store.state.slip.length === 0) {
          tgBridge.showAlert("Добавьте хотя бы одно событие в купон.");
          return;
        }

        const amt = store.state.stakeAmount;
        if (amt < 10) {
          tgBridge.showAlert("Минимальная сумма ставки — 10 🪙.");
          return;
        }

        if ((store.state.user?.balance || 0) < amt) {
          tgBridge.showAlert("Недостаточно монет на балансе.");
          return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = '⏳ ОБРАБОТКА...';

        try {
          const idempotencyKey = `slip-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
          const res = await api.placePrediction(amt, store.state.slip, idempotencyKey);
          if (res.status === 'ok') {
            store.setUser({ ...store.state.user, balance: res.new_balance });
            store.clearSlip();
            this.toggleSlipDrawer(false);
            ParticleEffects.confetti();
            tgBridge.hapticNotification('success');
            this.showSuccessModal('🎉 Прогноз принят!', `Сумма: ${amt} 🪙. Удачи в туре!`);
            this.fetchUserExtras();
            // Refresh predictions history immediately
            try {
              const myBetsRes = await api.getPredictions();
              if (myBetsRes.status === 'ok') store.setMyBets(myBetsRes.predictions);
            } catch (e) {
              console.warn("Could not refresh predictions:", e);
            }
          }
        } catch (err) {
          if (err.data && err.data.error === 'ODDS_CHANGED') {
            const { old_odd, new_odd, match_id, outcome } = err.data;
            UIRenderer.showOddsChangedModal(
              old_odd,
              new_odd,
              () => {
                // User accepted new odds
                const item = store.state.slip.find(s => s.match_id === match_id && s.outcome === outcome);
                if (item) {
                  item.odd = parseFloat(new_odd);
                  store.notify();
                }
                tgBridge.hapticImpact('medium');
                // Allow UI to re-enable before triggering re-submission
                setTimeout(() => {
                  submitBtn.click();
                }, 100);
              },
              () => {
                tgBridge.hapticImpact('light');
              }
            );
            return;
          }
          tgBridge.showAlert(err.message);
        } finally {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Сделать прогноз';
        }
      });
    }

    // 15. Modals close triggers
    document.querySelectorAll('.modal-overlay').forEach(modal => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.closest('.btn-modal-close')) {
          modal.classList.remove('active');
        }
      });
    });

    // 16. Leaderboard Modal Trigger
    const btnLdr = document.getElementById('btn-toggle-leaderboard-modal');
    if (btnLdr) {
      btnLdr.addEventListener('click', async () => {
        const modal = document.getElementById('leaderboard-modal');
        if (modal) {
          modal.classList.add('active');
          try {
            const data = await api.getLeaderboard();
            if (data.status === 'ok') {
              UIRenderer.renderLeaderboardModal(data.leaderboard, data.my_rank);
            }
          } catch (err) {
            console.warn("Could not load leaderboard:", err);
          }
        }
      });
    }

    // 17. Achievement rewards removed — достижения теперь чисто статусные,
    // монеты и XP за них не выдаются, поэтому обработчика получения награды нет.

    // 18. Early Cashout Settlement
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-cashout');
      if (btn && btn.dataset.betId) {
        const betId = parseInt(btn.dataset.betId);
        btn.disabled = true;
        try {
          const quoteRes = await api.getCashoutQuote(betId);
          if (quoteRes.status !== 'ok' || !quoteRes.cashout_available) {
            tgBridge.showAlert(quoteRes.message || "Кэшаут в данный момент недоступен для этого прогноза.");
            return;
          }
          const quoteAmount = quoteRes.amount;
          tgBridge.showConfirm(
            `💰 Досрочный расчет (Cashout)\n\nВы получите ${quoteAmount} 🪙 немедленно. Завершить ставку?`,
            async (confirmed) => {
              if (!confirmed) return;
              try {
                const idempotencyKey = `co-${betId}-${Date.now()}`;
                const execRes = await api.executeCashout(betId, idempotencyKey);
                if (execRes.status === 'ok') {
                  const newBal = execRes.new_balance;
                  store.setUser({ ...store.state.user, balance: newBal });
                  tgBridge.hapticNotification('success');
                  this.showSuccessModal('💰 Кэшаут выполнен!', `Зачислено: +${execRes.payout} 🪙.`);
                  try {
                    const myBetsRes = await api.getPredictions();
                    if (myBetsRes.status === 'ok') store.setMyBets(myBetsRes.predictions);
                  } catch (err2) {
                    console.warn("Could not refresh predictions after cashout:", err2);
                  }
                  this.fetchUserExtras();
                }
              } catch (execErr) {
                tgBridge.showAlert(execErr.message || "Не удалось выполнить кэшаут.");
              }
            }
          );
        } catch (err) {
          tgBridge.showAlert(err.message || "Ошибка получения котировки кэшаута.");
        } finally {
          btn.disabled = false;
        }
      }
    });

    // 18b. My Club — внутренние под-вкладки
    document.querySelectorAll('#my-club-subtabs .mc-subtab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        store.setMyClubSubTab(btn.dataset.clubTab);
        tgBridge.hapticImpact('light');
      });
    });

    // 18c. My Club — согласование времени матча
    document.addEventListener('click', async (e) => {
      const proposeBtn = e.target.closest('.btn-propose-time');
      if (proposeBtn) {
        this.openMatchTimeModal(
          parseInt(proposeBtn.dataset.matchId),
          proposeBtn.dataset.opponent,
          proposeBtn.dataset.currentTime
        );
        tgBridge.hapticImpact('light');
        return;
      }

      const acceptBtn = e.target.closest('.btn-accept-time');
      if (acceptBtn) {
        const matchId = parseInt(acceptBtn.dataset.matchId);
        acceptBtn.disabled = true;
        try {
          const res = await api.acceptMatchTime(matchId);
          if (res.status === 'ok') {
            tgBridge.hapticNotification('success');
            this.showSuccessModal('✅ Время согласовано', `Матч назначен на ${res.proposed_time || 'согласованное время'}.`);
            await this.refreshMyClubMatches();
          }
        } catch (err) {
          tgBridge.showAlert(err.message || 'Не удалось подтвердить время матча.');
        } finally {
          acceptBtn.disabled = false;
        }
      }

      const protocolBtn = e.target.closest('.btn-view-match-protocol') || e.target.closest('.club-match-card.clickable');
      if (protocolBtn && protocolBtn.dataset.matchId) {
        const matchId = parseInt(protocolBtn.dataset.matchId);
        if (matchId) {
          this.openMatchProtocolModal(matchId);
          tgBridge.hapticImpact('light');
          return;
        }
      }
    });

    // 18d. My Club — пресеты и отправка в модалке выбора времени
    document.querySelectorAll('#match-time-presets .time-preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const timeInput = document.getElementById('match-time-time');
        if (timeInput) timeInput.value = btn.dataset.time;
        document.querySelectorAll('#match-time-presets .time-preset-btn')
          .forEach(b => b.classList.toggle('active', b === btn));
      });
    });

    const btnSubmitTime = document.getElementById('btn-submit-match-time');
    if (btnSubmitTime) {
      btnSubmitTime.addEventListener('click', () => this.submitMatchTime());
    }

    // 19. Close Locked App screen
    const btnCloseLocked = document.getElementById('btn-close-locked-app');
    if (btnCloseLocked) {
      btnCloseLocked.addEventListener('click', () => {
        tgBridge.close();
      });
    }

    // 20. Close Global Lockdown App screen
    const btnCloseLockdown = document.getElementById('btn-close-lockdown-app');
    if (btnCloseLockdown) {
      btnCloseLockdown.addEventListener('click', () => {
        try {
          tgBridge.close();
        } catch (e) {
          window.close();
        }
      });
    }
  }

  toggleSlipDrawer(forceOpen = null) {
    const drawer = document.getElementById('slip-drawer');
    const label = document.getElementById('slip-toggle-label');
    if (!drawer) return;

    if (forceOpen !== null) {
      if (forceOpen) drawer.classList.add('expanded');
      else drawer.classList.remove('expanded');
    } else {
      drawer.classList.toggle('expanded');
    }

    if (label) {
      label.textContent = drawer.classList.contains('expanded') ? 'Свернуть' : 'Открыть';
    }
  }

  switchView(viewName) {
    store.setActiveView(viewName);

    // Update bottom nav
    document.querySelectorAll('.bottom-nav .nav-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.view === viewName);
    });

    // Update views container
    document.querySelectorAll('.view-section').forEach(sec => {
      sec.classList.toggle('active', sec.id === `view-${viewName}`);
    });

    // On-demand view refresh
    if (viewName === 'history') {
      api.getPredictions().then(res => {
        if (res.status === 'ok') store.setMyBets(res.predictions || res.bets || []);
      }).catch(() => {});
    } else if (viewName === 'profile') {
      this.fetchUserExtras();
    } else if (viewName === 'tournaments') {
      this.fetchTournamentData();
    } else if (viewName === 'my_club') {
      this.fetchMyClubData();
    }

    tgBridge.hapticImpact('light');
  }

  showSuccessModal(title, desc) {
    const modal = document.getElementById('general-success-modal');
    const titleEl = document.getElementById('success-modal-title');
    const descEl = document.getElementById('success-modal-desc');
    if (modal) {
      if (titleEl) titleEl.textContent = title;
      if (descEl) descEl.textContent = desc;
      modal.classList.add('active');
    }
  }
}

// Instantiate on DOM ready
if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', () => {
    new AppController();
  });
} else {
  new AppController();
}
