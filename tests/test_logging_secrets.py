"""
tests/test_logging_secrets.py

Токен бота не должен попадать в логи.

httpx логирует на INFO полный URL запроса, а токен — часть пути Telegram API
(`https://api.telegram.org/bot<TOKEN>/getUpdates`). При корневом уровне INFO это
означало бы токен открытым текстом в journalctl в каждой строке опроса.

Инвариант: импорт main поднимает порог httpx/httpcore до WARNING, и запись
уровня INFO от этих логгеров не доходит до обработчиков.
"""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: F401  — импорт и есть проверяемое действие


class TestNoisyClientLoggersAreMuted(unittest.TestCase):

    def test_http_client_loggers_are_raised_to_warning(self):
        for name in ("httpx", "httpcore"):
            with self.subTest(logger=name):
                self.assertGreaterEqual(
                    logging.getLogger(name).level, logging.WARNING,
                    f"{name} на INFO пишет полные URL запросов вместе с токеном бота"
                )

    def test_info_record_with_a_url_is_not_emitted(self):
        """Проверка поведением: строка вида httpx-INFO не проходит фильтр уровня."""
        httpx_logger = logging.getLogger("httpx")
        self.assertFalse(httpx_logger.isEnabledFor(logging.INFO))
        # Ошибки при этом по-прежнему видно.
        self.assertTrue(httpx_logger.isEnabledFor(logging.WARNING))


if __name__ == "__main__":
    unittest.main()
