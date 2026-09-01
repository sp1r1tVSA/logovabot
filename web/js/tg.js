/**
 * web/js/tg.js
 * Telegram Mini App WebApp API Bridge & Haptic Feedback Controller.
 */

class TelegramBridge {
  constructor() {
    this.tg = window.Telegram?.WebApp || null;
    this.init();
  }

  init() {
    if (this.tg) {
      this.tg.ready();
      this.tg.expand();
      // Apply header color
      try {
        this.tg.setHeaderColor('#080a0e');
        this.tg.setBackgroundColor('#080a0e');
      } catch (e) {}
    }
  }

  getInitData() {
    if (this.tg && this.tg.initData) {
      return this.tg.initData;
    }
    // Development fallback mock
    return "mock_admin_12345";
  }

  getUser() {
    return this.tg?.initDataUnsafe?.user || null;
  }

  hapticImpact(style = 'light') {
    try {
      this.tg?.HapticFeedback?.impactOccurred(style);
    } catch (e) {}
  }

  hapticNotification(type = 'success') {
    try {
      this.tg?.HapticFeedback?.notificationOccurred(type);
    } catch (e) {}
  }

  showBackButton(onClick) {
    if (this.tg?.BackButton) {
      this.tg.BackButton.show();
      this.tg.BackButton.onClick(onClick);
    }
  }

  hideBackButton() {
    if (this.tg?.BackButton) {
      this.tg.BackButton.hide();
    }
  }

  close() {
    try {
      this.tg?.close();
    } catch (e) {}
  }
}

export const tgBridge = new TelegramBridge();
