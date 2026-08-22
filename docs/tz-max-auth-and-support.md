# ТЗ Voitos: вход через MAX, PIN, уведомления, ОС и пожелания

> Переписано под **реальные** сущности репозитория. Исходный промпт про Telegram/t.me **не подходит** без этой адаптации.

## 0. Вердикт по исходному ТЗ

| Исходное допущение | Реальность Voitos |
|--------------------|-------------------|
| Telegram-бот | Мессенджер **MAX** (`MaxClient`, `BotUser.max_user_id`, `chat_id`) |
| `t.me/...`, Telegram API | Deep link / open MAX bot; API `platform-api2.max.ru` |
| PIN как основной вход | Сейчас: `POST /api/v1/auth/phone/start` + `verify` → `MobileAuthToken` (OTP-заглушка, код в `debug_code`) |
| Бот только auth+notify | Сейчас бот ещё ведёт анкету, слоты заявок, пожелания/голосования, опросы |
| `/support/message` | Уже есть `GET\|POST /api/v1/me/feedback` → `FeedbackTicket` + панель `/panel/feedback/` |
| Пожелания = чат поддержки | **Пожелания** = `NeighborhoodWish` + ballot; **ОС/баги** = `FeedbackTicket` |

**Вывод:** идеи (бот = канал кода и пушей, приложение = основной UI, PIN для повторного входа, фильтр «новые» в панели) — ок. Канал, эндпоинты и модели нужно брать из текущей архитектуры, а не из Telegram-шаблона.

---

## 1. Роли сущностей (целевые)

### Бот MAX («Макс»)
- **Да:** привязка `max_user_id` + телефон (кнопка «Поделиться номером»), выдача/сброс **разового кода** в чат, системные уведомления по заявкам/сборам.
- **Сокращать со временем:** полный UI заказов/чатов в боте (дубли приложения). Переходный период: бот может оставлять критичные pending-действия, пока app-deep-links не закроют 100% сценариев.
- **Не цель v1:** переписывать бота на Telegram.

### Мобильное приложение
- Основной UI: кабинет, заявки, сборы, ОС, (позже) пожелания.
- Вход: MAX → разовый код в чат → PIN → постоянный PIN на устройстве.
- Пока MAX-флоу не готов — сохранить текущий phone OTP как **dev/fallback**.

### Веб-панель
- Заявки, мастера, группы, сборы, **обращения** `/panel/feedback/`, архив по группам.
- Фильтр «Только новые» = статус `open` у `FeedbackTicket` (не отдельная сущность «заказы»).

---

## 2. Идентичность и БД

### Существующее (использовать)
- `BotUser`: `max_user_id`, `chat_id`, `phone`, профиль, `family_payer`, …
- `MobileAuthToken`: Bearer для app, push token
- `PhoneOtpChallenge`: временные коды (можно обобщить под PIN-challenge)
- `FeedbackTicket`: bug / feedback / manager
- `NeighborhoodWish`, `WishPeriod`, `WishBallot`, `WishVote`
- `WorkRequest` + photo + статусы; уведомления через `emit_app_event` + MAX `send_message`

### Добавить (фаза PIN)
- `BotUser.pin_hash` (bcrypt/argon2) — постоянный PIN, **никогда** plaintext
- `PinChallenge` (или расширить `PhoneOtpChallenge`): `kind` ∈ `login` \| `reset` \| `change`, `code_hash`, `expires_at`, `consumed_at`, связь с `BotUser`
- Опционально `DevicePinBinding`: device_id + last_success_at (не хранить PIN на клиенте в открытом виде дольше сессии)

Слияние учёток: при первом входе из MAX с телефоном, который уже есть у `app_{phone}`, **мержить** в одного `BotUser` (сохранить реальный `max_user_id`).

---

## 3. Флоу (адаптированные)

### A. Регистрация / первый вход (App + MAX)

1. App: экран входа → крупная кнопка **«Войти через Max»** + (временно) fallback «Войти по телефону».
2. Deep link в MAX-бота (не `t.me`). Пример целевой схемы: `https://max.ru/...` / deeplink из настроек `AppSettings`.
3. Бот: запрос телефона → `phone` + `max_user_id` (+ `chat_id`).
4. Backend `POST /api/v1/auth/max/start` (вместо вымышленного `/auth/register-or-login`):
   - upsert `BotUser` по `max_user_id` / merge по `phone`;
   - сгенерировать 4-значный **разовый** код, сохранить **hash**, TTL ~10 мин;
   - отправить в MAX: «Код для входа в Voitos: ****»;
   - кнопка/ссылка **«Открыть приложение»** → `voitos://app/login` (код **не** обязательно в URL; пользователь вводит вручную или deep link `voitos://app/login?phone=...` без кода в query для безопасности).
5. App: экран «Введите код из Max» → `POST /api/v1/auth/max/verify` → `access_token`.
6. Если `pin_hash` пуст → экран «Задайте постоянный PIN (4 цифры)» → `POST /api/v1/auth/pin/set`.
7. Далее кабинет / онбординг как сейчас.

### B. Повторный вход
- Экран PIN → `POST /api/v1/auth/pin/login` `{phone|user_id, pin}` → token.
- «Забыли PIN?» → `POST /api/v1/auth/pin/reset-request` → код в MAX → verify → `pin/set`.

