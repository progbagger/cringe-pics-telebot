# cringe-pics-telebot

Telegram-бот, который отправляет картинки из тематических папок на Яндекс Диске: по команде пользователя, по расписанию или через inline-режим в любом чате.

## Возможности

- `/start` и `/help` показывают справку и клавиатуру с доступными категориями.
- Кнопка нужной категории отправляет случайную картинку из соответствующей папки на Яндекс Диске.
- `/list`, `/subscriptions` или кнопка «Подписки» открывают управление рассылками. Для каждой категории можно отдельно подписаться или отписаться.
- Подписчики автоматически получают случайную картинку в заданное для категории время. Повторная отправка одному пользователю в ту же минуту блокируется через Redis.
- В inline-режиме запрос вида `@имя_бота day` позволяет выбрать картинку нужной категории и отправить её в любой чат.
- Поддерживаются изображения и GIF-анимации. После первой отправки Telegram `file_id` сохраняется в Redis и повторно используется в течение семи дней.

Категории, их расписание и соответствующие папки задаются в таблице `subscription_types`, поэтому список не зашит в коде и может быть изменён без пересборки приложения.

Чтобы включить inline-режим, владелец бота должен один раз выполнить `/setinline` через [@BotFather](https://t.me/BotFather), выбрать бота и задать текст-подсказку.

## Стек

- **Python 3.14** и [uv](https://docs.astral.sh/uv/) — окружение, зависимости и запуск приложения;
- **aiogram 3** — асинхронная работа с Telegram Bot API в режиме long polling;
- **aiohttp** — обращения к API Яндекс Диска;
- **PostgreSQL**, **SQLAlchemy 2** и **asyncpg** — пользователи, категории и подписки;
- **Redis** — кеш Telegram `file_id` и защита рассылок от дублей;
- **Docker Compose** — запуск бота и инфраструктуры;
- **pytest**, **pytest-asyncio**, **Ruff** и **mypy** — тестирование и статические проверки;
- **GitHub Actions** — CI, автоматическое и ручное развёртывание по SSH.

## Развёртывание через Docker Compose

### 1. Подготовка

На сервере нужны Git, Docker Engine и Docker Compose v2. Также потребуются:

- токен Telegram-бота от [@BotFather](https://t.me/BotFather);
- OAuth-токен Яндекс Диска с доступом к папке приложения;
- отдельные папки с изображениями для каждой категории внутри папки приложения на Яндекс Диске.

Клонируйте репозиторий и перейдите в него:

```bash
git clone https://github.com/progbagger/cringe-pics-telebot.git
cd cringe-pics-telebot
```

### 2. Переменные окружения

Создайте четыре файла. Они игнорируются Git и не должны попадать в репозиторий.

`docker/bot/.env`:

```dotenv
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
LOG_LEVEL_NAME=INFO
```

`docker/yandex/.env`:

```dotenv
YANDEX_DISK_TOKEN=<yandex-disk-oauth-token>
```

`docker/postgres/.env`:

```dotenv
POSTGRES_USER=cringe_pics_telebot
POSTGRES_PASSWORD=<strong-postgres-password>
POSTGRES_DB=cringe_pics_telebot
POSTGRES_HOST=postgres
```

`docker/redis/.env`:

```dotenv
REDIS_USERNAME=cringe_pics_telebot
REDIS_PASSWORD=<strong-redis-password>
REDIS_HOST=redis
```

Опционально в `docker/bot/.env` можно задать `SUBSCRIPTION_BROADCAST_INTERVAL_SECONDS` — интервал проверки расписания больше 0 и не больше 60 секунд. По умолчанию используется 30 секунд.

### 3. Запуск и настройка категорий

Запустите сервисы:

```bash
docker compose up -d --build
docker compose ps
```

При первом запуске бот создаст таблицы PostgreSQL. После этого добавьте категории и расписание, например для часового пояса Новосибирска:

```bash
docker compose exec postgres psql -U cringe_pics_telebot -d cringe_pics_telebot
```

```sql
INSERT INTO subscription_types
    (name, time, s3_directory_path, created_at, updated_at)
VALUES
    ('/random',  '00:00:00+07', 'random',  CURRENT_TIME, CURRENT_TIME),
    ('/morning', '08:00:00+07', 'morning', CURRENT_TIME, CURRENT_TIME),
    ('/day',     '13:00:00+07', 'day',     CURRENT_TIME, CURRENT_TIME),
    ('/evening', '19:00:00+07', 'evening', CURRENT_TIME, CURRENT_TIME),
    ('/night',   '23:00:00+07', 'night',   CURRENT_TIME, CURRENT_TIME)
ON CONFLICT (name) DO UPDATE SET
    time = EXCLUDED.time,
    s3_directory_path = EXCLUDED.s3_directory_path,
    updated_at = CURRENT_TIME;
```

Значение `s3_directory_path` должно совпадать с именем папки внутри папки приложения на Яндекс Диске. В папках должны находиться файлы с MIME-типом `image/*`.

Проверьте журнал бота:

```bash
docker compose logs -f bot
```

Контейнеры настроены на автоматический перезапуск. Данные PostgreSQL сохраняются в Docker volume `pg-data`.

> `compose.yml` публикует PostgreSQL на порту `4243`, а Redis — на `12312`. На публичном сервере закройте эти порты межсетевым экраном либо ограничьте их привязку локальным интерфейсом.

## Обновление

Для ручного обновления checkout на сервере:

```bash
git pull --ff-only origin main
docker compose up -d --build --remove-orphans --force-recreate
docker compose ps
```

## Деплой через GitHub Actions

Workflow **Deploy over SSH** запускается:

- автоматически после успешного CI для смерженного в `main` pull request, если он изменяет `src/**`, `docker/**`, `pyproject.toml`, `uv.lock` или `compose.yml`;
- вручную через `workflow_dispatch`.

Прямые push-события, незавершённый или неуспешный CI и изменения только в документации, тестах или GitHub Actions не запускают автоматический деплой.

Создайте GitHub Environment с именем `Deploy` и добавьте в него secrets:

- `DEPLOY_HOST` — адрес сервера;
- `DEPLOY_USER` — SSH-пользователь;
- `DEPLOY_SSH_KEY` — приватный SSH-ключ;
- `DEPLOY_PORT` — SSH-порт, если используется не `22`;
- `DEPLOY_PATH` — путь к checkout на сервере, если он отличается от `cringe-pics-telebot`.

На сервере заранее должны быть выполнены шаги первоначального развёртывания, а у SSH-пользователя должны быть права на работу с Docker. Workflow синхронизирует серверный checkout с `origin/main` и перезапускает Compose-сервисы, поэтому локальные изменения в этом checkout будут удалены.

## Проверки

```bash
uv sync
uv run ruff check
uv run mypy .
uv run pytest tests/units -q
uv run pytest tests/functional -q
```

Функциональные тесты поднимают PostgreSQL и Redis через Docker Compose, поэтому для их запуска нужен Docker.
