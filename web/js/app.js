/**
 * web/js/app.js
 * Comprehensive App Controller and Event Orchestrator for Logovo.bet (v2.0).
 */

import { api } from './api.js';
import { store } from './store.js';
import { tgBridge } from './tg.js';
import { UIRenderer } from './ui.js';
import { ParticleEffects } from './effects.js';

class AppController {
  constructor() {
    this.currentTournamentTab = 'standings';
    this.livePollTimer = null;
    this.init();
  }

  async init() {
    // 1. Subscribe UI renderer to reactive store changes
    store.subscribe((state) => {
      UIRenderer.renderHeader(state.user, state.progression);
      UIRenderer.renderDivisionTabs(state.divisions, state.selectedDivisionId, 'lobby-division-tabs-container');
      UIRenderer.renderDivisionTabs(state.divisions, state.selectedDivisionId, 'tournament-division-tabs-container');
      UIRenderer.renderTourTabs(state.tours, state.selectedTour);
      UIRenderer.renderHotMatches(state.hotMatches);
      UIRenderer.renderOddsMovers(state.oddsMovers);
      UIRenderer.renderRecommendations(state.recommendations);
      UIRenderer.renderMatches(state.tours, state.selectedTour, state.marketCategoryFilter, state.searchQuery, state.matchStatusFilter, state.selectedDivisionId);
      UIRenderer.renderLiveCenter(
        state.liveMatches,
        state.selectedLiveMatchId,
        state.liveMatchDetail,
        state.liveMatchEvents,
        state.liveMatchStats,
        state.liveMatchMarkets,
        state.liveMatchIntelligence
      );
      UIRenderer.renderMatchCenter(state.matchDetail, state.matchStats, state.matchH2H, state.matchInsights, state.matchLive, state.matchMarkets, state.matchCenterSubTab);
      UIRenderer.renderTournaments(state.standings, state.results, state.topScorers, this.currentTournamentTab);
      UIRenderer.renderPredictionsHistory(state.myBets, state.myBetsFilter);
      UIRenderer.renderSavedCoupons(state.savedCoupons);
      UIRenderer.renderProfile(state.user, state.progression, state.myStats, state.achievements);
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
          const lockScreen = document.getElementById('lab-lock-screen');
          if (lockScreen) lockScreen.style.display = 'flex';
          const nav = document.querySelector('.bottom-nav');
          if (nav) nav.style.display = 'none';
          const drawer = document.getElementById('slip-drawer');
          if (drawer) drawer.style.display = 'none';
          const views = document.querySelector('.views-container');
          if (views) views.style.display = 'none';
          return;
        }

        // Fetch divisions
        try {
          const divData = await api.getDivisions();
          if (divData.status === 'ok' && divData.divisions) {
            store.setDivisions(divData.divisions);
            if (divData.divisions.length > 0 && !store.state.selectedDivisionId) {
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
          // Preload first match for Match Center
          if (toursData.tours.length > 0 && toursData.tours[0].matches?.length > 0) {
            const firstMatch = toursData.tours[0].matches[0];
            this.loadMatchCenter(firstMatch.match_id);
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

  startLivePolling() {
    this.stopLivePolling();
    this.fetchLiveMatches();
    this.livePollTimer = setInterval(() => {
      this.fetchLiveMatches();
    }, 10000);
  }

  stopLivePolling() {
    if (this.livePollTimer) {
      clearInterval(this.livePollTimer);
      this.livePollTimer = null;
    }
  }

  async fetchLiveMatches() {
    try {
      const res = await api.getLiveMatches();
      if (res.status === 'ok') {
        store.setLiveMatches(res.live_matches);
        if (store.state.selectedLiveMatchId) {
          this.loadLiveMatchDetail(store.state.selectedLiveMatchId);
        }
      }
    } catch (e) {
      console.warn("Could not fetch live matches:", e);
    }
  }

  async loadLiveMatchDetail(matchId) {
    try {
      const [detailRes, eventsRes, statsRes, mktsRes, intRes] = await Promise.all([
        api.getLiveMatch(matchId),
        api.getLiveEvents(matchId),
        api.getLiveStats(matchId),
        api.getLiveMarkets(matchId),
        api.getLiveIntelligence(matchId)
      ]);
      store.setLiveMatchData(
        matchId,
        detailRes.status === 'ok' ? detailRes.match : null,
        eventsRes.status === 'ok' ? eventsRes.events : [],
        statsRes.status === 'ok' ? statsRes : null,
        mktsRes.status === 'ok' ? mktsRes.markets : [],
        intRes.status === 'ok' ? intRes : null
      );
    } catch (e) {
      console.warn("Could not load live match detail:", e);
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
        topRes.status === 'ok' ? topRes.top_scorers : []
      );
    } catch (e) {
      console.warn("Could not load tournament data:", e);
    }
  }

  async fetchUserExtras() {
    try {
      const [statsRes, savedRes] = await Promise.all([
        api.getMyStats(),
        api.getSavedCoupons()
      ]);
      if (statsRes.status === 'ok') store.setMyStats(statsRes.stats);
      if (savedRes.status === 'ok') store.setSavedCoupons(savedRes.saved_coupons);
    } catch (e) {
      console.warn("Could not load user extras:", e);
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

    // 2d. Match Status Filter Pills
    const statusPills = document.getElementById('match-status-pills-container');
    if (statusPills) {
      statusPills.addEventListener('click', (e) => {
        const btn = e.target.closest('.category-pill');
        if (btn && btn.dataset.status) {
          statusPills.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
          btn.classList.add('active');
          store.setMatchStatusFilter(btn.dataset.status);
          tgBridge.hapticImpact('light');
        }
      });
    }

    // 3. Tour Selector Tabs
    const tourTabs = document.getElementById('tour-tabs-container');
    if (tourTabs) {
      tourTabs.addEventListener('click', (e) => {
        const btn = e.target.closest('.tour-tab-btn');
        if (btn && btn.dataset.tour) {
          const tourNum = parseInt(btn.dataset.tour);
          store.setSelectedTour(tourNum);
          tgBridge.hapticImpact('light');
        }
      });
    }

    // 4. Category Filter Pills
    const catPills = document.getElementById('category-pills-container');
    if (catPills) {
      catPills.addEventListener('click', (e) => {
        const btn = e.target.closest('.category-pill');
        if (btn && btn.dataset.cat) {
          catPills.querySelectorAll('.category-pill').forEach(p => p.classList.remove('active'));
          btn.classList.add('active');
          store.setMarketCategoryFilter(btn.dataset.cat);
          tgBridge.hapticImpact('light');
        }
      });
    }

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
        UIRenderer.renderTournaments(store.state.standings, store.state.results, store.state.topScorers, tab);
        tgBridge.hapticImpact('light');
      };

      btnStandings.addEventListener('click', () => setTab('standings', btnStandings));
      btnResults.addEventListener('click', () => setTab('results', btnResults));
      btnScorers.addEventListener('click', () => setTab('scorers', btnScorers));
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

    // 13b. Phase 6: Live Center Events
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-open-live-detail');
      if (btn && btn.dataset.matchId) {
        const mId = parseInt(btn.dataset.matchId);
        this.loadLiveMatchDetail(mId);
        tgBridge.hapticImpact('light');
      }
    });

    const btnRefreshLive = document.getElementById('btn-refresh-live');
    if (btnRefreshLive) {
      btnRefreshLive.addEventListener('click', () => {
        this.fetchLiveMatches();
        tgBridge.hapticImpact('light');
      });
    }

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

    // 17. Claim Achievement
    document.addEventListener('click', async (e) => {
      const btn = e.target.closest('.btn-claim-ach');
      if (btn && btn.dataset.achId) {
        btn.disabled = true;
        try {
          const achId = String(btn.dataset.achId).trim();
          const res = await api.claimAchievement(achId);
          if (res.status === 'ok') {
            const addedCoins = (res.reward && res.reward.coins) ? Number(res.reward.coins) : 0;
            const currentBal = Number(store.state.user?.balance || 0);
            store.setUser({ ...store.state.user, balance: currentBal + addedCoins });
            this.fetchProgressionData();
            ParticleEffects.confetti();
            tgBridge.hapticNotification('success');
            tgBridge.showAlert(res.message || "Награда успешно получена!");
          }
        } catch (err) {
          tgBridge.showAlert(err.message);
        } finally {
          btn.disabled = false;
        }
      }
    });

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
    if (viewName === 'live') {
      this.startLivePolling();
    } else {
      this.stopLivePolling();
    }

    if (viewName === 'history') {
      api.getPredictions().then(res => {
        if (res.status === 'ok') store.setMyBets(res.predictions || res.bets || []);
      }).catch(() => {});
    } else if (viewName === 'profile') {
      this.fetchUserExtras();
    } else if (viewName === 'tournaments') {
      this.fetchTournamentData();
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

// Instantiate on DOM load
window.addEventListener('DOMContentLoaded', () => {
  new AppController();
});