### C. Смена PIN (ЛК → Безопасность)
- `POST /api/v1/auth/pin/change-request` → код в MAX → ввод кода + новый PIN дважды → `pin/set`.

### D. Уведомления через MAX (уже частично есть)
Сохранять dual-channel: MAX текст + `AppNotification`/FCM + deep link `voitos://app/...`.

Примеры копирайта (привязать к **реальным** статусам `WorkRequest`):
- `offering` / поиск: «По заявке №{id} идёт поиск мастера»
- назначен: «По заявке №{id} найден мастер — откройте приложение»
- сборы: существующие события кампаний

Не выдумывать статус «открыт сбор предложений», если в модели его нет — маппить на `WorkRequestStatus`.

---

## 4. Функционал панели и ЛК (без смены мессенджера)

### A. `/panel/feedback/` — фильтр «Только новые»
- Уже есть фильтр по `status`. «Новые» = `status=open` (На рассмотрении).
- UX: быстрый пресет / select: Все · Новые (`open`) · Отвеченные (`answered`) · Закрытые (`closed`).
- Не путать с заявками мастеров (`/panel/work-requests/`).

### B. ЛК — «Пожелания» vs «Обратная связь»
Сейчас в кабинете есть **«Обратная связь»** → `FeedbackScreen` → `/me/feedback` (баг / ОС / оценка менеджера). Это **не** то же самое, что пожелания двора.

| Сценарий | Куда |
|----------|------|
| Баг, ОС, оценка менеджера | `FeedbackTicket` / `/me/feedback` (уже есть) |
| Идея для сбора/двора («пожелание») | `NeighborhoodWish` — нужен app API + экран |
| Чат поддержки «как в боте одной строкой» | Не плодить `/support/message`; либо kind в feedback, либо wish |

**Целевой экран «Оставить пожелание»:**
- `POST /api/v1/me/wishes` `{text}` → `capture_wish(...)` (тот же сервис, что бот)
- Панель/задачи как при wish из MAX
- Опционально позже: список своих wishes, участие в ballot из app

---

## 5. API (целевые имена под Voitos)

Префикс: `/api/v1/`

| Метод | Назначение |
|-------|------------|
| `POST /auth/max/start` | Bot/trusted: `max_user_id`, `phone`, `chat_id` → создать challenge, отправить код в MAX |
| `POST /auth/max/verify` | App: `phone` + `code` → `access_token`, `needs_pin_setup` |
| `POST /auth/pin/set` | Auth: установить/сменить постоянный PIN (hash) |
| `POST /auth/pin/login` | `phone` + `pin` → `access_token` |
| `POST /auth/pin/reset-request` | Auth или phone: триггер кода в MAX |
| `POST /auth/pin/change-request` | Auth: код в MAX для смены |
| `GET\|POST /me/feedback` | **Уже есть** — ОС/баги |
| `GET\|POST /me/wishes` | **Новое** — пожелания двора |
| Оставить | `auth/phone/start\|verify` как fallback/dev |

Защита `auth/max/start`: shared secret бота / только из worker с `MAX_BOT_INTERNAL_TOKEN`, не публичный произвольный вызов.

### Примеры JSON

**max/start (от бота)**
```json
// POST /api/v1/auth/max/start
{"max_user_id": "123", "phone": "89625507832", "chat_id": "456"}
// 200
{"ok": true, "expires_in": 600, "is_new_user": false}
```

**max/verify (из app)**
```json
{"phone": "89625507832", "code": "4821"}
// 200
{"access_token": "...", "bot_user_id": 1, "display_name": "...", "needs_pin_setup": true}
```

**pin/set**
```json
{"pin": "1937"}
// 200
{"ok": true}
```

**pin/login**
```json
{"phone": "89625507832", "pin": "1937"}
// 200
{"access_token": "...", "bot_user_id": 1, "display_name": "..."}
```

**me/wishes**
```json
{"text": "Нужен навес у площадки"}
// 201
{"id": 42, "topic": "other", "text": "..."}
```

---

## 6. UX

- Кнопка Max на логине: бренд MAX (не иконка Telegram), текст «Войти через Max».
- После кода в боте: кнопка «Открыть Voitos» → `voitos://app/login`.
- PIN: 4 цифры, на сервере только hash; rate-limit verify/login; блокировка после N ошибок.
- Не класть одноразовый код в query deep link без TTL и одноразовости.

---

## 7. Фазы внедрения

1. **Сейчас / быстро:** фильтр «Новые» в `/panel/feedback/`; зафиксировать это ТЗ.
2. **Пожелания в app:** `POST/GET /me/wishes` + пункт в ЛК (рядом с ОС).
3. **Auth MAX + PIN:** модели, эндпоинты, bot-команда выдачи кода, экраны login/PIN/reset; phone OTP оставить fallback.
4. **Сужение бота:** убрать дубли UI, оставить auth-коды + уведомления + временно незакрытые pending.

---

## 8. Что не делать

- Не мигрировать бота на Telegram.
- Не заводить параллельный `/support/message`, дублирующий `FeedbackTicket`.
- Не хранить PIN plaintext.
- Не ломать существующих пользователей `app_{phone}` без merge-стратегии.
