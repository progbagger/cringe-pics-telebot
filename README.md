# cringe-pics-telebot

Telegram-бот, который присылает кринжовые картинки и GIF из коллекции на Яндекс Диске. Медиа можно запросить вручную, получать по расписанию или отправлять в любой чат через inline-режим.

## Возможности

- `/start` и `/help` показывают актуальные категории, команды и клавиатуру быстрого доступа;
- кнопка категории отправляет случайное медиа из соответствующей папки;
- `/list` и `/subscriptions` позволяют подписаться на рассылки и отписаться от них. Бот отправляет случайную картинку из категории в указанное для неё время;
- inline-запрос вида `@имя_бота day` ищет категории по части названия и показывает до 50 картинок и GIF. Первый результат `🎲 Выбрать случайную картинку` сразу отправляет случайно выбранное медиа;
- полученные при ручной или автоматической отправке Telegram `file_id` сохраняются в Redis, поэтому повторная отправка не требует повторной загрузки файла с Яндекс Диска;
- Redis также защищает рассылку от повторной отправки одному пользователю в одну и ту же минуту.

Категории не зашиты в коде: их названия, время рассылки и папки с медиа хранятся в PostgreSQL. Поэтому набор категорий можно менять без изменения приложения.

Для inline-режима владелец бота должен один раз включить его через [@BotFather](https://t.me/BotFather): выполнить `/setinline`, выбрать бота и задать текст-подсказку для поля ввода.

## Технологии

- **Python 3.14** и [aiogram 3](https://docs.aiogram.dev/) — асинхронный Telegram-бот в режиме long polling;
- **aiohttp** — работа с REST API Яндекс Диска;
- **PostgreSQL**, **SQLAlchemy 2** и **asyncpg** — категории и пользовательские подписки;
- **Redis** с **hiredis** — кэш Telegram `file_id` и защита рассылок от дублирования;
- **uv** — управление зависимостями и запуск приложения;
- **Docker Compose** — запуск бота, PostgreSQL и Redis;
- **pytest**, **Ruff** и **mypy** — функциональные тесты, линтинг и проверка типов в CI.

## Развёртывание на своём сервере

### Что понадобится

- Linux-сервер с Git, Docker Engine и Docker Compose v2;
- Telegram-бот и его токен от [@BotFather](https://t.me/BotFather);
- OAuth-токен Яндекс Диска с доступом к папке приложения;
- исходящие HTTPS-подключения к Telegram Bot API и API Яндекс Диска.

Входящий HTTP-порт для бота открывать не нужно: обновления Telegram он получает через long polling. Файл `compose.yml` публикует PostgreSQL на порту `4243`, а Redis — на `12312`; ограничьте доступ к ним сетевым экраном сервера, если эти порты не нужны извне.

### 1. Получить проект

```bash
git clone https://github.com/progbagger/cringe-pics-telebot.git
cd cringe-pics-telebot
```

### 2. Настроить переменные окружения

Создайте четыре файла. Они игнорируются Git и не должны попадать в репозиторий.

`docker/bot/.env`:

```dotenv
TELEGRAM_BOT_TOKEN=<токен Telegram-бота>
```

`docker/postgres/.env`:

```dotenv
POSTGRES_USER=cringe_bot
POSTGRES_PASSWORD=<надёжный пароль PostgreSQL>
POSTGRES_DB=cringe_bot
POSTGRES_HOST=postgres
```

`docker/redis/.env`:

```dotenv
REDIS_USERNAME=cringe_bot
REDIS_PASSWORD=<надёжный пароль Redis>
REDIS_HOST=redis
```

`docker/yandex/.env`:

```dotenv
YANDEX_DISK_TOKEN=<OAuth-токен Яндекс Диска>
```

Дополнительно в `docker/bot/.env` можно задать `LOG_LEVEL_NAME` и интервал проверки рассылок `SUBSCRIPTION_BROADCAST_INTERVAL_SECONDS`. Интервал по умолчанию — 30 секунд, допустимое значение — не более 60 секунд.

### 3. Подготовить медиа на Яндекс Диске

Создайте в папке приложения на Яндекс Диске каталоги категорий и загрузите туда изображения или GIF. Для стандартного набора это:

```text
morning/
day/
evening/
night/
random/
```

Имена каталогов должны совпадать со значениями `s3_directory_path` в таблице `subscription_types` из следующего шага.

### 4. Запустить сервисы

```bash
docker compose run --rm bot uv run --isolated --no-dev --group migration alembic upgrade head
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

Миграции Alembic запускаются отдельной одноразовой командой и не входят в production-зависимости процесса бота. После запуска polling можно остановить просмотр логов сочетанием `Ctrl+C` — контейнеры продолжат работать.

### 5. Добавить категории

Пример ниже создаёт стандартные категории и задаёт время рассылок по Новосибирску (`UTC+7`). Повторный запуск команды обновит существующие записи.

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
INSERT INTO subscription_types (name, time, s3_directory_path, created_at, updated_at)
VALUES
    ('random',  '00:00:00+07', 'random',  now(), now()),
    ('morning', '08:00:00+07', 'morning', now(), now()),
    ('day',     '13:00:00+07', 'day',     now(), now()),
    ('evening', '19:00:00+07', 'evening', now(), now()),
    ('night',   '23:00:00+07', 'night',   now(), now())
ON CONFLICT (name) DO UPDATE
SET time = EXCLUDED.time,
    s3_directory_path = EXCLUDED.s3_directory_path,
    updated_at = now();
SQL
```

Название категории используется в подписи кнопки и в inline-поиске; добавлять к нему `/` не нужно. Время необходимо указывать с UTC-смещением, а путь — относительно папки приложения на Яндекс Диске. Категория `random` в этом примере является отдельной коллекцией и отдельной рассылкой; при необходимости её можно удалить или настроить как любую другую категорию.

После инициализации отправьте боту `/start`. Для проверки состояния и логов используйте:

```bash
docker compose ps
docker compose logs --tail=100 bot
```

Обновление установленной версии выполняется так:

```bash
git pull --ff-only
docker compose run --rm bot uv run --isolated --no-dev --group migration alembic upgrade head
docker compose up -d --build --remove-orphans
```

При запуске без Docker Compose сначала примените миграции командой `uv run --isolated --no-dev --group migration alembic upgrade head`, а затем запустите бота командой `uv run --no-dev bot`.

Данные PostgreSQL и виртуальное окружение бота сохраняются в Docker volumes `pg-data` и `bot-env`. Redis используется как восстанавливаемый кэш и отдельного volume не имеет.

## Автоматический деплой через GitHub Actions

Workflow **Deploy over SSH** автоматически обновляет сервер после merge pull request в `main`, если workflow **CI** успешно проверил итоговый commit и были изменены файлы приложения или production-сборки:

- `src/**`;
- `docker/**`;
- `pyproject.toml` или `uv.lock`;
- `compose.yml`.

Изменения только в документации, тестах и служебных файлах деплой не запускают. Workflow также можно запустить вручную через `workflow_dispatch`; ручной запуск не зависит от списка изменённых файлов.

В GitHub environment с именем **Deploy** нужно создать secrets:

- `DEPLOY_HOST` — адрес сервера;
- `DEPLOY_USER` — SSH-пользователь;
- `DEPLOY_SSH_KEY` — приватный SSH-ключ;
- `DEPLOY_PORT` — SSH-порт, если используется не `22`;
- `DEPLOY_PATH` — путь к checkout репозитория на сервере, если он отличается от `cringe-pics-telebot`.

Перед первым автоматическим деплоем репозиторий, `.env`-файлы, категории PostgreSQL и папки Яндекс Диска нужно подготовить вручную по инструкции выше. Workflow разворачивает конкретный commit, применяет миграции отдельным one-shot контейнером и перезапускает сервисы через Docker Compose.
