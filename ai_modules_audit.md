# 🤖 Аудит и рефакторинг AI-модулей (`ai_chat.py` & `ai_recognizer.py`)

> **Роль:** Senior Python / AI Engineer  
> **Дата аудита:** 26 июля 2026 г.  
> **Объект аудита:** `ai_chat.py` (ИИ-комментатор Темшик) и `ai_recognizer.py` (AI Vision скриншотов FIFA/EA FC).

---

## 📊 1. СВОДНЫЙ АНАЛИЗ (SCORECARD)

| Критерий проверки | `ai_chat.py` | `ai_recognizer.py` | Статус / Замечание |
|---|---|---|---|
| **Актуальность Gemini моделей** | ⚠️ До рефакторинга | ⚠️ До рефакторинга | Использовались несуществующие в REST API имена `gemini-3.1-flash-lite`, создававшие лишние 404 ошибки |
| **Обработка Rate Limits (429/404)** | ✅ Отлично | ✅ Отлично | Неблокирующий фоллбек на следующую действующую модель |
| **Парсинг JSON & Markdown** | N/A | ⚠️ До рефакторинга | При возврате JSON внутри ```json ... ``` вызов `json.loads()` мог вызывать `JSONDecodeError` |
| **Логирование и f-строки** | ✅ Отлично | 🔴 До рефакторинга | В `logger.exception("... {m_name} ...")` отсутствовал `f`-префикс, имя модели выводилось буквально как `{m_name}` |
| **Async & Threading Safety** | ✅ Отлично | ✅ Отлично | Функции вызываются строго через `asyncio.to_thread`, таймауты ограничены 20–25 сек |
| **Промт зеркальных колонок EA FC** | N/A | ✅ Отлично | Промт четко описывает правило зеркальности [ Г \| А ] слева и [ А \| Г ] справа |

---

## 🔍 2. НАЙДЕННЫЕ БАГИ И РИСКИ

### 🔴 Баг 1: Отсутствие `f`-префикса в `logger.exception()` (`ai_recognizer.py:136, 139`)
* **Было:**
  ```python
  logger.exception("Gemini model '{m_name}' HTTP Error {e.code}")
  logger.exception("Gemini model '{m_name}' recognition error")
  ```
* **Проблема:** Переменные `{m_name}` и `{e.code}` не подставлялись, в логи писалась буквальная строка `Gemini model '{m_name}' recognition error`.
* **Стало:**
  ```python
  logger.exception(f"Gemini model '{m_name}' HTTP Error {e.code}")
  logger.exception(f"Gemini model '{m_name}' recognition error")
  ```

---

### ⚠️ Риск 2: `JSONDecodeError` при возврате тройных кавычек ```json (`ai_recognizer.py`)
* **Было:**
  ```python
  text_content = candidates[0]["content"]["parts"][0]["text"]
  parsed_data = json.loads(text_content)
  ```
* **Проблема:** Даже при `response_mime_type: "application/json"` Gemini Vision иногда оборачивает ответ в markdown-блок ````json { ... } ````, что вызывает падение `json.loads()`.
* **Стало:**
  ```python
  text_content = candidates[0]["content"]["parts"][0]["text"].strip()
  if text_content.startswith("```"):
      text_content = re.sub(r"^```(?:json)?\s*", "", text_content, flags=re.IGNORECASE)
      text_content = re.sub(r"\s*```$", "", text_content)
  parsed_data = json.loads(text_content)
  ```

---

### ⚠️ Риск 3: Неактуальные имена моделей `gemini-3.x`
* **Было:** Первыми в списке шли несуществующие в Gemini REST API имена `gemini-3.1-flash-lite`, вызывая гарантированный `404 Not Found` на каждом запросе.
* **Стало:** Список приведён к официальным актуальным именам API:
  `["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash-lite", "gemini-1.5-flash"]`.

---

## 🛠️ 3. РЕФАКТОРИНГ И ДЕПЛОЙ

Все найденные уязвимости и баги были полностью устранены в исходных файлах `ai_chat.py` и `ai_recognizer.py`, скомпилированы и успешно отправлены в репозиторий.
